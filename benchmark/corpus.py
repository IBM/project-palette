"""The input corpus: real Palette plans, used as the material a user pastes.

The documents live **outside the repository**, at `$PALETTE_BENCH_INPUTS`. They
came out of real Palette use — all-hands decks, a Q3 business review, a
competitive positioning brief, a platform architecture, a hackathon kickoff,
benchmark results — and they are internal material, which is why they are not
checked in and why the path is yours to choose:

    export PALETTE_BENCH_INPUTS=~/palette-benchmark-inputs

Unset, every corpus case raises at import with that message rather than
silently producing a run over nothing. A benchmark that quietly measures an
empty dataset is worse than one that refuses to start.

They are already in Palette's plan format (`# Title`, `Audience:`,
`Preferences:`, `## sections`), which makes them useful in two different ways:

**As pasted material.** A user drops a document into the chat and says "turn
this into a deck". The agent has to route the text to `--context` rather than
retyping it into `--request`, and the plan that comes back has to keep the
document's figures. This is the common case and most cases here use it.

**As a plan directly.** Because they *are* plans, `build-deck` can render them
without a planning call at all. That isolates the render path from the planning
path: if a deck built this way is wrong, the fault is in rendering, not in how
the agent phrased the request.

Nothing here is generated. Editing a document changes what the benchmark
measures, which is the point — drop your own in and add cases that reference
them by filename.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The environment variable naming the corpus directory.
INPUTS_ENV = "PALETTE_BENCH_INPUTS"


class CorpusNotConfigured(RuntimeError):
    """Raised with the variable to set and what it should point at."""


def inputs_dir() -> Path:
    """Where the documents are. Raises rather than guessing.

    There is deliberately no default and no fallback to a directory inside the
    repo. A corpus that silently resolves to an empty folder produces a run of
    thirteen cases over nothing and reports it as a result.
    """
    raw = os.environ.get(INPUTS_ENV, "").strip()
    if not raw:
        raise CorpusNotConfigured(
            f"${INPUTS_ENV} is not set, so the benchmark's input documents "
            f"cannot be found.\n\n"
            f"    export {INPUTS_ENV}=/path/to/your/benchmark-inputs\n\n"
            f"It should hold the .md documents the corpus cases name "
            f"(all_hands.md, ibm_q3_review.md, …). They are not in this "
            f"repository on purpose."
        )
    directory = Path(raw).expanduser()
    if not directory.is_dir():
        raise CorpusNotConfigured(
            f"${INPUTS_ENV}={directory} is not a directory."
        )
    return directory


def read(name: str) -> str:
    """The text of an input document, by filename."""
    directory = inputs_dir()
    path = directory / name
    if not path.is_file():
        available = ", ".join(sorted(p.name for p in directory.glob("*.md")))
        raise FileNotFoundError(
            f"no input {name!r} in {directory}. Available: {available or '(none)'}"
        )
    return path.read_text(encoding="utf-8")


def path_to(name: str) -> Path:
    directory = inputs_dir()
    if not (directory / name).is_file():
        raise FileNotFoundError(f"no input {name!r} in {directory}")
    return directory / name


def documents() -> list[Path]:
    return sorted(inputs_dir().glob("*.md"))


def declared_slides(text: str) -> int | None:
    """The slide count a plan asks for, from its `Length:` preference.

    Palette plans state their own length ("- Length: 5 slides"). When a case
    feeds a plan straight to `build-deck`, that is the number the rendered deck
    should have — read from the document rather than restated in the case, so
    editing the document keeps the expectation true.
    """
    import re

    match = re.search(r"^[-\s]*Length:\s*(\d+)\s*slides?", text, re.M | re.I)
    if match:
        return int(match.group(1))
    match = re.search(r"\[\[\s*(\d+)\s*slide", text, re.I)
    return int(match.group(1)) if match else None


def sections(text: str) -> int:
    """How many `## ` sections a plan has — its slide count, roughly."""
    return sum(1 for line in text.splitlines() if line.startswith("## "))
