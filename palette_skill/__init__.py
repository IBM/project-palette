"""Palette as an agent skill — a thin, dependency-light client.

Import this in an agent sandbox to drive the Palette deck builder over HTTP:

    from palette_skill import PaletteClient

    pal = PaletteClient()                      # PALETTE_URL, or the default
    plan = pal.draft("Deck on RAG for backend engineers")
    build = pal.build_and_wait(plan)           # or start_build / progress / result
    pptx = pal.download(build.thread_id, "./deck.pptx")

The heavy pipeline — pptxgenjs, LibreOffice, Poppler, the RITS model roster —
lives on the Palette server. Nothing here needs any of it.
"""

from __future__ import annotations

__version__ = "0.1.0"

from palette_skill.client import (
    BuildOutcome,
    PaletteClient,
    PaletteError,
    PaletteHTTPError,
    PaletteTimeout,
    PaletteUnavailable,
    Progress,
)

__all__ = [
    "__version__",
    "BuildOutcome",
    "PaletteClient",
    "PaletteError",
    "PaletteHTTPError",
    "PaletteTimeout",
    "PaletteUnavailable",
    "Progress",
]
