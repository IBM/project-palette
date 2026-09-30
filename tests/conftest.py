"""Test-wide setup.

The only thing that has to happen before anything else is the corpus: cases.py
reads its documents when it is imported, so `$PALETTE_BENCH_INPUTS` must resolve
before the first `from cases import CASES`. That is why this runs at module
level rather than in a fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _corpus_fixture import ensure_corpus  # noqa: E402

CORPUS = ensure_corpus()
