"""Stage-2 check against the designer/coder endpoint — palette's own code path.

Runs pipeline.run_designer on a plan, then codes the first N slides with
pipeline._code_one_slide in parallel, exactly as a build does (config.ROSTER,
llm.chat, retries, parsing). No render: no Node or LibreOffice needed, and
only designer/coder are called, so RITS_API_KEY is not needed when the
redirect is on.

  export PALETTE_CE_BASE_URL=http://<palette-lb-hostname>/v1
  export PALETTE_CE_API_KEY=$(cat ../palette-model-fleet/.serve_api_key)
  uv run python scripts/ce_stage2_check.py [plan.md] [--slides N]

Unset PALETTE_CE_BASE_URL (and set RITS_API_KEY) to run the same check on RITS.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config  # noqa: E402  (logs the CE banner when the redirect is on)
import pipeline  # noqa: E402

DEFAULT_PLAN = config.REFERENCE_PLANS / config.USER_FACING_EXAMPLES[0][0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("plan", nargs="?", type=Path, default=DEFAULT_PLAN)
    ap.add_argument("--slides", type=int, default=3,
                    help="how many slides to code (0 = all)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    # full request/response bodies — far too verbose for a terminal check
    logging.getLogger("llm.responses").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    spec = config.ROSTER["designer"]
    print(f"backend: {spec.base_url or 'RITS ' + spec.slug}  model: {spec.payload_model}")
    print(f"plan:    {args.plan}")

    t0 = time.time()
    raw, deck, attempts = pipeline.run_designer(
        args.plan.read_text(), config.available_icons(), "ibm_watsonx")
    if deck is None:
        print(f"FAIL designer: no parseable brief after {attempts} attempts\n{raw[:1500]}")
        return 1
    slides = deck.get("slides", [])
    print(f"OK   designer: {len(slides)} slides in {time.time() - t0:.0f}s "
          f"(attempt {attempts}) — {deck.get('deck_title')!r}")

    todo = slides if args.slides == 0 else slides[:args.slides]
    t0 = time.time()
    titles = [s.get("title", "") for s in slides]
    with cf.ThreadPoolExecutor(len(todo) or 1) as ex:
        results = list(ex.map(
            lambda s: pipeline._code_one_slide(deck, s, titles[:slides.index(s)]),
            todo))
    failed = 0
    for n, _trace, code, att in results:
        ok = bool(code and code.strip())
        failed += not ok
        print(f"{'OK  ' if ok else 'FAIL'} coder slide {n}: {len(code or '')} chars JS "
              f"(attempt {att})")
    print(f"coded {len(todo)} slides in {time.time() - t0:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
