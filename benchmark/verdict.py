"""What counts as a pass — one definition, for every host.

This is the whole reason the benchmark's numbers can be compared. Three hosts
run the same conversations; if each decided for itself what "worked" meant, the
columns would be three different measurements printed in one table.

It lived inside `run.py` until there were three hosts, which meant the Claude
and ReAct runners imported their verdict *from the CUGA runner* — working, but
inverted, and one stray module-level import in `run.py` away from breaking both.

**The judge never trusts the agent.** Everything here is read from the
filesystem or from the call trace. An agent that says it built a deck and did
not is the exact failure this file exists to catch, and it has happened: a run
reported a real 3-slide deck alongside "palette calls: (none)".

A host has to satisfy four things, and then `judge()` scores it like any other:

  1. write `input/` beside `output/`, so the question is reproducible
  2. set `$PALETTE_TRACE` per case, so `deck.py` records what it was asked
  3. drive the opening message, then one reply per time the agent yields
  4. call `judge(case, result)` — this one, not a local equivalent
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Below this a .pptx is a stub, not a deck. A failed render can still leave a
#: small well-formed file behind, and reporting that as success is the failure
#: this threshold exists to prevent.
MIN_PPTX_BYTES = 20_000

#: Each poll is a model round trip out of a finite step budget. A run that spent
#: forty of them was one step limit from failing, and the deck it produced tells
#: you nothing about how close it came.
MAX_POLLS = 12


@dataclass
class TurnRecord:
    sent: str
    answer: str
    seconds: float
    error: str | None = None


@dataclass
class CaseResult:
    #: The `Case` this scores. Untyped to keep this module importable without
    #: the case set — the judge only reads attributes off it.
    case: Any
    turns: list = field(default_factory=list)
    palette_calls: list[dict] = field(default_factory=list)
    pptx: Path | None = None
    slides: int = 0
    has_plex: bool = False
    seconds: float = 0.0
    failures: list[str] = field(default_factory=list)
    workspace: Path | None = None

    @property
    def ok(self) -> bool:
        return not self.failures

    def commands(self) -> list[str]:
        return [c.get("command", "?") for c in self.palette_calls]


# ------------------------------------------------------------------ the artifact


def inspect_deck(pptx: Path) -> tuple[int, bool]:
    """(slide count, carries IBM Plex). Plex is what proves Palette rendered it.

    The renderer forces the font, so a deck hand-written with pptxgenjs or
    python-pptx cannot have it. This is the check that separates "a deck exists"
    from "Palette made this".
    """
    try:
        with zipfile.ZipFile(pptx) as archive:
            slides = [n for n in archive.namelist() if n.startswith("ppt/slides/slide")]
            first = archive.read("ppt/slides/slide1.xml").decode("utf-8", "replace")
        return len(slides), "IBM Plex" in first
    except (OSError, zipfile.BadZipFile, KeyError):
        return 0, False


def newest_deck(workspace: Path) -> Path | None:
    """The most recent real deck under a case workspace, or None."""
    decks = [p for p in workspace.rglob("deck.pptx") if p.stat().st_size >= MIN_PPTX_BYTES]
    return max(decks, key=lambda p: p.stat().st_mtime) if decks else None


# ----------------------------------------------------------------- the verdict


def judge(case, result: CaseResult) -> None:  # noqa: ANN001 - see CaseResult.case
    """Decide from disk, never from what the agent claimed."""
    if not case.expect_deck:
        if result.pptx is not None:
            result.failures.append(
                "a deck was built although the user never approved it — "
                "the confirmation gate was skipped"
            )
        return

    if result.pptx is None:
        result.failures.append("no .pptx was produced")
        return
    if not result.has_plex:
        result.failures.append(
            "the .pptx does not carry IBM Plex — it was not rendered by Palette"
        )
    if case.expect_slides is not None and result.slides != case.expect_slides:
        result.failures.append(
            f"asked for {case.expect_slides} slides, got {result.slides}"
        )
    if "edit" in case.tags:
        commands = result.commands()
        if "edit" not in commands:
            result.failures.append(
                "the user asked for a change but edit-plan was never called "
                f"(calls: {', '.join(commands) or 'none'})"
            )
        elif "plan" in commands[commands.index("edit") :]:
            # Calling edit and then re-planning throws the revision away and
            # pays for a second plan. Seen once, and it passed the old check
            # because `edit` did appear in the trace.
            result.failures.append(
                "edit-plan was called and then the plan was drafted again from "
                "scratch — the revision the user approved was discarded"
            )

    polls = sum(1 for c in result.commands() if c in {"plan-status", "status"})
    if polls > MAX_POLLS:
        result.failures.append(
            f"{polls} status polls — each is a model round trip, and the step "
            f"budget is finite. The commands should be holding, not spinning"
        )
    if case.context and not any(
        c.get("args", {}).get("context") for c in result.palette_calls
    ):
        result.failures.append(
            "pasted material was never passed as --context; it was probably "
            "retyped into the request, which loses the grounding"
        )
