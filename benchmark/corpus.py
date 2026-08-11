"""The input corpus: real Palette plans, used as the material a user pastes.

`benchmark/inputs/` holds thirteen documents that came out of real Palette use —
all-hands decks, a Q3 business review, a competitive positioning brief, a
platform architecture, a hackathon kickoff, benchmark results. They are already
in Palette's plan format (`# Title`, `Audience:`, `Preferences:`, `## sections`),
which makes them useful in two different ways:

**As pasted material.** A user drops a document into the chat and says "turn
this into a deck". The agent has to route the text to `--context` rather than
retyping it into `--request`, and the plan that comes back has to keep the
document's figures. This is the common case and most cases here use it.

**As a plan directly.** Because they *are* plans, `build-deck` can render them
without a planning call at all. That isolates the render path from the planning
path: if a deck built this way is wrong, the fault is in rendering, not in how
the agent phrased the request.

Nothing here is generated. Editing a file under `inputs/` changes what the
benchmark measures, which is the point — drop your own documents in and add
cases that reference them.
"""

from __future__ import annotations

from pathlib import Path

INPUTS = Path(__file__).resolve().parent / "inputs"


def read(name: str) -> str:
    """The text of an input document, by filename."""
    path = INPUTS / name
    if not path.is_file():
        available = ", ".join(sorted(p.name for p in INPUTS.glob("*.md")))
        raise FileNotFoundError(f"no input {name!r} in {INPUTS}. Available: {available}")
    return path.read_text(encoding="utf-8")


def path_to(name: str) -> Path:
    if not (INPUTS / name).is_file():
        raise FileNotFoundError(f"no input {name!r} in {INPUTS}")
    return INPUTS / name


def documents() -> list[Path]:
    return sorted(INPUTS.glob("*.md"))


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
