#!/usr/bin/env python3
"""Run `palette.py build-deck` without blocking, for hosts that cap a step.

`build-deck` renders every slide and repairs geometry, which takes three to ten
minutes. Some agent hosts allow that in one call — Claude Code's Bash tool
permits ten. Others do not: CUGA kills a sandbox step at 120 seconds, so a
direct call is killed part-way and the agent learns nothing about how far it
got. Worse, the build keeps running server-side, so the work is done and
thrown away.

So: start the build detached, then poll.

    python scripts/deck.py start  --plan plan.md --out-dir ./deck
    python scripts/deck.py status --out-dir ./deck     # repeat until done

Both print one JSON object. `status` reports `done` only when the .pptx is on
disk and large enough to be real — never because the process said so. A build
that exits 0 having written nothing is a failure, and an agent relaying "done"
from an exit code would report a deck that does not exist.

Stdlib only, so it runs wherever the agent does.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

#: Written into --out-dir so `status` can find the build across separate calls.
#: Each poll is its own process; nothing is held in memory between them.
STATE = ".palette-build.json"

#: Below this a .pptx is a stub, not a deck. A failed render can still leave a
#: small well-formed file behind, and reporting that as success is the exact
#: failure this script exists to prevent.
MIN_PPTX_BYTES = 20_000


def palette_home() -> Path:
    """The Palette checkout. $PALETTE_HOME, else walk up from this script."""
    env = os.environ.get("PALETTE_HOME", "").strip()
    if env:
        home = Path(env).expanduser()
        if not (home / "palette.py").is_file():
            raise SystemExit(f"error: $PALETTE_HOME={home} has no palette.py")
        return home
    # skills/palette/scripts/deck.py -> repo root is three levels up. Holds for
    # the in-repo copy; an installed skill elsewhere must set $PALETTE_HOME.
    here = Path(__file__).resolve()
    for candidate in list(here.parents)[:5]:
        if (candidate / "palette.py").is_file():
            return candidate
    raise SystemExit(
        "error: cannot find the Palette checkout. Set PALETTE_HOME to it, e.g.\n"
        "  export PALETTE_HOME=~/code/project-palette"
    )


def interpreter(home: Path) -> str:
    """Palette's own venv if it has one — it holds the pipeline's dependencies.

    Falling back to whatever `python3` is on PATH is right for a system-wide
    install and wrong for a checkout with a venv, so prefer the venv.
    """
    venv = home / ".venv" / "bin" / "python"
    return str(venv) if venv.is_file() else sys.executable


def verify(out_dir: Path) -> dict:
    """Stat what is on disk. The only thing allowed to say a deck exists."""
    pptx = out_dir / "deck.pptx"
    size = pptx.stat().st_size if pptx.is_file() else 0
    previews = sorted(p.name for p in out_dir.glob("*.png"))
    return {
        "pptx": str(pptx.resolve()) if pptx.is_file() else None,
        "pptx_bytes": size,
        "slide_previews": len(previews),
        "verified": bool(pptx.is_file() and size >= MIN_PPTX_BYTES),
    }


def _alive(pid: int) -> bool:
    """Is *our* build still running under this pid?

    `status` now waits on this before it will call a build done, so a pid that
    has been recycled onto an unrelated process would poll forever. Checking
    the command line as well costs one `ps` and rules that out. If `ps` is
    missing or shaped differently, fall back to the signal test rather than
    calling a live build dead -- the wrong answer in that direction is worse.
    """
    if pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    try:
        listing = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    return "palette.py" in listing.stdout if listing.returncode == 0 else True


def _run_palette(home: Path, argv: list[str]) -> subprocess.CompletedProcess:
    """Run palette.py from the checkout, whatever the caller's cwd is.

    palette.py imports config/pipeline by relative import, so it only runs with
    cwd set to the checkout — but the agent's cwd is its own workspace, and
    that is where output has to land. Getting this wrong is not hypothetical:
    an agent told to `cd $PALETTE_HOME` and then run `palette.py` instead ran
    `skills/palette/palette.py`, mixing the skill folder with the checkout.
    So no caller ever has to think about it: paths in, paths out, cwd handled.
    """
    return subprocess.run(
        [interpreter(home), "-u", "palette.py", *argv],
        cwd=str(home), capture_output=True, text=True,
    )


def _relay(result: subprocess.CompletedProcess) -> int:
    """palette.py's own stdout/stderr, verbatim. Its error text is the reason."""
    if result.stdout:
        print(result.stdout.rstrip())
    if result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip().splitlines()
        print(json.dumps({"ok": False, "error": message[-1] if message else "failed"}))
    return result.returncode


def plan(args: argparse.Namespace) -> int:
    """build-plan, with --out resolved against *your* cwd rather than the checkout."""
    home = palette_home()
    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = ["build-plan", args.request, "--out", str(out)]
    if args.context:
        argv += ["--context", args.context]
    for source in args.source or []:
        argv += ["--source", str(Path(source).expanduser().resolve())]
    return _relay(_run_palette(home, argv))


def edit(args: argparse.Namespace) -> int:
    """edit-plan, reading and writing the same file by absolute path."""
    home = palette_home()
    plan_path = Path(args.plan).expanduser().resolve()
    if not plan_path.is_file():
        raise SystemExit(f"error: no plan at {plan_path}")
    out = Path(args.out).expanduser().resolve() if args.out else plan_path
    return _relay(_run_palette(
        home, ["edit-plan", args.instruction, "--plan", str(plan_path), "--out", str(out)]
    ))


