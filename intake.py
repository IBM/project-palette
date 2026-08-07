"""Stage 1 — intake. A user request becomes a plan.md the Stage-2 designer
consumes. One unified path handles all three input situations: a vague
request, a request plus source documents, or an already-rough plan.

Source documents are parsed with lightweight per-type extractors: python-docx
for DOCX, pdfplumber for PDF, python-pptx for PPTX (slide-aware). Plain
.md / .txt are read directly. These keep the deployed container lean (no
torch / layout models) at the cost of layout-model table parsing, which the
prose / business-doc intake case doesn't need.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import config
import llm
from harness_prompts import (
    CRAFTER_SYSTEM_PROMPT,
    TRANSCRIBE_SYSTEM_PROMPT,
    build_crafter_user_message,
    build_plan_edit_user_message,
    build_transcribe_user_message,
)

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

# Paired (source.txt, plan.md) exemplars for the TRANSCRIBE path -- shown to
# the crafter when the user uploads a finished slide deck and wants a 1:1
# plan that regenerates it. Each entry is a subdir under
# reference_plans/transcribe/ containing source.txt + plan.md.
TRANSCRIBE_PAIRS = [
    "01_credit_exception",   # multi-region one-slide (header / 3 circles / 2-col body)
    "02_hero_stat",          # simple one-slide (single hero number)
    "03_quarterly_readout",  # multi-slide deck, one ## per source slide
]

# Source kinds that imply "this is already a deck, transcribe it" rather than
# "this is raw research material, organize it." Add .key here once we test it.
_DECK_SUFFIXES = {".pptx", ".key"}

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


def _load_transcribe_pairs() -> list[tuple[str, str]]:
    """Load (source.txt, plan.md) exemplar pairs for the TRANSCRIBE path. A
    missing file in a pair drops that pair silently -- exemplar curation
    breakage shouldn't abort intake."""
    out: list[tuple[str, str]] = []
    base = config.REFERENCE_PLANS / "transcribe"
    for d in TRANSCRIBE_PAIRS:
        sp = base / d / "source.txt"
        pp = base / d / "plan.md"
        if sp.is_file() and pp.is_file():
            out.append((sp.read_text(), pp.read_text()))
        else:
            log.warning("transcribe exemplar missing: %s", d)
    return out


def _is_deck_source(paths: list[Path]) -> bool:
    """Heuristic: any uploaded .pptx (or .key) triggers transcribe mode. PDFs
    are NOT treated as decks by default -- a research paper looks the same as
    a slide-PDF to the suffix check, so PDFs stay on the research path. A
    user who wants transcribe behaviour on a slide-PDF can convert to PPTX
    or, later, we add a slide-density heuristic."""
    return any(p.suffix.lower() in _DECK_SUFFIXES for p in paths)


def _chart_to_text(chart) -> list[str]:
    """Extract a NATIVE pptx chart's data as text — works for every chart
    family, not just bar.

    Two data models cover all chart types:
      - category charts (bar/column, line, area, pie, doughnut, radar):
        one shared category axis + one or more named series of values.
        Emitted as a header row of categories + one row per series.
      - XY charts (scatter, bubble): no categories; each series is a set of
        (x, y[, size]) points. Emitted as point tuples per series.
    The leading `[chart: <type>]` line names the source shape so the
    transcribe crafter can map it to the right `[[render as a … chart]]`
    treatment (bar→bar chart, line→line chart, pie/doughnut→donut, etc.)."""
    out: list[str] = []
    try:
        ctype = str(chart.chart_type)
    except Exception:  # noqa: BLE001
        ctype = "unknown"
    out.append(f"[chart: {ctype}]")
    try:
        title = chart.chart_title.text_frame.text.strip() if chart.has_title else ""
        if title:
            out.append(f"chart title: {title}")
    except Exception:  # noqa: BLE001
        pass

    # XY / bubble charts have no categories — detect via the plot/series shape.
    is_xy = "XY" in ctype or "SCATTER" in ctype or "BUBBLE" in ctype
    try:
        if is_xy:
            for s in chart.series:
                xs = list(getattr(s, "values", []) or [])
                pts = ", ".join(str(v) for v in xs)
                out.append(f"series {getattr(s, 'name', '') or '?'}: {pts}")
        else:
            cats = []
            if chart.plots:
                cats = [str(c) for c in (chart.plots[0].categories or [])]
            if cats:
                out.append("categories: " + " | ".join(cats))
            for s in chart.series:
                vals = " | ".join(str(v) for v in (list(s.values) or []))
                out.append(f"{getattr(s, 'name', '') or 'series'}: {vals}")
    except Exception as e:  # noqa: BLE001 — a malformed chart shouldn't abort
        out.append(f"(chart data unreadable: {e})")
    return out


