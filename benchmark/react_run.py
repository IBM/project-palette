"""The third host: a LangGraph ReAct agent, on watsonx `openai/gpt-oss-120b`.

    python benchmark/react_run.py --check --env-file <cuga>/.env
    python benchmark/react_run.py --cases core
    python benchmark/react_run.py --case edit_slide_count

Third because the first two cannot be told apart. CUGA runs gpt-oss-120b and
Claude Code runs Claude, so every difference between their columns has two
possible causes and no way to separate them. This host makes the model a flag:
run it on the same 120B CUGA uses and the scaffold is the only variable.

It is also the thinnest scaffold in the set — a model, a shell, and a loader
that hands over SKILL.md when asked. No planner, no todo list, no subagents. A
case it passes was passed by the instructions, not by the harness around them.

The agent itself lives in `agents/palette_react/`; this file is the runner, and
it imports `judge` from `run.py` rather than reimplementing it. Two hosts scored
by two rules is not a comparison, and neither is three.

Results land in `benchmark/runs/<timestamp>/react/<case>/`, beside `cuga/` and
`claude/`, in the same input/output shape.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import time
import uuid
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCHMARK_DIR.parent
AGENTS_DIR = REPO_ROOT / "agents"

# Append rather than insert, for the reason run.py documents: these directories
# going first shadow any stdlib module that shares a filename with a file in one.
sys.path.append(str(BENCHMARK_DIR))
sys.path.append(str(AGENTS_DIR))

from cases import CASES, by_tag  # noqa: E402
from run import CaseResult, inspect_deck, judge, newest_deck  # noqa: E402

from palette_react import model as model_factory  # noqa: E402
from palette_react import skill as skill_loader  # noqa: E402
from palette_react.agent import DEFAULT_RECURSION_LIMIT, build_agent  # noqa: E402
from palette_react.tools import DEFAULT_TIMEOUT  # noqa: E402

HOST = "react"
#: A build is 3-10 minutes and a plan up to 3, so a turn well past that is
#: stuck rather than slow. Matches run.py's ceiling for the same reason.
DEFAULT_TURN_TIMEOUT = 1500


class TurnTimeout(Exception):
    pass


def _with_timeout(seconds: int):
    """SIGALRM around one turn. A hung model call is otherwise unbounded.

    Returns a no-op context on platforms without SIGALRM rather than pretending
    to enforce a ceiling it cannot.
    """
    from contextlib import contextmanager

    @contextmanager
    def alarm():
        if not hasattr(signal, "SIGALRM") or seconds <= 0:
            yield
            return

        def fire(signum, frame):  # noqa: ARG001
            raise TurnTimeout(f"turn exceeded {seconds}s")

        previous = signal.signal(signal.SIGALRM, fire)
        signal.alarm(seconds)
        try:
            yield
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)

    return alarm()


def run_case(case, card, out_root: Path, options) -> CaseResult:
    result = CaseResult(case=case)
    workspace = out_root / HOST / case.name
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    result.workspace = workspace

    # Inputs beside outputs — a number you cannot reproduce the question for is
    # not a measurement.
    inputs = workspace / "input"
    inputs.mkdir()
    (inputs / "request.txt").write_text(case.request, encoding="utf-8")
    if case.context:
        (inputs / "context.md").write_text(case.context, encoding="utf-8")
    (inputs / "replies.txt").write_text("\n".join(case.replies), encoding="utf-8")

    trace = workspace / "palette-calls.jsonl"
    os.environ["PALETTE_TRACE"] = str(trace)

    started = time.time()
    session = None
    try:
        session = build_agent(
            workspace,
            [card],
            model_factory.build(options.model),
            lazy=not options.eager,
            timeout=options.timeout,
            recursion_limit=options.recursion_limit,
            thread_id=f"bench-{case.name}-{uuid.uuid4().hex[:6]}",
        )

        # How a user actually pastes: the ask, then the material. Splitting them
        # into --request and --context is the agent's job, and one of the things
        # the judge checks.
        opening = f"{case.request}\n\n{case.context}" if case.context else case.request

        for index, message in enumerate([opening, *case.replies]):
            turn_started = time.time()
            try:
                with _with_timeout(options.turn_timeout):
                    answer = session.send(message)
                session.turns[-1]["seconds"] = round(time.time() - turn_started, 1)
            except TurnTimeout as exc:
                session.turns.append(
                    {"sent": message, "answer": "", "error": str(exc),
                     "seconds": round(time.time() - turn_started, 1)}
                )
                result.failures.append(f"turn {index + 1} timed out")
                break
            except Exception as exc:  # noqa: BLE001 - one bad case must not end the run
                session.turns.append(
                    {"sent": message, "answer": "", "error": f"{type(exc).__name__}: {exc}",
                     "seconds": round(time.time() - turn_started, 1)}
                )
                result.failures.append(f"turn {index + 1} raised {type(exc).__name__}: {exc}")
                break
    except Exception as exc:  # noqa: BLE001 - a case that cannot start is a
        # failed case, not a failed run. Building the agent reaches watsonx, so
        # a credential or quota problem lands here and must not end 33 cases.
        result.failures.append(f"could not start: {type(exc).__name__}: {exc}")
    finally:
        os.environ.pop("PALETTE_TRACE", None)

    result.seconds = round(time.time() - started, 1)
    # Dicts rather than run.py's TurnRecord: this runner writes its own report,
    # and the shared judge never reads turns — it reads the filesystem.
    result.turns = session.turns if session is not None else []

    if trace.is_file():
        for line in trace.read_text(encoding="utf-8").splitlines():
            try:
                result.palette_calls.append(json.loads(line))
            except ValueError:
                continue

    deck = newest_deck(workspace)
    if deck is not None:
        result.pptx = deck
        result.slides, result.has_plex = inspect_deck(deck)
        output = workspace / "output"
        output.mkdir(exist_ok=True)
        shutil.copy2(deck, output / f"{case.name}.pptx")
        for preview in sorted(deck.parent.glob("*.png")):
            shutil.copy2(preview, output / preview.name)
        plan = next(iter(sorted(deck.parent.parent.glob("plan*.md"))), None)
        if plan is not None:
            shutil.copy2(plan, output / "plan.md")

    judge(case, result)
    return result


def write_report(results, out_root: Path, options) -> Path:
    payload = {
        "host": HOST,
        # Recorded rather than hardcoded: the model is this host's whole reason
        # for existing, so a report that does not say which one ran is useless.
        "model": options.model,
        "provider": "watsonx",
        "skill_loading": "eager" if options.eager else "lazy",
        "recursion_limit": options.recursion_limit,
        "cases": len(results),
        "passed": sum(1 for r in results if r.ok),
        "results": [
            {
                "name": r.case.name,
                "covers": r.case.covers,
                "tags": list(r.case.tags),
                "ok": r.ok,
                "failures": r.failures,
                "seconds": r.seconds,
                "pptx": str(r.pptx) if r.pptx else None,
                "slides": r.slides,
                "ibm_plex": r.has_plex,
                "palette_calls": [
                    {"command": c.get("command"), "args": c.get("args"),
                     "seconds": c.get("seconds"), "exit_code": c.get("exit_code"),
                     "stdout": c.get("stdout", "")[:1500]}
                    for c in r.palette_calls
                ],
                "turns": r.turns,
            }
            for r in results
        ],
    }
    report = out_root / HOST / "report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return report


def print_summary(results, report: Path, options) -> None:
    width = max(len(r.case.name) for r in results)
    print()
    print(f"{'case':<{width}}  {'verdict':<7} {'slides':>6} {'time':>7}  palette calls")
    print("-" * (width + 48))
    for r in results:
        calls = " ".join(r.commands()) or "(none)"
        print(f"{r.case.name:<{width}}  {'pass' if r.ok else 'FAIL':<7} "
              f"{r.slides or '-':>6} {r.seconds:>6.0f}s  {calls[:44]}")
        for failure in r.failures:
            print(f"{'':<{width}}  ↳ {failure}")

    passed = sum(1 for r in results if r.ok)
    decks = sum(1 for r in results if r.pptx)
    print("-" * (width + 48))
    print(f"{passed}/{len(results)} passed · {decks} decks built")
    print(f"host={HOST} model={options.model} "
          f"skill={'eager' if options.eager else 'lazy'} "
          f"recursion_limit={options.recursion_limit}")
    print(f"report: {report}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--case", action="append", help="one case by name (repeatable)")
    parser.add_argument("--cases", help="a tag: core, corpus, edit, context, trap …")
    parser.add_argument("--out", default=str(BENCHMARK_DIR / "runs"))
    parser.add_argument("--env-file", help="read credentials from this file first")
    parser.add_argument("--skills-root", help="override $PALETTE_HOME/skills")
    parser.add_argument("--skill", default="palette")
    parser.add_argument("--model", default=model_factory.DEFAULT_MODEL)
    parser.add_argument("--eager", action="store_true",
                        help="inline the skill body — removes the routing decision")
    parser.add_argument("--recursion-limit", type=int, default=DEFAULT_RECURSION_LIMIT)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help="per-command ceiling in seconds")
    parser.add_argument("--turn-timeout", type=int, default=DEFAULT_TURN_TIMEOUT)
    parser.add_argument("--check", action="store_true", help="verify the setup and exit")
    parser.add_argument("--list", action="store_true", help="list the cases and exit")
    options = parser.parse_args(argv)

    if options.list:
        for case in CASES:
            print(f"{case.name:<26} {','.join(case.tags):<22} {case.covers}")
        return 0

    if options.env_file:
        try:
            model_factory.load_env_file(options.env_file)
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    problems: list[str] = []
    card = None
    try:
        card = skill_loader.load(options.skill, options.skills_root)
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

    if options.check:
        # No drift check, and that is not an omission: this host reads the skill
        # where it lives instead of installing a copy, so there is nothing to
        # drift from.
        print(f"ready: skill {card.name!r} read in place at {card.directory}\n"
              f"       model {options.model} on watsonx\n"
              f"       {'eager' if options.eager else 'lazy'} loading, "
              f"recursion limit {options.recursion_limit}\n"
              f"       {len(CASES)} cases available")
        return 0

    selected = list(CASES)
    if options.cases:
        selected = list(by_tag(options.cases))
    if options.case:
        wanted = set(options.case)
        selected = [c for c in CASES if c.name in wanted]
        missing = wanted - {c.name for c in selected}
        if missing:
            raise SystemExit(f"error: no such case(s): {', '.join(sorted(missing))}")
    if not selected:
        raise SystemExit("error: no cases selected")

    out_root = Path(options.out).expanduser().resolve() / time.strftime("%Y%m%d-%H%M%S")
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"running {len(selected)} case(s) on {HOST} "
          f"against {options.model} (watsonx) -> {out_root}")
    results = []
    for index, case in enumerate(selected, 1):
        print(f"[{index}/{len(selected)}] {case.name} … ", end="", flush=True)
        result = run_case(case, card, out_root, options)
        print(f"{'pass' if result.ok else 'FAIL'} ({result.seconds:.0f}s)")
        results.append(result)

    report = write_report(results, out_root, options)
    print_summary(results, report, options)
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
