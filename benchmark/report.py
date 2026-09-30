#!/usr/bin/env python3
"""One report, across every host, as Markdown.

    python benchmark/report.py                  # newest run per host
    python benchmark/report.py --run <dir>      # every host inside one run
    python benchmark/report.py --out FILE       # default: benchmark/runs/REPORT.md

`show.py` reads one run in depth and `compare.py` prints a table to a terminal.
This writes the thing you actually send someone: the scores, the metrics behind
them, the disagreements with their reasons, and — deliberately last but never
omitted — what the numbers are not entitled to say.

Everything here is computed from the recorded reports. Nothing is restated from
memory, so a claim in this file can always be traced to a `palette-calls.jsonl`.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parent
sys.path.append(str(BENCHMARK_DIR))

# The same discovery the comparison table uses. Two tools disagreeing about
# which run is current would be its own small nightmare.
from compare import KNOWN_HOSTS, RUNS, find_reports  # noqa: E402


def _run_label(report: Path) -> str:
    """The timestamped run directory, whichever depth the host writes at."""
    parent = report.parent
    return parent.parent.name if parent.name in KNOWN_HOSTS else parent.name

#: Above this the judge fails a case: every poll is a model round trip out of a
#: finite step budget. Reported as a margin, because passing at the ceiling and
#: passing comfortably are different facts.
POLL_LIMIT = 12


def _polls(case: dict) -> int:
    return sum(
        1 for c in case["palette_calls"] if c.get("command") in {"status", "plan-status"}
    )


def _commands(case: dict) -> list[str]:
    return [c.get("command", "?") for c in case["palette_calls"]]


def _used_context(case: dict) -> bool:
    return any((c.get("args") or {}).get("context") for c in case["palette_calls"])


def host_metrics(report: dict) -> dict:
    """Everything measurable about one host's run."""
    results = report["results"]
    timings = [r["seconds"] for r in results if isinstance(r.get("seconds"), (int, float))]
    polls = [_polls(r) for r in results]
    calls = [len(r["palette_calls"]) for r in results]

    context_cases = [r for r in results if "context" in (r.get("tags") or [])]
    edit_cases = [r for r in results if "edit" in (r.get("tags") or [])]

    return {
        "passed": report["passed"],
        "cases": report["cases"],
        "decks": sum(1 for r in results if r.get("pptx")),
        "plex": sum(1 for r in results if r.get("ibm_plex")),
        "median_seconds": statistics.median(timings) if timings else None,
        "total_seconds": sum(timings) if timings else None,
        "max_polls": max(polls) if polls else 0,
        "total_polls": sum(polls),
        "at_poll_ceiling": sum(1 for p in polls if p >= POLL_LIMIT),
        "median_calls": statistics.median(calls) if calls else 0,
        "context_ok": sum(1 for r in context_cases if _used_context(r)),
        "context_total": len(context_cases),
        "edit_ok": sum(1 for r in edit_cases if "edit" in _commands(r)),
        "edit_total": len(edit_cases),
        "replanned": sum(
            1 for r in edit_cases
            if "edit" in _commands(r) and "plan" in _commands(r)[_commands(r).index("edit"):]
        ),
    }