def start(args: argparse.Namespace) -> int:
    home = palette_home()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = Path(args.plan).expanduser().resolve()
    if not plan.is_file():
        raise SystemExit(f"error: no plan at {plan}")

    state_path = out_dir / STATE
    previous = _load(state_path)
    if previous and _alive(previous.get("pid", -1)):
        print(json.dumps({**previous, "note": "already building; poll with status"}))
        return 0

    log = out_dir / "build.log"
    cmd = [
        interpreter(home), "-u", "palette.py", "build-deck",
        "--plan", str(plan), "--out-dir", str(out_dir), "--json",
    ]
    if args.palette_family:
        cmd += ["--palette-family", args.palette_family]

    with log.open("wb") as handle:
        process = subprocess.Popen(
            cmd,
            cwd=str(home),            # palette.py imports config/pipeline from here
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,   # outlive the step that started it
        )

    state = {
        "state": "running",
        "pid": process.pid,
        "plan": str(plan),
        "out_dir": str(out_dir),
        "log": str(log),
        "started_at": int(time.time()),
    }
    state_path.write_text(json.dumps(state, indent=2))
    print(json.dumps({
        **state,
        "note": "building — takes 3-10 minutes; poll with `deck.py status`",
        "next": f"python {Path(__file__).name} status --out-dir {out_dir}",
    }))
    return 0


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def status(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir).expanduser().resolve()
    state = _load(out_dir / STATE)
    if not state:
        raise SystemExit(f"error: no build started in {out_dir} — run `deck.py start` first")

    checked = verify(out_dir)
    running = _alive(state.get("pid", -1))
    elapsed = int(time.time()) - int(state.get("started_at", time.time()))

    # Liveness is checked FIRST, and a .pptx on disk cannot overrule it.
    #
    # `build-deck` renders the deck, lints the geometry, and re-renders to the
    # *same path* until the layout settles -- observed three passes on a plain
    # five-slide deck, the last one still fixing overflows. So a complete,
    # valid, correctly-sized .pptx exists minutes before the build is finished.
    # Trusting the file while the process still holds it hands the user a
    # pre-repair deck, or a torn read of a zip being rewritten underneath them.
    if running:
        # `build-deck` prints only when it finishes -- the pipeline's own stage
        # logging goes to a per-session file, not to stdout -- so the log is
        # empty for most of a run. Report elapsed time, which is always true,
        # rather than an empty string the agent is told to relay.
        result = {
            "state": "running", "done": False, "elapsed_seconds": elapsed,
            "note": f"still rendering after {elapsed}s of a typical 180-600s build",
            "next": f"python {Path(__file__).name} status --out-dir {out_dir}",
        }
        latest = _tail(Path(state["log"]), 1)
        if latest:
            result["progress"] = latest
    elif checked["verified"]:
        # How long the build *took*, not how long ago it started. Polling an
        # hour later would otherwise report an hour, and the agent is told to
        # flag a long build -- it would flag a fast one it happened to revisit.
        finished = Path(checked["pptx"]).stat().st_mtime
        took = max(0, int(finished) - int(state.get("started_at", finished)))
        result = {"state": "done", "done": True, **checked, "elapsed_seconds": took}
    else:
        # Process gone and no usable .pptx: surface the log, because the reason
        # is in it and the agent cannot see the detached process's output.
        result = {
            "state": "error", "done": False, "elapsed_seconds": elapsed,
            **checked,
            "log_tail": _tail(Path(state["log"]), 15),
            "hint": "the build exited without writing a usable deck; the tail above says why",
        }

    (out_dir / STATE).write_text(json.dumps({**state, "state": result["state"]}, indent=2))
    print(json.dumps(result, indent=2))
    return 0 if result["state"] != "error" else 1


def _tail(path: Path, lines: int) -> str:
    try:
        content = path.read_text(errors="replace").strip().splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pl = sub.add_parser("plan", help="request -> markdown plan (blocks 40-90s)")
    pl.add_argument("--request", required=True, help="the user's request, passed through as-is")
    pl.add_argument("--context", default=None, help="material the user pasted, to ground the plan")
    pl.add_argument("--source", action="append", help="grounding file on disk (repeatable)")
    pl.add_argument("--out", required=True, help="where to write the plan")
    pl.set_defaults(func=plan)

    ed = sub.add_parser("edit", help="apply a requested change to an existing plan")
    ed.add_argument("--instruction", required=True, help="the change, passed through as-is")
    ed.add_argument("--plan", required=True)
    ed.add_argument("--out", default=None, help="default: overwrite --plan")
    ed.set_defaults(func=edit)

    s = sub.add_parser("start", help="launch build-deck detached and return at once")
    s.add_argument("--plan", required=True, help="path to the approved plan markdown")
    s.add_argument("--out-dir", required=True, help="where deck.pptx and previews land")
    s.add_argument("--palette-family", default=None, help="visual style (default ibm_watsonx)")
    s.set_defaults(func=start)

    q = sub.add_parser("status", help="is it done? poll until state is done or error")
    q.add_argument("--out-dir", required=True)
    q.set_defaults(func=status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