def _shape_lines(sh) -> list[str]:
    """Text/data lines for ONE shape. Recurses into groups; extracts charts;
    leaves a placeholder for pictures so a visual-only slide is not silently
    empty. Decorative auto-shapes with no text yield nothing."""
    st = getattr(sh, "shape_type", None)
    # GROUP (6) — python-pptx's slide.shapes does NOT recurse, so a process
    # flow / SmartArt / arrow chain (almost always grouped) would lose ALL its
    # text. Recurse, preserving the same row-band/column ordering inside.
    if st == 6:
        out: list[str] = []
        for child in _ordered(sh.shapes):
            out += _shape_lines(child)
        return out
    if sh.has_text_frame:
        return [ln for para in sh.text_frame.paragraphs
                if (ln := "".join(r.text for r in para.runs).strip())]
    if st == 19:  # TABLE
        return [" | ".join(c.text.strip() for c in row.cells)
                for row in sh.table.rows]
    if st == 3 and getattr(sh, "has_chart", False):  # CHART
        return _chart_to_text(sh.chart)
    if st == 13:  # PICTURE
        # Only a sizeable picture is content worth a placeholder. Logos, icons,
        # and decorative marks are small and typically repeat on every slide
        # (the IBM logo is ~1.0x0.4in) — emitting a placeholder for those puts
        # "[image …]" on every slide, which is noise. A genuine figure /
        # diagram / screenshot occupies real estate, so gate on size.
        w = (sh.width or 0) / 914400.0
        h = (sh.height or 0) / 914400.0
        if w >= 2.5 and h >= 1.5:
            alt = (getattr(sh, "name", "") or "").strip()
            return [f"[image: {alt}]" if alt else "[image]"]
        return []  # logo / icon / decoration → drop
    return []


_GUTTER_EMU = 137160  # ~0.15in — min gap between shapes to count as a cut


def _widest_gap(boxes, axis):
    """Largest gutter along `axis` ('h' = y-gaps, 'v' = x-gaps) and the index
    (into the axis-sorted list) where it splits. A box's running far-edge is
    carried forward so a tall/wide box correctly bridges (suppresses) gaps it
    spans — which is how a full-width title/footer prevents a spurious column
    cut."""
    lo, hi = ("y0", "y1") if axis == "h" else ("x0", "x1")
    ordered = sorted(boxes, key=lambda b: b[lo])
    best_gap, best_idx = 0, None
    running = ordered[0][hi]
    for i, b in enumerate(ordered[1:], 1):
        gap = b[lo] - running
        if gap > best_gap:
            best_gap, best_idx = gap, i
        running = max(running, b[hi])
    return best_gap, best_idx, ordered


def _xy_cut(boxes):
    """Recursive XY-cut reading order: at each step cut at the SINGLE widest
    gutter across both axes (ties → horizontal), then recurse each half. Full-
    width headers/footers bridge column gaps so they peel off as their own
    band first, leaving clean columns underneath — which keeps side-by-side
    content (slide-4 list+code-panel, slide-2 01/02/03 badge columns) read
    column-by-column instead of interleaved."""
    if len(boxes) <= 1:
        return list(boxes)
    gh, ih, oh = _widest_gap(boxes, "h")
    gv, iv, ov = _widest_gap(boxes, "v")
    if max(gh, gv) <= _GUTTER_EMU:             # no real gutter → reading order
        return sorted(boxes, key=lambda b: (b["y0"], b["x0"]))
    if gh >= gv:
        first, second = oh[:ih], oh[ih:]
    else:
        first, second = ov[:iv], ov[iv:]
    return _xy_cut(first) + _xy_cut(second)


def _ordered(shapes):
    """Shapes in human reading order via XY-cut (handles multi-column slides).
    Shapes without a position are dropped (decoration / off-slide)."""
    boxes = []
    for s in shapes:
        if getattr(s, "top", None) is None or getattr(s, "left", None) is None:
            continue
        x0, y0 = int(s.left), int(s.top)
        boxes.append({"x0": x0, "y0": y0,
                      "x1": x0 + int(s.width or 0),
                      "y1": y0 + int(s.height or 0), "sh": s})
    if not boxes:
        return []
    return [b["sh"] for b in _xy_cut(boxes)]


def _pptx_to_text(path: Path) -> str:
    """Walk a PPTX slide-by-slide and emit text with explicit
    `--- slide N ---` boundaries (so the transcribe crafter can count source
    slides). Captures every CONTENT-bearing shape type — text boxes, tables,
    native charts (all families, with data), grouped shapes (recursed), and a
    placeholder for pictures — dropping only decoration. Reading order is
    top-to-bottom, left-to-right by shape position."""
    from pptx import Presentation
    prs = Presentation(str(path))
    parts: list[str] = []
    for i, slide in enumerate(prs.slides, 1):
        parts.append(f"--- slide {i} ---")
        for sh in _ordered(slide.shapes):
            parts += _shape_lines(sh)
        parts.append("")  # blank line between slides
    return "\n".join(parts).strip()


