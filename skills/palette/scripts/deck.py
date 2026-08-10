#!/usr/bin/env python3
"""Run Palette's slow commands without blocking, for hosts that cap a step.

Two of them are slow. `build-plan` is one model call: 43-82s locally, ~170s
inside CUGA's sandbox. `build-deck` renders every slide and repairs geometry:
three to ten minutes. Some hosts allow that in one call — Claude Code's Bash
tool permits ten minutes. CUGA does not: it kills a sandbox step at 120s.

A killed step is the worst possible outcome, because it is silent. The work
carries on in its own process and finishes; the caller just never finds out.
Both real failures in the wild were exactly this — an agent reported that
Palette had timed out and gave up, while a good plan and a good 15-slide deck
sat finished in the workspace.

So nothing here blocks. Every slow command detaches and is collected by
polling:

    python scripts/deck.py plan        --request "..." --out plan.md
    python scripts/deck.py plan-status --out plan.md          # until done
    python scripts/deck.py start       --plan plan.md --out-dir ./deck
    python scripts/deck.py status      --out-dir ./deck        # until done

Each prints one JSON object. Completion is computed from the filesystem, never
from an exit code: a build that exits 0 having written nothing is a failure,
and an agent relaying "done" from a return value reports a deck that does not
exist. Conversely a build is only over once it says so in `.palette-exit` —
liveness cannot be probed by signalling, because a sandbox will not permit it.

Stdlib only, so it runs wherever the agent does.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

#: Written into --out-dir so `status` can find the build across separate calls.
#: Each poll is its own process; nothing is held in memory between them.
STATE = ".palette-build.json"

#: Holds the build's exit code, written by the shell when the build ends. This
#: is how `status` knows a build is over, because it is the only signal that
#: survives a sandbox: reading a workspace file is always allowed, while asking
#: the kernel about another process is not.
EXIT = ".palette-exit"

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


def _finished_at(exit_file: Path) -> int | None:
    """A step's exit code, or None if it has not ended yet.

    The primary signal, because it is the only one a sandbox cannot take away.
    Every slow command is wrapped in `; echo $? > <file>`, so the file appears
    exactly once, when that command is over. Reading a file is allowed
    everywhere this runs; asking the kernel about another process is not.
    """
    try:
        return int(exit_file.read_text().strip())
    except (OSError, ValueError):
        return None


def _finished(out_dir: Path) -> int | None:
    """The deck build's exit code."""
    return _finished_at(out_dir / EXIT)


def _alive(pid: int) -> bool | None:
    """True, False, or None when the host refuses to say.

    None is the important one. CUGA runs each step under Seatbelt with
    `(allow signal (target self))`, so `os.kill(pid, 0)` against the detached
    build raises PermissionError and `ps` cannot even exec. Neither is evidence
    that the build died -- but treating them as such reported `state: error` on
    the first poll of every sandboxed build, minutes before the .pptx existed,
    and the agent then invented reasons for a failure that had not happened.

    So: only a real ProcessLookupError means dead. Anything we cannot determine
    is None, and the caller waits for `.palette-exit` instead.
    """
    if pid < 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return None
    # Alive -- but pids get recycled, so confirm it is still our build.
    try:
        listing = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if listing.returncode != 0:
        return None
    return "palette.py" in listing.stdout


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


def _sidecar(target: Path, suffix: str) -> Path:
    """A hidden file beside *target*, so two plans in one directory never clash."""
    return target.parent / f".{target.name}.{suffix}"


def _detach(home: Path, argv: list[str], log: Path, exit_file: Path) -> subprocess.Popen:
    """Run palette.py in the background, recording its exit code when it ends.

    Everything slow goes through here. A step limit kills the *caller*, not the
    detached child, so the work still lands -- but only this exit file lets a
    later poll find out that it did.
    """
    cmd = [interpreter(home), "-u", "palette.py", *argv]
    quoted = " ".join(shlex.quote(part) for part in cmd)
    exit_file.unlink(missing_ok=True)   # a stale one reads as "already finished"
    with log.open("wb") as handle:
        return subprocess.Popen(
            ["/bin/sh", "-c", f"{quoted}; echo $? > {shlex.quote(str(exit_file))}"],
            cwd=str(home),            # palette.py imports config/pipeline from here
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,   # outlive the step that started it
        )


def _start_plan(home: Path, argv: list[str], out: Path, label: str) -> int:
    """Kick off a plan or an edit detached, and say how to collect it."""
    process = _detach(home, argv, _sidecar(out, "plan.log"), _sidecar(out, "plan.exit"))
    state = {
        "state": "running", "stage": label, "pid": process.pid,
        "plan": str(out), "started_at": int(time.time()),
    }
    _sidecar(out, "plan.json").write_text(json.dumps(state, indent=2))
    print(json.dumps({
        **state,
        "note": f"{label} takes 40-180s; poll with `deck.py plan-status`",
        "next": f"python {Path(__file__).name} plan-status --out {out}",
    }))
    return 0


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
    if args.wait:
        return _relay(_run_palette(home, argv))
    return _start_plan(home, argv, out, "build-plan")


