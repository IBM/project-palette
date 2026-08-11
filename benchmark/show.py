#!/usr/bin/env python3
"""Read a benchmark run back: what Palette was asked, and what came out.

    python benchmark/show.py                 # the newest run
    python benchmark/show.py --run <dir>
    python benchmark/show.py --case edit_slide_count --verbose

The report is JSON so it can be diffed between runs; this renders it for a
person. The palette calls are the part worth reading — an agent's account of
what it asked for is a paraphrase, and this is the real thing.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

RUNS = Path(__file__).resolve().parent / "runs"


def newest_run() -> Path:
    runs = [p for p in RUNS.glob("*") if (p / "report.json").is_file()]
    if not runs:
        raise SystemExit(f"error: no completed run under {RUNS}")
    return max(runs, key=lambda p: p.stat().st_mtime)


def clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", help="a run directory (default: the newest)")
    parser.add_argument("--case", action="append", help="only these cases")
    parser.add_argument("--failures", action="store_true", help="only cases that failed")
    parser.add_argument("--verbose", action="store_true", help="include the conversation")
    args = parser.parse_args(argv)

    run = Path(args.run).expanduser().resolve() if args.run else newest_run()
    report = json.loads((run / "report.json").read_text(encoding="utf-8"))

    results = report["results"]
    if args.case:
        wanted = set(args.case)
        results = [r for r in results if r["name"] in wanted]
    if args.failures:
        results = [r for r in results if not r["ok"]]

    print(f"run: {run}")
    print(f"model: {report['model']} · {report['passed']}/{report['cases']} passed\n")

    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        print(f"── {mark}  {r['name']}  ({r['seconds']:.0f}s)")
        print(f"   covers: {r['covers']}")
        for failure in r["failures"]:
            print(f"   ✗ {failure}")
        if r["pptx"]:
            print(f"   deck: {r['slides']} slides, IBM Plex={r['ibm_plex']}")
            print(f"         {r['pptx']}")
        else:
            print("   deck: none")

        print("   palette calls:")
        if not r["palette_calls"]:
            print("     (none — the skill was never driven)")
        for call in r["palette_calls"]:
            args_shown = {
                k: clip(str(v), 90)
                for k, v in (call.get("args") or {}).items()
                if k not in {"hold_seconds", "wait"}
            }
            print(f"     {call['command']:<12} {call.get('seconds', 0):>6.1f}s  {args_shown}")
            if call.get("stdout"):
                print(f"     {'':<12} → {clip(call['stdout'], 120)}")

        if args.verbose:
            print("   conversation:")
            for turn in r["turns"]:
                print(f"     user  ▸ {clip(turn['sent'], 100)}")
                print(f"     agent ◂ {clip(turn['answer'], 200)}")
                if turn.get("error"):
                    print(f"     error ! {turn['error']}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
