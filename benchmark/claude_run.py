#!/usr/bin/env python3
"""The Claude Code half of the benchmark.

Claude Code has no headless CLI on this machine, so this cannot drive it the way
`run.py` drives CUGA's SDK. Rather than pretend otherwise, it splits the job:

    python benchmark/claude_run.py prepare --cases corpus
        Writes a working directory per case, with the input saved, and prints
        the exact utterances to paste. One case per Claude Code session.

    python benchmark/claude_run.py collect
        Finds the deck each case produced, copies it to output/, and judges it
        with the same rules `run.py` uses — same slide check, same IBM Plex
        check, same verdict.

The point is comparability: two hosts, one corpus, one judge. What differs is
who types the utterances, not how the result is scored.

If a `claude` binary ever lands on PATH, `prepare --auto` will use it and the
manual step disappears. It is detected, not assumed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCHMARK_DIR.parent
sys.path.append(str(BENCHMARK_DIR))

from cases import CASES, by_tag  # noqa: E402
from run import MIN_PPTX_BYTES, CaseResult, inspect_deck, judge  # noqa: E402

RUNS = BENCHMARK_DIR / "runs"


def claude_cli() -> str | None:
    """The Claude Code CLI, if this machine has one."""
    return shutil.which("claude")


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
    """Headless, if this machine has the CLI. Untested here: none was present."""
    for case in selected:
        workspace = root / case.name
        print(f"[{case.name}] driving {binary} …", flush=True)
        transcript = []
        for message in [(workspace / "utterance.txt").read_text(), *case.replies]:
            done = subprocess.run(
                [binary, "-p", message],
                cwd=str(workspace), capture_output=True, text=True, timeout=1800,
            )
            transcript.append({"sent": message, "answer": done.stdout, "error": done.stderr})
        (workspace / "transcript.json").write_text(json.dumps(transcript, indent=2))
    return 0


def _run_sheet(selected, root: Path) -> None:
    print(f"prepared {len(selected)} case(s) under {root}\n")
    print("For each case: open Claude Code **in that case's directory**, paste the")
    print("utterance, then send each reply as the agent hands back to you.\n")
    print("The working directory matters — `collect` looks for the deck there.\n")

    for index, case in enumerate(selected, 1):
        workspace = root / case.name
        print(f"── {index}. {case.name}")
        print(f"   cd {workspace}")
        opening = (workspace / "utterance.txt").read_text(encoding="utf-8")
        first = opening if len(opening) < 400 else opening[:400] + f"\n   … [+{len(opening)-400} chars, full text in utterance.txt]"
        print(f"   paste ▸ {first}")
        for reply in case.replies:
            print(f"   then  ▸ {reply}")
        print()

    print("When they are all done:")
    print(f"   python {Path(__file__).name} collect\n")


def collect(root: Path) -> int:
    results: list[CaseResult] = []
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

        judge(case, result)
        results.append(result)

    if not results:
        raise SystemExit(f"error: nothing to collect under {root}")

    payload = {
        "host": "claude-code",
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
