#!/usr/bin/env python3
"""Put the hosts side by side.

    python benchmark/compare.py                    # newest run of each host
    python benchmark/compare.py --run <dir>        # every host inside one run
    python benchmark/compare.py --hosts cuga react

`show.py` reads one run in depth — every call, every turn. This answers the
other question: *where do the hosts disagree*. One row per case, one column per
host, and a note under any row where they did not reach the same verdict, since
that row is the only kind that tells you something about the scaffold.

It reports what each host actually ran, too. The columns are only comparable
when the model is the same; when it is not, that is the first thing you need to
know and the easiest thing to forget.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parent
RUNS = BENCHMARK_DIR / "runs"

#: Report locations differ by host: the CUGA runner writes to the top of a run,
#: the per-host runners write under <run>/<host>/.
KNOWN_HOSTS = ("cuga", "claude", "react")


def _load(report: Path) -> dict | None:
    try:
        payload = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    payload["_report"] = report
    # The CUGA runner did not always record a host; infer it from the path.
    if not payload.get("host"):
        payload["host"] = report.parent.name if report.parent.name in KNOWN_HOSTS else "cuga"
    return payload


def find_reports(run: Path | None) -> list[dict]:
    """Every report under `run`, or the newest one per host across all runs."""
    if run is not None:
        found = [_load(p) for p in sorted(run.glob("*/report.json"))]
        found.append(_load(run / "report.json"))
        return [r for r in found if r]

    newest: dict[str, dict] = {}
    for report in RUNS.glob("*/report.json"):
        payload = _load(report)
        if payload:
            newest.setdefault(payload["host"], payload)
    for report in RUNS.glob("*/*/report.json"):
        payload = _load(report)
        if payload:
            newest.setdefault(payload["host"], payload)

    # setdefault above keeps whichever came first; redo it properly by mtime.
    best: dict[str, dict] = {}
    for pattern in ("*/report.json", "*/*/report.json"):
        for report in RUNS.glob(pattern):
            payload = _load(report)
            if not payload:
                continue
            host = payload["host"]
            if host not in best or report.stat().st_mtime > best[host]["_report"].stat().st_mtime:
                best[host] = payload
    return [best[h] for h in sorted(best, key=lambda h: KNOWN_HOSTS.index(h) if h in KNOWN_HOSTS else 99)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--run", help="one run directory (default: newest per host)")
    parser.add_argument("--hosts", nargs="*", help="limit to these hosts")
    args = parser.parse_args(argv)

    reports = find_reports(Path(args.run).expanduser().resolve() if args.run else None)
    if args.hosts:
        reports = [r for r in reports if r["host"] in set(args.hosts)]
    if not reports:
        raise SystemExit(f"error: no reports found under {RUNS}")

    print("hosts")
    models = set()
    for r in reports:
        model = r.get("model", "unknown")
        models.add(model)
        extra = " · ".join(
            str(r[k]) for k in ("provider", "skill_loading") if r.get(k)
        )
        print(f"  {r['host']:<8} {r['passed']}/{r['cases']}  {model}"
              f"{'  (' + extra + ')' if extra else ''}")
        print(f"           {r['_report'].parent}")

    if len(models) > 1:
        # The whole point of the third host is holding the model fixed. Saying so
        # here is cheaper than someone reading a scaffold conclusion off a table
        # where the model also changed.
        print("\n  ! the hosts did not run the same model, so differences below "
              "are model *and* scaffold")

    by_case: dict[str, dict[str, dict]] = {}
    for r in reports:
        for case in r["results"]:
            by_case.setdefault(case["name"], {})[r["host"]] = case

    hosts = [r["host"] for r in reports]
    width = max((len(n) for n in by_case), default=4)
    print(f"\n{'case':<{width}}  " + "  ".join(f"{h:<16}" for h in hosts))
    print("-" * (width + 18 * len(hosts)))

    disagreements = 0
    for name in sorted(by_case):
        row = by_case[name]
        cells = []
        for host in hosts:
            case = row.get(host)
            if case is None:
                cells.append(f"{'—':<16}")
                continue
            mark = "pass" if case["ok"] else "FAIL"
            slides = case.get("slides") or "-"
            # Not every host records timing: the Claude runner collects decks a
            # person produced, so there is no per-case duration to report. Show
            # what exists rather than refusing to draw the table.
            seconds = case.get("seconds")
            timing = f"{seconds:>5.0f}s" if isinstance(seconds, (int, float)) else "    —"
            cells.append(f"{mark} {slides:>2}sl {timing}")
        print(f"{name:<{width}}  " + "  ".join(f"{c:<16}" for c in cells))

        verdicts = {h: row[h]["ok"] for h in hosts if h in row}
        if len(set(verdicts.values())) > 1:
            disagreements += 1
            for host, case in row.items():
                for failure in case["failures"]:
                    print(f"{'':<{width}}    {host}: {failure}")

    print("-" * (width + 18 * len(hosts)))
    covered = [n for n in by_case if len(by_case[n]) == len(hosts)]
    print(f"{len(by_case)} case(s) · {len(covered)} run on every host · "
          f"{disagreements} disagreement(s)")
    if disagreements == 0 and len(hosts) > 1:
        print("the hosts agreed everywhere they overlapped — nothing here "
              "distinguishes the scaffolds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
