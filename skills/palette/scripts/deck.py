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
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    return True


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

    if checked["verified"]:
        result = {"state": "done", "done": True, **checked, "elapsed_seconds": elapsed}
    elif running:
        result = {
            "state": "running", "done": False, "elapsed_seconds": elapsed,
            "progress": _tail(Path(state["log"]), 1),
            "next": f"python {Path(__file__).name} status --out-dir {out_dir}",
        }
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
