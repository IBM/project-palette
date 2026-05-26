"""Stage 1 — intake. A user request becomes a plan.md the Stage-2 designer
consumes. One unified path handles all three input situations: a vague
request, a request plus source documents, or an already-rough plan.

Source documents (PDF / DOCX / PPTX) are parsed with docling — IBM Research's
layout-aware document converter — which yields clean Markdown.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import config
import llm
from harness_prompts import CRAFTER_SYSTEM_PROMPT, build_crafter_user_message

log = logging.getLogger("intake")

# Curated real training plans (reference_plans/) shown to the crafter as
# format + density exemplars. Chosen to span the range and, deliberately, to
# cover the hard high-value cases: multi-visual slides, matrix tables, and
# layered architecture diagrams.
EXEMPLAR_NAMES = [
    "multiviz_readout.md",        # multi-visual slides; bar / line / donut / funnel
    "competitive_overview.md",    # matrix table, 2x2 quadrant, brief-style discipline
    "architecture_reference.md",  # layered architecture, components, request flow
    "kafka_fundamentals.md",      # code blocks, stacked layers, definition
    "stripe_board_update.md",     # terse business deck -- stats, comparison
]

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n?\s*```\s*$", re.DOTALL)

# The crafter is told to use plain ASCII, but the model still emits the odd
# typographic dash / curly quote. Those corrupt downstream (the `?`/`�`
# bug), so normalize deterministically rather than trusting the instruction.
_ASCII_MAP = str.maketrans({
    "–": "-", "—": "-", "‒": "-", "‐": "-", "‑": "-",
    "―": "-", "−": "-",
    "“": '"', "”": '"', "„": '"', "‘": "'",
    "’": "'", "‚": "'",
    "…": "...", "•": "-", " ": " ", "×": "x",
})


_ASCII_MAP.update(str.maketrans({"≈": "~", "≠": "!=", "±": "+/-"}))


def _strip_fence(text: str) -> str:
    m = _FENCE.match(text.strip())
    return m.group(1).strip() if m else text.strip()


def _normalize_ascii(text: str) -> str:
    """Fold typographic punctuation the crafter occasionally emits down to
    plain ASCII (en/em dashes, curly quotes, ellipsis) — those do not survive
    the designer -> coder -> render pipeline cleanly."""
    out = text.translate(_ASCII_MAP)
    out = out.replace("≥", ">=").replace("≤", "<=")
    out = out.replace("→", "->")
    return out


def _load_exemplars() -> list[str]:
    out: list[str] = []
    for name in EXEMPLAR_NAMES:
        p = config.REFERENCE_PLANS / name
        if p.is_file():
            out.append(p.read_text())
    return out


def parse_document(path: Path) -> tuple[str, str]:
    """Any supported document -> (filename, markdown text).

    PDF / DOCX / PPTX go through docling (layout-aware, outputs Markdown with
    reading order and tables preserved). Plain .md / .txt are read directly.
    """
    path = Path(path)
    if path.suffix.lower() in (".md", ".txt"):
        return path.name, path.read_text(errors="replace")
    from docling.document_converter import DocumentConverter
    result = DocumentConverter().convert(str(path))
    return path.name, result.document.export_to_markdown()


def craft_plan(request: str, source_paths: list[Path] | None = None) -> str:
    """Turn a request (plus any uploaded source documents) into a plan.md."""
    source_texts: list[tuple[str, str]] = []
    for sp in source_paths or []:
        try:
            source_texts.append(parse_document(sp))
            log.info("parsed source: %s", Path(sp).name)
        except Exception as e:  # noqa: BLE001 — a bad upload shouldn't abort
            log.warning("failed to parse %s: %s", sp, e)

    messages = [
        {"role": "system", "content": CRAFTER_SYSTEM_PROMPT},
        {"role": "user", "content": build_crafter_user_message(
            request, source_texts, _load_exemplars())},
    ]
    content, _ = llm.chat(config.ROSTER["crafter"], messages)
    plan = _normalize_ascii(_strip_fence(content))
    log.info("crafted plan: %d chars, %d slide section(s)",
             len(plan), plan.count("\n## "))
    return plan