def plan_status(args: argparse.Namespace) -> int:
    """Has the plan (or edit) landed? Same filesystem-only test as the build."""
    out = Path(args.out).expanduser().resolve()
    state = _load(_sidecar(out, "plan.json"))
    if not state:
        raise SystemExit(f"error: no plan started for {out} — run `deck.py plan` first")

    exit_code = _finished_at(_sidecar(out, "plan.exit"))
    elapsed = int(time.time()) - int(state.get("started_at", time.time()))
    written = out.is_file() and out.stat().st_size > 0

    if exit_code is None and _alive(state.get("pid", -1)) is not False:
        result = {
            "state": "running", "done": False, "elapsed_seconds": elapsed,
            "note": f"still drafting after {elapsed}s of a typical 40-180s call",
            "next": f"python {Path(__file__).name} plan-status --out {out}",
        }
    elif written:
        result = {
            "state": "done", "done": True, "plan": str(out),
            "elapsed_seconds": elapsed,
            "text": out.read_text(encoding="utf-8", errors="replace"),
        }
    else:
        result = {
            "state": "error", "done": False, "exit_code": exit_code,
            "elapsed_seconds": elapsed,
            "log_tail": _tail(_sidecar(out, "plan.log"), 15),
            "hint": "the plan step ended without writing a plan; the tail above says why",
        }

    _sidecar(out, "plan.json").write_text(json.dumps({**state, "state": result["state"]}, indent=2))
    print(json.dumps(result, indent=2))
    return 0 if result["state"] != "error" else 1


def edit(args: argparse.Namespace) -> int:
    """edit-plan, reading and writing the same file by absolute path."""
    home = palette_home()
    plan_path = Path(args.plan).expanduser().resolve()
    if not plan_path.is_file():
        raise SystemExit(f"error: no plan at {plan_path}")
    out = Path(args.out).expanduser().resolve() if args.out else plan_path
    argv = ["edit-plan", args.instruction, "--plan", str(plan_path), "--out", str(out)]
    if args.wait:
        return _relay(_run_palette(home, argv))
    # An edit is the same shape of model call as a plan -- 54s measured, and
    # subject to the same step limit -- so it collects the same way.
    return _start_plan(home, argv, out, "edit-plan")


def start(args: argparse.Namespace) -> int:
    home = palette_home()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = Path(args.plan).expanduser().resolve()
    if not plan.is_file():
        raise SystemExit(f"error: no plan at {plan}")

    state_path = out_dir / STATE
    previous = _load(state_path)
    if previous and _finished(out_dir) is None and _alive(previous.get("pid", -1)) is not False:
        print(json.dumps({**previous, "note": "already building; poll with status"}))
        return 0

    log = out_dir / "build.log"
    argv = ["build-deck", "--plan", str(plan), "--out-dir", str(out_dir), "--json"]
    if args.palette_family:
        argv += ["--palette-family", args.palette_family]

    process = _detach(home, argv, log, out_dir / EXIT)

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
    exit_code = _finished(out_dir)
    elapsed = int(time.time()) - int(state.get("started_at", time.time()))

    # Has the build ENDED? That question comes first, and a .pptx on disk does
    # not answer it. `build-deck` renders, lints the geometry, and re-renders
    # to the *same path* until the layout settles -- three passes on a plain
    # five-slide deck, the last still fixing overflows. So a complete, valid,
    # correctly-sized .pptx exists minutes before the build is over, and
    # handing it over then means a pre-repair deck or a torn read of a zip
    # being rewritten underneath the user.
    #
    # `.palette-exit` answers it, and `_alive` only corroborates: it returns
    # None wherever the host will not discuss other processes, and None must
    # never read as "dead" -- doing so failed every sandboxed build on poll one.
    if exit_code is None and _alive(state.get("pid", -1)) is not False:
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
        # Build over, no usable .pptx: surface the log, because the reason is
        # in it and the agent cannot see the detached process's output.
        result = {
            "state": "error", "done": False, "elapsed_seconds": elapsed,
            "exit_code": exit_code,
            **checked,
            "log_tail": _tail(Path(state["log"]), 15),
            "hint": "the build ended without writing a usable deck; the tail above says why",
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

    pl = sub.add_parser("plan", help="request -> markdown plan; returns at once, poll plan-status")
    pl.add_argument("--request", required=True, help="the user's request, passed through as-is")
    pl.add_argument("--context", default=None, help="material the user pasted, to ground the plan")
    pl.add_argument("--source", action="append", help="grounding file on disk (repeatable)")
    pl.add_argument("--out", required=True, help="where to write the plan")
    pl.add_argument("--wait", action="store_true",
                    help="block until done (40-180s) instead of detaching; for a terminal, "
                         "not for an agent whose step can be cut short")
    pl.set_defaults(func=plan)

    ps = sub.add_parser("plan-status", help="is the plan (or edit) written yet?")
    ps.add_argument("--out", required=True, help="the --out you passed to plan or edit")
    ps.set_defaults(func=plan_status)

    ed = sub.add_parser("edit", help="apply a requested change to an existing plan")
    ed.add_argument("--instruction", required=True, help="the change, passed through as-is")
    ed.add_argument("--plan", required=True)
    ed.add_argument("--out", default=None, help="default: overwrite --plan")
    ed.add_argument("--wait", action="store_true", help="block instead of detaching")
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
