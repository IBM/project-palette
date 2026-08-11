"""Drive the ReAct agent by hand — the standalone way to prove it works.

    # is everything wired up? runs nothing.
    python agents/palette_react/cli.py --check --env-file ~/code/cuga-agent/.env

    # one deck, scripted: ask, approve, done
    python agents/palette_react/cli.py --trace \\
        "Build a 3-slide deck explaining prompt caching to backend engineers" \\
        --reply yes

    # the same thing, but you type the replies
    python agents/palette_react/cli.py "Build a deck about our on-call rotation"

The deck lands in the workspace (`./palette-react-run` unless you say
otherwise), and with `--trace` you also get `palette-calls.jsonl` — every
`deck.py` invocation with its arguments, which is the only honest account of
what the agent actually asked Palette for.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import zipfile
from pathlib import Path

if __package__ in (None, ""):  # `python agents/palette_react/cli.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "palette_react"

from palette_react import model as model_factory  # noqa: E402
from palette_react import skill as skill_loader  # noqa: E402
from palette_react.agent import DEFAULT_RECURSION_LIMIT, build_agent  # noqa: E402
from palette_react.tools import DEFAULT_TIMEOUT  # noqa: E402

DEFAULT_WORKSPACE = "./palette-react-run"


def _deck_readout(workspace: Path) -> str:
    """What landed on disk. Informational — the benchmark's judge is the verdict."""
    decks = sorted(workspace.rglob("*.pptx"), key=lambda p: p.stat().st_mtime)
    if not decks:
        return "no .pptx under the workspace"

    deck = decks[-1]
    size = deck.stat().st_size
    try:
        with zipfile.ZipFile(deck) as archive:
            slides = sum(1 for n in archive.namelist() if n.startswith("ppt/slides/slide"))
            plex = "IBM Plex" in archive.read("ppt/slides/slide1.xml").decode("utf-8", "replace")
    except (OSError, zipfile.BadZipFile, KeyError):
        return f"{deck} is not a readable .pptx"

    # Plex is the check that separates "a deck exists" from "Palette made this":
    # the renderer forces it, so a hand-written deck cannot have it.
    verdict = "rendered by Palette" if plex else "NOT rendered by Palette (no IBM Plex)"
    return f"{deck}\n  {size:,} bytes · {slides} slides · {verdict}"


def _trace_readout(trace: Path) -> str:
    if not trace.is_file():
        return "no palette calls recorded"
    lines = []
    for raw in trace.read_text(encoding="utf-8").splitlines():
        try:
            call = json.loads(raw)
        except ValueError:
            continue
        lines.append(
            f"  {call.get('command', '?'):<12} {call.get('seconds', 0):>6.1f}s  "
            f"{json.dumps(call.get('args', {}))[:110]}"
        )
    return "\n".join(lines) or "no palette calls recorded"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("request", nargs="?", help="the opening message")
    parser.add_argument("--reply", action="append", default=[],
                        help="a scripted reply, sent when the agent hands back (repeatable)")
    parser.add_argument("--workspace", default=DEFAULT_WORKSPACE,
                        help=f"where the agent works (default {DEFAULT_WORKSPACE})")
    parser.add_argument("--env-file", help="read credentials from this file first")
    parser.add_argument("--skills-root", help="override $PALETTE_HOME/skills")
    parser.add_argument("--skill", default="palette", help="skill folder name")
    parser.add_argument("--model", default=model_factory.DEFAULT_MODEL,
                        help=f"watsonx model id (default {model_factory.DEFAULT_MODEL})")
    parser.add_argument("--eager", action="store_true",
                        help="inline the skill body instead of offering it via load_skill "
                             "— removes the routing decision")
    parser.add_argument("--recursion-limit", type=int, default=DEFAULT_RECURSION_LIMIT)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"per-command ceiling in seconds (default {DEFAULT_TIMEOUT})")
    parser.add_argument("--trace", action="store_true",
                        help="record every deck.py call to palette-calls.jsonl")
    parser.add_argument("--check", action="store_true", help="verify the setup and exit")
    args = parser.parse_args(argv)

    if args.env_file:
        try:
            loaded = model_factory.load_env_file(args.env_file)
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"read {len(loaded)} variable(s) from {args.env_file}")

    # ---------------------------------------------------------------- preflight
    problems: list[str] = []
    card = None
    try:
        card = skill_loader.load(args.skill, args.skills_root)
    except skill_loader.SkillNotFound as exc:
        problems.append(str(exc))

    if card is not None and not (card.scripts / "deck.py").is_file():
        problems.append(f"the skill has no scripts/deck.py under {card.directory}")
    problems.extend(model_factory.missing_credentials())

    if problems:
        print("setup is not ready:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    if args.check:
        print(
            f"ready: skill {card.name!r} at {card.directory}\n"
            f"       model {args.model} on watsonx\n"
            f"       {'eager' if args.eager else 'lazy'} skill loading, "
            f"recursion limit {args.recursion_limit}"
        )
        return 0

    if not args.request:
        parser.error("a request is required unless you pass --check")

    workspace = Path(args.workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    trace = workspace / "palette-calls.jsonl"
    if args.trace:
        # The skill writes this only when the variable is set, so tracing costs
        # nothing when it is off — and this sets it without touching the skill.
        os.environ["PALETTE_TRACE"] = str(trace)
        trace.unlink(missing_ok=True)

    session = build_agent(
        workspace,
        [card],
        model_factory.build(args.model),
        lazy=not args.eager,
        timeout=args.timeout,
        recursion_limit=args.recursion_limit,
        thread_id=f"cli-{int(time.time())}",
    )

    print(f"workspace: {workspace}")
    print(f"model:     {args.model} (watsonx)\n")

    pending = list(args.reply)
    message = args.request
    started = time.time()

    while True:
        print(f"you ▸ {message}\n")
        try:
            answer = session.send(message)
        except KeyboardInterrupt:
            print("\n[interrupted]")
            break
        except Exception as exc:  # noqa: BLE001 - report, never a bare traceback
            print(f"\n[the turn failed: {type(exc).__name__}: {exc}]")
            break
        print(f"agent ▸ {answer}\n")

        if pending:
            message = pending.pop(0)
            continue
        if not sys.stdin.isatty():
            break
        try:
            message = input("you ▸ ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not message or message.lower() in {"exit", "quit"}:
            break

    print("-" * 72)
    print(f"{len(session.turns)} turn(s) in {time.time() - started:.0f}s")
    print(f"deck: {_deck_readout(workspace)}")
    if args.trace:
        print(f"palette calls:\n{_trace_readout(trace)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