def _docx_to_text(path: Path) -> str:
    """Walk a DOCX in document order and emit text. Headings become `## `
    Markdown so the crafter sees the author's section structure; tables become
    `cell | cell` rows. python-docx (a tiny pure-Python dep) is used rather
    than docling: business / prose docs don't need layout-model parsing, and
    avoiding docling keeps the deployed container lean (no torch + layout
    models) and cold-start fast.

    The simple `.paragraphs` / `.tables` accessors lose document order
    (all paragraphs, then all tables), so we iterate the body's child
    elements directly -- the canonical python-docx recipe for in-order walk."""
    from docx import Document
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    doc = Document(str(path))
    parts: list[str] = []
    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            para = Paragraph(child, doc)
            text = para.text.strip()
            if not text:
                continue
            style = (para.style.name or "") if para.style else ""
            parts.append(f"## {text}" if style.startswith("Heading") else text)
        elif isinstance(child, CT_Tbl):
            table = Table(child, doc)
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells]
                parts.append(" | ".join(cells))
    return "\n\n".join(parts).strip()


def _pdf_to_text(path: Path) -> str:
    """Extract text from a PDF page-by-page with pdfplumber (already a
    dependency for the geometry detector). Emits `--- page N ---` boundaries
    so a multi-page source keeps its structure. Lighter than docling; good
    enough for prose / report PDFs, which is the common intake case."""
    import pdfplumber
    parts: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            parts.append(f"--- page {i} ---")
            parts.append(text)
            parts.append("")
    return "\n".join(parts).strip()


def parse_document(path: Path) -> tuple[str, str]:
    """Any supported document -> (filename, extracted text).

    PPTX uses a slide-aware walker that emits `--- slide N ---` boundaries
    so the transcribe crafter can count source slides. DOCX is parsed with
    python-docx (headings -> `## `, tables -> `cell | cell`), PDF with
    pdfplumber (page boundaries). Plain .md / .txt are read directly.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".md", ".txt"):
        return path.name, path.read_text(errors="replace")
    if suffix == ".pptx":
        return path.name, _pptx_to_text(path)
    if suffix == ".docx":
        return path.name, _docx_to_text(path)
    if suffix == ".pdf":
        return path.name, _pdf_to_text(path)
    raise ValueError(
        f"unsupported source type {suffix!r} for {path.name} "
        "(supported: .md .txt .docx .pdf .pptx)")


def craft_plan(request: str, source_paths: list[Path] | None = None) -> str:
    """Turn a request (plus any uploaded source documents) into a plan.md."""
    source_texts: list[tuple[str, str]] = []
    parse_errors: list[str] = []
    for sp in source_paths or []:
        try:
            name, text = parse_document(sp)
            if not text.strip():
                raise ValueError("parsed but yielded no text")
            source_texts.append((name, text))
            log.info("parsed source: %s (%d chars)", Path(sp).name, len(text))
        except Exception as e:  # noqa: BLE001
            parse_errors.append(f"{Path(sp).name}: {e}")
            log.warning("failed to parse %s: %s", sp, e)

    # If the user uploaded sources but NONE parsed, fail loudly. Silently
    # continuing makes the crafter invent a plan from the request string
    # alone -- which reads to the user as "the planner hallucinated something
    # completely different" rather than "your upload couldn't be read".
    if (source_paths and not source_texts):
        raise RuntimeError(
            "could not read any uploaded source document -- "
            + "; ".join(parse_errors)
            + ". Check that the file type is supported (.docx .pdf .pptx "
            ".md .txt) and that the parsing dependencies are installed.")

    transcribe = _is_deck_source(source_paths or [])
    if transcribe:
        log.info("transcribe mode: uploaded source is a finished deck")
        messages = [
            {"role": "system", "content": TRANSCRIBE_SYSTEM_PROMPT},
            {"role": "user", "content": build_transcribe_user_message(
                request, source_texts, _load_transcribe_pairs())},
        ]
    else:
        messages = [
            {"role": "system", "content": CRAFTER_SYSTEM_PROMPT},
            {"role": "user", "content": build_crafter_user_message(
                request, source_texts, _load_exemplars())},
        ]
    content, _ = llm.chat(config.ROSTER["crafter"], messages)
    plan = _normalize_ascii(_strip_fence(content))
    log.info("crafted plan (%s): %d chars, %d slide section(s)",
             "transcribe" if transcribe else "research",
             len(plan), plan.count("\n## "))
    return plan


def edit_plan(plan_md: str, instruction: str) -> str:
    """Apply a change instruction to an existing plan.md, returning the FULL
    revised plan. Reuses the crafter system prompt (same format + faithfulness
    rules); only the user-message framing differs -- see
    build_plan_edit_user_message. Surgical: apply only the requested change,
    preserve the rest, invent nothing beyond the plan and the instruction."""
    if not plan_md or not plan_md.strip():
        raise ValueError("plan is empty")
    if not instruction or not instruction.strip():
        raise ValueError("instruction is empty")
    messages = [
        {"role": "system", "content": CRAFTER_SYSTEM_PROMPT},
        {"role": "user", "content": build_plan_edit_user_message(
            plan_md, instruction)},
    ]
    content, _ = llm.chat(config.ROSTER["crafter"], messages)
    plan = _normalize_ascii(_strip_fence(content))
    log.info("edited plan: %d -> %d chars, %d slide section(s), instruction=%r",
             len(plan_md), len(plan), plan.count("\n## "), instruction[:80])
    return plan
