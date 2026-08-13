#!/usr/bin/env python3
"""The Claude Code half of the benchmark.

    python benchmark/claude_run.py prepare --auto --cases core
        Drives the `claude` CLI headlessly, one conversation per case.

    python benchmark/claude_run.py prepare --cases corpus
        No CLI, or you want to watch: writes a working directory per case and
        prints the exact utterances to paste. One case per Claude Code session.

    python benchmark/claude_run.py collect
        Finds the deck each case produced, copies it to output/, and judges it
        with the same rules every other host uses — same slide check, same IBM
        Plex check, same `verdict.judge`.

The point is comparability: three hosts, one corpus, one judge. What differs is
who types the utterances, not how the result is scored.

`--auto` needs a `claude` on PATH; it is detected, never assumed, and falls back
to the run sheet without one. Note that this host differs from the other two in
**model as well as scaffold** — it runs Claude, they run gpt-oss-120b — so its
column answers "which product builds better decks", not "which scaffold drives
the skill better".
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCHMARK_DIR.parent
sys.path.append(str(BENCHMARK_DIR))

# The corpus lives at $PALETTE_BENCH_INPUTS and cases.py reads it on import, so
# a missing variable surfaces here. Re-raised as SystemExit: the message is
# already actionable, and a traceback through the import machinery buries it.
try:
    from cases import CASES, by_tag  # noqa: E402
except Exception as exc:  # noqa: BLE001 - corpus.CorpusNotConfigured, or a bad path
    raise SystemExit(f"error: {exc}") from None
from verdict import MIN_PPTX_BYTES, CaseResult, inspect_deck, judge  # noqa: E402

RUNS = BENCHMARK_DIR / "runs"


def claude_cli() -> str | None:
    """The Claude Code CLI, if this machine has one."""
    return shutil.which("claude")


def _cli_label() -> str:
    """`claude (Claude Code X.Y.Z)`, or a plain note when the CLI is absent."""
    binary = claude_cli()
    if not binary:
        return "claude (model not reported; run collected by hand)"
    try:
        raw = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=30
        ).stdout.strip()
        # `claude --version` prints "2.1.228 (Claude Code)"; keep the number.
        version = raw.split()[0] if raw else ""
    except (OSError, subprocess.SubprocessError):
        version = ""
    return f"claude (Claude Code {version})" if version else "claude"


def latest_run() -> Path:
    prepared = [p for p in RUNS.glob("*/claude") if p.is_dir()]
    if not prepared:
        raise SystemExit(
            f"error: nothing prepared under {RUNS}. Run `claude_run.py prepare` first."
        )
    return max(prepared, key=lambda p: p.stat().st_mtime)


def prepare(selected, out_root: Path, auto: bool) -> int:
    root = out_root / "claude"
    root.mkdir(parents=True, exist_ok=True)

    for case in selected:
        workspace = root / case.name
        if workspace.exists():
            shutil.rmtree(workspace)
        (workspace / "input").mkdir(parents=True)

        (workspace / "input" / "request.txt").write_text(case.request, encoding="utf-8")
        (workspace / "input" / "replies.txt").write_text(
            "\n".join(case.replies), encoding="utf-8"
        )
        opening = case.request
        if case.context:
            (workspace / "input" / "context.md").write_text(case.context, encoding="utf-8")
            opening = f"{case.request}\n\n{case.context}"
        (workspace / "utterance.txt").write_text(opening, encoding="utf-8")

        # Without this the case cannot be scored. `deck.py` writes its call
        # trace only when $PALETTE_TRACE is set, and the judge reads the trace
        # to see whether `edit-plan` was called and whether pasted material
        # reached `--context`. 24 of the 33 cases depend on one of those, so a
        # run without it fails them all regardless of what Claude did — while
        # the decks on disk look fine.
        #
        # The harness cannot set a variable inside the shell you will run
        # Claude Code in, so it writes the exports and the run sheet tells you
        # to source them.
        (workspace / "env.sh").write_text(
            "# Source this before starting Claude Code in this directory.\n"
            f"export PALETTE_TRACE={workspace / 'palette-calls.jsonl'}\n"
            f"export PALETTE_HOME={REPO_ROOT}\n",
            encoding="utf-8",
        )

        (workspace / "case.json").write_text(
            json.dumps(
                {
                    "name": case.name,
                    "covers": case.covers,
                    "tags": list(case.tags),
                    "replies": list(case.replies),
                    "expect_deck": case.expect_deck,
                    "expect_slides": case.expect_slides,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    binary = claude_cli()
    if auto and binary:
        return _drive(selected, root, binary)
    if auto and not binary:
        print("no `claude` on PATH — falling back to the manual run sheet\n")

    _run_sheet(selected, root)
    return 0


def _drive(selected, root: Path, binary: str) -> int:
    """Headless, if this machine has the CLI.

    **Untested — no `claude` binary was present on the machine this was written
    on.** Treat the first run as a shakedown, not a measurement.

    Two things it has to get right, both of which the first version did not:

    *Conversation state.* `claude -p <message>` is one-shot. Sending the replies
    as separate invocations starts a fresh session each time, so `"yes"` arrives
    with no plan to approve and every multi-turn case — the traps, the edits,
    the whole reason the suite exists — measures nothing. `--continue` resumes
    the most recent conversation in the working directory, and each case has its
    own, so the turns stay together.

    *The trace.* `deck.py` records calls only when `$PALETTE_TRACE` is set, and
    the judge reads that trace for 24 of the 33 cases. A subprocess inherits the
    parent environment, so it is set here explicitly rather than relied upon.
    """
    for case in selected:
        workspace = root / case.name
        print(f"[{case.name}] driving {binary} …", flush=True)

        environment = os.environ.copy()
        environment["PALETTE_TRACE"] = str(workspace / "palette-calls.jsonl")
        environment.setdefault("PALETTE_HOME", str(REPO_ROOT))

        transcript = []
        started = time.time()
        messages = [(workspace / "utterance.txt").read_text(encoding="utf-8"), *case.replies]
        for index, message in enumerate(messages):
            command = [binary, "-p", message]
            if index:
                command.insert(1, "--continue")
            done = subprocess.run(
                command, cwd=str(workspace), capture_output=True, text=True,
                timeout=1800, env=environment,
            )
            transcript.append({
                "sent": message, "answer": done.stdout,
                "error": done.stderr, "exit_code": done.returncode,
            })
            if done.returncode != 0:
                # A failed turn means every later reply lands in the wrong
                # state; stop this case rather than record noise.
                print(f"  turn {index + 1} exited {done.returncode}: "
                      f"{done.stderr.strip()[:200]}")
                break
        # Wall clock for the case. The trace can only ever bound this from
        # below — it starts at the first deck.py call, so everything the model
        # spent deciding to make that call is invisible to it.
        (workspace / "transcript.json").write_text(
            json.dumps(
                {"seconds": round(time.time() - started, 1), "turns": transcript},
                indent=2,
            ),
            encoding="utf-8",
        )
    return 0


def _run_sheet(selected, root: Path) -> None:
    print(f"prepared {len(selected)} case(s) under {root}\n")
    print("For each case: cd into it, **source env.sh**, then open Claude Code")
    print("there, paste the utterance, and send each reply as it hands back.\n")
    print("Both steps matter. The working directory is where `collect` looks for")
    print("the deck; `env.sh` sets $PALETTE_TRACE, without which no call trace is")
    print("written and 24 of the 33 cases cannot be scored at all — they fail on")
    print("a missing trace while the deck sits on disk looking correct.\n")

    for index, case in enumerate(selected, 1):
        workspace = root / case.name
        print(f"── {index}. {case.name}")
        print(f"   cd {workspace} && source env.sh")
        opening = (workspace / "utterance.txt").read_text(encoding="utf-8")
        first = opening if len(opening) < 400 else opening[:400] + f"\n   … [+{len(opening)-400} chars, full text in utterance.txt]"
        print(f"   paste ▸ {first}")
        for reply in case.replies:
            print(f"   then  ▸ {reply}")
        print()

    print("When they are all done:")
    print(f"   python {Path(__file__).name} collect\n")


def _case_seconds(workspace: Path, calls: list[dict]) -> tuple[float, str | None]:
    """How long the case took, and how confident that number is.

    `--auto` records the real wall clock. A case a person drove has no such
    record, so this falls back to the span of the call trace — first `deck.py`
    invocation to the end of the last. That is a **lower bound**: everything the
    model spent before reaching for the first command is invisible to it, and on
    these cases that is the majority of the thinking. Labelled, never silently
    mixed with a measured figure.
    """
    transcript = workspace / "transcript.json"
    if transcript.is_file():
        try:
            payload = json.loads(transcript.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("seconds"):
                return float(payload["seconds"]), "measured"
        except (OSError, ValueError):
            pass

    stamps = [c.get("at") for c in calls if isinstance(c.get("at"), (int, float))]
    if not stamps:
        return 0.0, None
    last = calls[-1]
    span = max(stamps) + float(last.get("seconds") or 0) - min(stamps)
    return round(span, 1), "trace-span"


def collect(root: Path) -> int:
    results: list[CaseResult] = []
    timings: dict[str, str] = {}
    by_name = {c.name: c for c in CASES}

    for workspace in sorted(p for p in root.iterdir() if p.is_dir()):
        meta_path = workspace / "case.json"
        if not meta_path.is_file():
            continue
        case = by_name.get(json.loads(meta_path.read_text())["name"])
        if case is None:
            continue

        result = CaseResult(case=case, workspace=workspace)

        decks = [
            p for p in workspace.rglob("*.pptx")
            if p.stat().st_size >= MIN_PPTX_BYTES and "output" not in p.parts
        ]
        if decks:
            deck = max(decks, key=lambda p: p.stat().st_mtime)
            result.pptx = deck
            result.slides, result.has_plex = inspect_deck(deck)
            output = workspace / "output"
            output.mkdir(exist_ok=True)
            shutil.copy2(deck, output / f"{case.name}.pptx")
            for preview in sorted(deck.parent.glob("*.png")):
                shutil.copy2(preview, output / preview.name)

        trace = next(iter(workspace.rglob("palette-calls.jsonl")), None)
        if trace is not None:
            for line in trace.read_text(encoding="utf-8").splitlines():
                try:
                    result.palette_calls.append(json.loads(line))
                except ValueError:
                    continue

        result.seconds, timing = _case_seconds(workspace, result.palette_calls)
        timings[case.name] = timing

        judge(case, result)
        results.append(result)

    if not results:
        raise SystemExit(f"error: nothing to collect under {root}")

    payload = {
        "host": "claude",
        # Claude Code does not report which model answered, so this names the
        # CLI instead of inventing a model id. The report says plainly that the
        # model is unreported rather than printing a guess next to two hosts
        # whose models are known exactly.
        "model": _cli_label(),
        "cases": len(results),
        "passed": sum(1 for r in results if r.ok),
        "results": [
            {
                "name": r.case.name,
                "covers": r.case.covers,
                "tags": list(r.case.tags),
                "ok": r.ok,
                "failures": r.failures,
                "pptx": str(r.pptx) if r.pptx else None,
                "slides": r.slides,
                "ibm_plex": r.has_plex,
                "seconds": r.seconds or None,
                "timing": timings.get(r.case.name),
                "palette_calls": [
                    {"command": c.get("command"), "args": c.get("args"),
                     "seconds": c.get("seconds")}
                    for c in r.palette_calls
                ],
            }
            for r in results
        ],
    }
    report = root / "report.json"
    report.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    width = max(len(r.case.name) for r in results)
    print(f"\n{'case':<{width}}  {'verdict':<7} {'slides':>6}  palette calls")
    print("-" * (width + 40))
    for r in results:
        calls = " ".join(c.get("command", "?") for c in r.palette_calls) or "(no trace)"
        print(f"{r.case.name:<{width}}  {'pass' if r.ok else 'FAIL':<7} "
              f"{r.slides or '-':>6}  {calls[:36]}")
        for failure in r.failures:
            print(f"{'':<{width}}  ↳ {failure}")
    print("-" * (width + 40))
    print(f"{payload['passed']}/{len(results)} passed · report: {report}")
    return 0 if all(r.ok for r in results) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("prepare", help="write a working directory per case and print the run sheet")
    pr.add_argument("--cases", help="tag to select (corpus, core, edit, …)")
    pr.add_argument("--case", action="append", help="one case by name (repeatable)")
    pr.add_argument("--out", default=str(RUNS), help="where runs land")
    pr.add_argument("--auto", action="store_true", help="drive the claude CLI if this machine has one")

    co = sub.add_parser("collect", help="harvest the decks and judge them")
    co.add_argument("--run", help="a prepared run directory (default: the newest)")

    args = parser.parse_args(argv)

    if args.command == "collect":
        return collect(Path(args.run).expanduser().resolve() if args.run else latest_run())

    selected = list(CASES)
    if args.cases:
        selected = list(by_tag(args.cases))
    if args.case:
        wanted = set(args.case)
        selected = [c for c in CASES if c.name in wanted]
    if not selected:
        raise SystemExit("error: no cases selected")

    out_root = Path(args.out).expanduser().resolve() / time.strftime("%Y%m%d-%H%M%S")
    return prepare(selected, out_root, args.auto)


if __name__ == "__main__":
    raise SystemExit(main())
