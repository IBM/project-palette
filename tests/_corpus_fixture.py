"""A stand-in corpus, so the test suite needs no copy of the real documents.

`benchmark/cases.py` reads its corpus documents at **import time**, and the
directory is now `$PALETTE_BENCH_INPUTS` — deliberately outside the repository,
because those documents are internal material. Without something here, importing
`cases` on a machine that has not set that variable fails, and every test in
every file that touches the benchmark fails with it.

So: if the variable is already set, tests run against the real corpus. If it is
not, this writes a throwaway document for each filename the cases name, and
points the variable at those. The stubs are generated from `cases.py` itself, so
adding a corpus case does not also mean remembering to add a fixture.

What this deliberately does *not* do is provide a default for the runners. Only
the tests get a stand-in; a real run against an unset variable still refuses to
start, which is the whole point of the change.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES = REPO_ROOT / "benchmark" / "cases.py"

#: Long enough to satisfy the "this is a real document, not a snippet" checks,
#: and shaped like a Palette plan so `sections()` and `declared_slides()` have
#: something true to read.
_STUB = """\
# {title}

Audience: engineers and product leads who need a worked example of this
document shape, written so the parser has something realistic to read rather
than a placeholder string.

Preferences:
- Tone: technical, concise
- Length: 5 slides

## Where this came from
- A stand-in for {name}, generated for the test suite.
- The real corpus lives outside the repository, at $PALETTE_BENCH_INPUTS.
- Nothing here is measured; it exists so imports resolve.

## What it stands in for
- A document a user pastes into the chat in one go.
- Long enough that a case pasting it is unmistakably pasting a document.
- Structured, so section counting has sections to count.

## Why it is generated
- The filenames come from cases.py, so the two cannot drift apart.
- A committed fixture would need updating every time a case is added.

## What a real document holds
- Figures, tables, named systems, and opinions worth grounding a deck in.
- None of which belong in a public repository.

## Closing
- Set $PALETTE_BENCH_INPUTS to measure anything real.
"""


def document_names() -> list[str]:
    """Every filename the cases read, taken from the source rather than a list."""
    source = CASES.read_text(encoding="utf-8")
    return sorted(set(re.findall(r"""read\(\s*["']([^"']+)["']""", source)))


def ensure_corpus() -> Path:
    """Point `$PALETTE_BENCH_INPUTS` at something readable. Returns the directory.

    Called from conftest at import time, before any test module does
    `from cases import CASES`.
    """
    existing = os.environ.get("PALETTE_BENCH_INPUTS", "").strip()
    if existing and Path(existing).expanduser().is_dir():
        return Path(existing).expanduser()

    directory = Path(tempfile.mkdtemp(prefix="palette-bench-inputs-"))
    for name in document_names() or ["placeholder.md"]:
        title = name.removesuffix(".md").replace("_", " ").title()
        (directory / name).write_text(_STUB.format(title=title, name=name), encoding="utf-8")

    os.environ["PALETTE_BENCH_INPUTS"] = str(directory)
    return directory