def render(reports: list[dict]) -> str:
    hosts = [r["host"] for r in reports]
    metrics = {r["host"]: host_metrics(r) for r in reports}
    out: list[str] = []
    w = out.append

    w("# Palette skill benchmark — results\n")
    w("Every host below ran the same cases and was scored by the same "
      "`judge()` in `benchmark/verdict.py`, which reads the filesystem and the "
      "call trace rather than anything the agent said.\n")

    # ---------------------------------------------------------------- scores
    w("## Scores\n")
    w("| Host | Score | Model | Scaffold notes | Run |")
    w("|---|---|---|---|---|")
    for r in reports:
        m = metrics[r["host"]]
        notes = " · ".join(
            str(r[k]) for k in ("provider", "skill_loading") if r.get(k)
        ) or "—"
        w(f"| `{r['host']}` | **{m['passed']}/{m['cases']}** | "
          f"`{r.get('model', 'unknown')}` | {notes} | `{_run_label(r['_report'])}` |")
    w("")

    models = {r.get("model", "unknown") for r in reports}
    if len(models) > 1:
        w("> **The hosts did not all run the same model.** Differences below are "
          "scaffold *and* model. Only hosts sharing a model can be compared as "
          "scaffolds.\n")

    # --------------------------------------------------------------- per case
    w("## Case by case\n")
    by_case: dict[str, dict[str, dict]] = {}
    for r in reports:
        for case in r["results"]:
            by_case.setdefault(case["name"], {})[r["host"]] = case

    w("| Case | What it catches | " + " | ".join(f"`{h}`" for h in hosts) + " |")
    w("|---|---|" + "---|" * len(hosts))
    for name in sorted(by_case):
        row = by_case[name]
        covers = next((c.get("covers", "") for c in row.values() if c.get("covers")), "")
        cells = []
        for host in hosts:
            case = row.get(host)
            if case is None:
                cells.append("—")
                continue
            mark = "✅" if case["ok"] else "❌"
            cells.append(f"{mark} {case.get('slides') or '?'} sl · {_polls(case)}p")
        w(f"| `{name}` | {covers} | " + " | ".join(cells) + " |")
    w("")
    w("*`sl` = slides in the rendered deck, `p` = status polls "
      f"(the judge fails a case above {POLL_LIMIT}).*\n")

    # ---------------------------------------------------------- disagreements
    disagreements = [
        (name, row) for name, row in sorted(by_case.items())
        if len({c["ok"] for c in row.values()}) > 1
    ]

    # With one host there is nothing to disagree *with*, and the comparison
    # framing actively misleads: it reported "every host agreed" over a run
    # with three failures in it, and listed none of them. Failures are the
    # point of a single-host report.
    if len(hosts) == 1:
        only = hosts[0]
        failed = [(n, row[only]) for n, row in sorted(by_case.items())
                  if not row[only]["ok"]]
        w("## Failures\n")
        if not failed:
            w("None. Every case passed.\n")
        else:
            w(f"{len(failed)} of {len(by_case)} cases.\n")
            for name, case in failed:
                w(f"### `{name}`\n")
                if case.get("covers"):
                    w(f"*{case['covers']}*\n")
                for failure in case["failures"]:
                    w(f"- {failure}")
                w(f"- calls: `{' → '.join(_commands(case)) or 'none recorded'}`")
                w("")
    else:
        w("## Where the hosts disagree\n")
        if not disagreements:
            w("Nowhere. Every case reached the same verdict on every host that "
              "ran it — so nothing in this run distinguishes the scaffolds, and "
              "a larger case set is the way to get signal.\n")
        else:
            w(f"{len(disagreements)} of {len(by_case)} cases. These are the only "
              "rows that say anything about the hosts; the rest agree.\n")
            for name, row in disagreements:
                w(f"### `{name}`\n")
                for host, case in row.items():
                    if case["ok"]:
                        w(f"- **{host}** — passed.")
                    else:
                        for failure in case["failures"]:
                            w(f"- **{host}** — {failure}")
                w("")

    # --------------------------------------------------------------- metrics
    w("## Metrics\n")
    w("| | " + " | ".join(f"`{h}`" for h in hosts) + " |")
    w("|---|" + "---|" * len(hosts))

    def row(label: str, fn) -> None:
        w(f"| {label} | " + " | ".join(fn(metrics[h]) for h in hosts) + " |")

    row("Cases passed", lambda m: f"{m['passed']}/{m['cases']}")
    row("Decks built", lambda m: str(m["decks"]))
    row("Decks carrying IBM Plex", lambda m: f"{m['plex']}/{m['decks']}" if m["decks"] else "—")
    row("Median case duration",
        lambda m: f"{m['median_seconds']:.0f}s" if m["median_seconds"] else "not recorded")
    row("Total wall clock",
        lambda m: f"{m['total_seconds'] / 60:.0f} min" if m["total_seconds"] else "not recorded")
    row("Median Palette calls per case", lambda m: f"{m['median_calls']:.0f}")
    row("Status polls (total)", lambda m: str(m["total_polls"]))
    row("Worst case polls", lambda m: f"{m['max_polls']} / {POLL_LIMIT}")
    row("Cases at the poll ceiling",
        lambda m: str(m["at_poll_ceiling"]) if m["at_poll_ceiling"] else "0")
    row("Pasted material reached `--context`",
        lambda m: f"{m['context_ok']}/{m['context_total']}" if m["context_total"] else "—")
    row("Edits routed to `edit-plan`",
        lambda m: f"{m['edit_ok']}/{m['edit_total']}" if m["edit_total"] else "—")
    row("Revisions discarded by re-planning",
        lambda m: str(m["replanned"]) if m["edit_total"] else "—")
    w("")

    # ------------------------------------------------------------- the traces
    w("## What each host actually asked Palette for\n")
    w("The agent's own account of this is a paraphrase; these are the recorded "
      "calls.\n")
    for name in sorted(by_case):
        w(f"**`{name}`**\n")
        for host in hosts:
            case = by_case[name].get(host)
            if case is None:
                w(f"- `{host}` — did not run")
                continue
            w(f"- `{host}` — `{' → '.join(_commands(case)) or 'no calls recorded'}`")
        w("")

    # ------------------------------------------------------------- the caveats
    w("## What these numbers are not\n")
    w(f"- **One run per cell, no repeats.** A one-case difference is a reason to "
      f"look, not a ranking.")
    if len(models) > 1:
        w("- **Not a like-for-like comparison across all hosts.** Where the model "
          "differs, the column answers *which product builds better decks*, not "
          "*which scaffold drives the skill better*.")
    incomplete = [n for n, row in by_case.items() if len(row) < len(hosts)]
    if incomplete:
        w(f"- **{len(incomplete)} case(s) did not run on every host**: "
          + ", ".join(f"`{n}`" for n in sorted(incomplete)) + ".")
    if not disagreements:
        w("- **No discriminating power in this case set.** Every host agreed, so "
          "these cases cannot tell them apart.")
    else:
        w(f"- **Discriminating power is {len(disagreements)} of {len(by_case)} "
          f"cases.** The rest agree and cost the same time to run.")
    w("")

    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--run", help="one run directory (default: newest per host)")
    parser.add_argument("--out", default=str(RUNS / "REPORT.md"))
    parser.add_argument("--stdout", action="store_true", help="print instead of writing")
    args = parser.parse_args(argv)

    reports = find_reports(Path(args.run).expanduser().resolve() if args.run else None)
    if not reports:
        raise SystemExit(f"error: no reports found under {RUNS}")

    text = render(reports)
    if args.stdout:
        print(text)
        return 0

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out}  ({len(text.splitlines())} lines, "
          f"{len(reports)} host(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
