#!/usr/bin/env python3
"""Drive the palette skill through CUGA's agent SDK, one scripted conversation
at a time, and report what Palette was actually asked to do.

    python benchmark/run.py --cases core          # a handful
    python benchmark/run.py                       # all of them
    python benchmark/run.py --case edit_slide_count --keep

Why an SDK harness rather than the web UI: a deck takes minutes and needs
several turns, so exercising it by hand does not scale past a couple of tries,
and the interesting failures are the ones that only show up across turns —
building an unapproved plan, re-planning instead of editing, losing pasted
context on the second pass. Those need a scripted user.

Each case produces:

  * the final `.pptx` (or an explicit note that none was produced)
  * every `deck.py` call with its arguments and its output, from `$PALETTE_TRACE`
  * the full conversation
  * a verdict, from the filesystem rather than from what the agent said

The verdict never trusts the agent. A deck exists if there is a `.pptx` of a
plausible size carrying IBM Plex, and not otherwise.

Requires: a CUGA checkout on `sys.path` (`--cuga`, or $CUGA_HOME), $PALETTE_HOME,
$RITS_API_KEY, and the palette skill installed into the CUGA folder this points
at. `--check` verifies all of that without running anything.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCHMARK_DIR.parent
# Append, never insert: this directory going first shadows any stdlib module
# that shares a filename here. A file called inspect.py did exactly that, and
# dataclasses failed deep inside cases.py with a baffling AttributeError.
sys.path.append(str(BENCHMARK_DIR))

from cases import CASES, Case, by_tag  # noqa: E402

MIN_PPTX_BYTES = 20_000
#: A build is 3-10 minutes and a plan up to 3; a conversation that has not
#: finished well past that is stuck rather than slow.
DEFAULT_CASE_TIMEOUT = 1500


# ---------------------------------------------------------------- environment


def add_cuga_to_path(explicit: str | None) -> Path:
    """Put a CUGA checkout on sys.path and return it."""
    raw = explicit or os.environ.get("CUGA_HOME", "").strip()
    if not raw:
        raise SystemExit(
            "error: set --cuga <path> or $CUGA_HOME to a cuga-agent checkout"
        )
    home = Path(raw).expanduser().resolve()
    src = home / "src"
    if not (src / "cuga" / "sdk.py").is_file():
        raise SystemExit(f"error: {home} does not look like a cuga-agent checkout")
    sys.path.insert(0, str(src))

    # CUGA reads its credentials from a .env it finds relative to the *current*
    # directory. This harness runs from Palette, and each case runs from its own
    # workspace, so that search never lands on CUGA's file and every model call
    # fails on a missing key. `ENV_FILE` is the supported override.
    env_file = home / ".env"
    if env_file.is_file() and not os.environ.get("ENV_FILE", "").strip():
        os.environ["ENV_FILE"] = str(env_file)

    # This harness lives in Palette but *runs* CUGA, so it needs CUGA's
    # dependencies — dynaconf, langgraph, the model clients. Palette's venv has
    # none of them, and the failure is an unhelpful ModuleNotFoundError several
    # imports deep. Say so here instead.
    try:
        import cuga.config  # noqa: F401
    except ModuleNotFoundError as exc:
        interpreter = home / ".venv" / "bin" / "python"
        raise SystemExit(
            f"error: this interpreter cannot import CUGA ({exc.name!r} is missing).\n"
            f"The harness drives CUGA, so run it with CUGA's interpreter:\n\n"
            f"  {interpreter} {Path(__file__).resolve()} …\n"
        ) from exc
    return home


def preflight(cuga_home: Path) -> list[str]:
    """Everything that has to be true before a run means anything."""
    problems: list[str] = []

    palette_home = os.environ.get("PALETTE_HOME", "").strip()
    if not palette_home:
        problems.append("PALETTE_HOME is not set")
    elif not (Path(palette_home) / "palette.py").is_file():
        problems.append(f"PALETTE_HOME={palette_home} has no palette.py")

    if not os.environ.get("RITS_API_KEY", "").strip():
        problems.append("RITS_API_KEY is not set — every model call will fail")

    skill = cuga_home / ".cuga" / "skills" / "palette" / "SKILL.md"
    if not skill.is_file():
        problems.append(
            f"no palette skill at {skill.parent} — "
            f"run `make skill-install CUGA={cuga_home}`"
        )
    else:
        source = REPO_ROOT / "skills" / "palette"
        installed = skill.parent
        drifted = [
            p.name
            for p in source.rglob("*")
            if p.is_file()
            and "__pycache__" not in p.parts
            and (installed / p.relative_to(source)).read_bytes() != p.read_bytes()
            if (installed / p.relative_to(source)).is_file()
        ]
        if drifted:
            problems.append(
                f"the installed skill has drifted from this checkout ({', '.join(drifted)}) — "
                f"reinstall before benchmarking, or you are measuring old instructions"
            )

    # Installed is not the same as *discoverable*. Discovery reads $CUGA_FOLDER,
    # and when that is wrong the scan silently returns nothing: the model is
    # never offered the skill, writes a deck by hand, and the run looks like a
    # routing failure. That reading cost an hour, so check it directly.
    os.environ["CUGA_FOLDER"] = str(cuga_home / ".cuga")
    try:
        from cuga.backend.skills.loader import discover_skills

        found = [s.name for s in discover_skills(str(cuga_home / ".cuga"))]
        if "palette" not in found:
            problems.append(
                f"CUGA cannot discover the palette skill under {cuga_home / '.cuga'} "
                f"(found: {found or 'nothing'}) — the model would never be offered it"
            )
    except Exception as exc:  # noqa: BLE001 - report, do not crash the preflight
        problems.append(f"skill discovery raised {type(exc).__name__}: {exc}")

    return problems


def configure_cuga(cuga_home: Path) -> None:
    """The settings `demo_palette` applies, minus the web server."""
    from cuga.config import settings

    # Skill discovery reads $CUGA_FOLDER, *not* CugaAgent(cuga_folder=…) —
    # that argument is for policies. Without this the scan runs against this
    # harness's own directory, finds no skills, and the model is never offered
    # one. It then does the reasonable thing and writes a deck by hand, which
    # looks exactly like a routing failure and is not one.
    os.environ["CUGA_FOLDER"] = str(cuga_home / ".cuga")

    settings.skills.enabled = True
    settings.advanced_features.enable_shell_tool = True
    # A deck is minutes of polling, so the model narrates progress far more than
    # a normal task; without this the first such message ends the run mid-build.
    settings.advanced_features.cuga_lite_nl_auto_continue = True
    settings.advanced_features.sandbox_execution_timeout = 120
    settings.policy.enabled = False


# -------------------------------------------------------------------- results


@dataclass
class TurnRecord:
    sent: str
    answer: str
    seconds: float
    error: str | None = None


@dataclass
class CaseResult:
    case: Case
    turns: list[TurnRecord] = field(default_factory=list)
    palette_calls: list[dict] = field(default_factory=list)
    pptx: Path | None = None
    slides: int = 0
    has_plex: bool = False
    seconds: float = 0.0
    failures: list[str] = field(default_factory=list)
    workspace: Path | None = None

    @property
    def ok(self) -> bool:
        return not self.failures

    def commands(self) -> list[str]:
        return [c.get("command", "?") for c in self.palette_calls]


# ------------------------------------------------------------------ verifying


def inspect_deck(pptx: Path) -> tuple[int, bool]:
    """(slide count, carries IBM Plex). Plex is what proves Palette rendered it."""
    try:
        with zipfile.ZipFile(pptx) as archive:
            slides = [n for n in archive.namelist() if n.startswith("ppt/slides/slide")]
            first = archive.read("ppt/slides/slide1.xml").decode("utf-8", "replace")
        return len(slides), "IBM Plex" in first
    except (OSError, zipfile.BadZipFile, KeyError):
        return 0, False


def newest_deck(workspace: Path) -> Path | None:
    decks = [p for p in workspace.rglob("deck.pptx") if p.stat().st_size >= MIN_PPTX_BYTES]
    return max(decks, key=lambda p: p.stat().st_mtime) if decks else None


def judge(case: Case, result: CaseResult) -> None:
    """Decide from disk, never from what the agent claimed."""
    if not case.expect_deck:
        if result.pptx is not None:
            result.failures.append(
                "a deck was built although the user never approved it — "
                "the confirmation gate was skipped"
            )
        return

    if result.pptx is None:
        result.failures.append("no .pptx was produced")
        return
    if not result.has_plex:
        result.failures.append(
            "the .pptx does not carry IBM Plex — it was not rendered by Palette"
        )
    if case.expect_slides is not None and result.slides != case.expect_slides:
        result.failures.append(
            f"asked for {case.expect_slides} slides, got {result.slides}"
        )
    if "edit" in case.tags:
        commands = result.commands()
        if "edit" not in commands:
            result.failures.append(
                "the user asked for a change but edit-plan was never called "
                f"(calls: {', '.join(commands) or 'none'})"
            )
        elif "plan" in commands[commands.index("edit") :]:
            # Calling edit and then re-planning throws the revision away and
            # pays for a second plan. Seen once, and it passed the old check
            # because `edit` did appear in the trace.
            result.failures.append(
                "edit-plan was called and then the plan was drafted again from "
                "scratch — the revision the user approved was discarded"
            )

    # Every poll is an agent turn. A run that spends forty of them asking "is it
    # done yet" is one step-limit away from failing, and the deck it produced
    # tells you nothing about how close it came.
    polls = sum(1 for c in result.commands() if c in {"plan-status", "status"})
    if polls > 12:
        result.failures.append(
            f"{polls} status polls — each is a model round trip, and the step "
            f"budget is finite. The commands should be holding, not spinning"
        )
    if case.context and not any(
        c.get("args", {}).get("context") for c in result.palette_calls
    ):
        result.failures.append(
            "pasted material was never passed as --context; it was probably "
            "retyped into the request, which loses the grounding"
        )


# --------------------------------------------------------------------- running


async def run_case(case: Case, cuga_home: Path, out_root: Path, timeout: int) -> CaseResult:
    from cuga.sdk import CugaAgent

    result = CaseResult(case=case)
    workspace = out_root / case.name
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    result.workspace = workspace

    # Inside the sandbox, writes are confined to <cwd>/cuga_workspace. A trace
    # anywhere else is silently denied — which produced a run reporting a real
    # 3-slide deck alongside "palette calls: (none)".
    trace = workspace / "cuga_workspace" / "palette-calls.jsonl"
    trace.parent.mkdir(parents=True, exist_ok=True)
    os.environ["PALETTE_TRACE"] = str(trace)

    # CUGA writes per-thread workspaces under the *current* directory, so run
    # each case from its own, and the deck lands beside its trace.
    previous_cwd = Path.cwd()
    os.chdir(workspace)

    started = time.time()
    try:
        agent = CugaAgent(cuga_folder=str(cuga_home / ".cuga"), auto_load_policies=False)
        thread_id = f"bench-{case.name}-{uuid.uuid4().hex[:6]}"

        opening = case.request
        if case.context:
            # How a user actually pastes: the ask, then the material. Splitting
            # them into --request and --context is the agent's job.
            opening = f"{case.request}\n\n{case.context}"

        messages = [opening, *case.replies]
        for index, message in enumerate(messages):
            turn_started = time.time()
            try:
                reply = await asyncio.wait_for(
                    agent.invoke(message=message, thread_id=thread_id, track_tool_calls=True),
                    timeout=timeout,
                )
                record = TurnRecord(
                    sent=message,
                    answer=(reply.answer or "")[:4000],
                    seconds=round(time.time() - turn_started, 1),
                    error=reply.error,
                )
            except asyncio.TimeoutError:
                record = TurnRecord(
                    sent=message,
                    answer="",
                    seconds=round(time.time() - turn_started, 1),
                    error=f"turn exceeded {timeout}s",
                )
                result.turns.append(record)
                result.failures.append(f"turn {index + 1} timed out after {timeout}s")
                break
            except Exception as exc:  # noqa: BLE001 - one bad case must not end the run
                record = TurnRecord(
                    sent=message, answer="", seconds=round(time.time() - turn_started, 1),
                    error=f"{type(exc).__name__}: {exc}",
                )
                result.turns.append(record)
                result.failures.append(f"turn {index + 1} raised {type(exc).__name__}: {exc}")
                break
            result.turns.append(record)
    finally:
        os.chdir(previous_cwd)
        os.environ.pop("PALETTE_TRACE", None)

    result.seconds = round(time.time() - started, 1)

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

    judge(case, result)
    return result


# ------------------------------------------------------------------ reporting


def write_report(results: list[CaseResult], out_root: Path) -> Path:
    payload = {
        "model": "openai/gpt-oss-120b",
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
                    {
                        "command": c.get("command"),
                        "args": c.get("args"),
                        "seconds": c.get("seconds"),
                        "exit_code": c.get("exit_code"),
                        "stdout": c.get("stdout", "")[:1500],
                    }
                    for c in r.palette_calls
                ],
                "turns": [
                    {"sent": t.sent, "answer": t.answer, "seconds": t.seconds, "error": t.error}
                    for t in r.turns
                ],
            }
            for r in results
        ],
    }
    report = out_root / "report.json"
    report.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return report


def print_summary(results: list[CaseResult], report: Path) -> None:
    width = max(len(r.case.name) for r in results)
    print()
    print(f"{'case':<{width}}  {'verdict':<7} {'slides':>6} {'time':>7}  palette calls")
    print("-" * (width + 48))
    for r in results:
        verdict = "pass" if r.ok else "FAIL"
        calls = " ".join(r.commands()) or "(none)"
        print(
            f"{r.case.name:<{width}}  {verdict:<7} {r.slides or '-':>6} "
            f"{r.seconds:>6.0f}s  {calls[:44]}"
        )
        for failure in r.failures:
            print(f"{'':<{width}}  ↳ {failure}")

    passed = sum(1 for r in results if r.ok)
    decks = sum(1 for r in results if r.pptx)
    print("-" * (width + 48))
    print(f"{passed}/{len(results)} passed · {decks} decks built · report: {report}")


# ---------------------------------------------------------------------- entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cuga", help="cuga-agent checkout (default: $CUGA_HOME)")
    parser.add_argument("--case", action="append", help="run only this case (repeatable)")
    parser.add_argument("--cases", help="run only cases with this tag (core, edit, context, trap …)")
    parser.add_argument("--out", default=str(REPO_ROOT / "benchmark" / "runs"),
                        help="where decks, traces and the report land")
    parser.add_argument("--timeout", type=int, default=DEFAULT_CASE_TIMEOUT,
                        help=f"per-turn ceiling in seconds (default {DEFAULT_CASE_TIMEOUT})")
    parser.add_argument("--check", action="store_true", help="verify the setup and exit")
    parser.add_argument("--list", action="store_true", help="list the cases and exit")
    args = parser.parse_args(argv)

    if args.list:
        for case in CASES:
            print(f"{case.name:<26} {','.join(case.tags):<22} {case.covers}")
        return 0

    cuga_home = add_cuga_to_path(args.cuga)
    problems = preflight(cuga_home)
    if problems:
        print("setup is not ready:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    if args.check:
        print(f"ready: cuga={cuga_home}, palette={os.environ['PALETTE_HOME']}, "
              f"skill installed and matching, RITS key present")
        return 0

    selected = list(CASES)
    if args.cases:
        selected = list(by_tag(args.cases))
    if args.case:
        wanted = set(args.case)
        selected = [c for c in CASES if c.name in wanted]
        missing = wanted - {c.name for c in selected}
        if missing:
            raise SystemExit(f"error: no such case(s): {', '.join(sorted(missing))}")
    if not selected:
        raise SystemExit("error: no cases selected")

    configure_cuga(cuga_home)
    out_root = Path(args.out).expanduser().resolve() / time.strftime("%Y%m%d-%H%M%S")
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"running {len(selected)} case(s) against openai/gpt-oss-120b -> {out_root}")
    results: list[CaseResult] = []
    for index, case in enumerate(selected, 1):
        print(f"[{index}/{len(selected)}] {case.name} … ", end="", flush=True)
        result = asyncio.run(run_case(case, cuga_home, out_root, args.timeout))
        print(f"{'pass' if result.ok else 'FAIL'} ({result.seconds:.0f}s)")
        results.append(result)

    report = write_report(results, out_root)
    print_summary(results, report)
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
