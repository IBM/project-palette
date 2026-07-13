"""Deterministic slide-geometry detector — the DETECT stage of the repair loop.

Given a rendered deck PDF and a page number, report the layout defects that can
be measured without a model: text-on-text overlap, glyph overlap, container
overflow, and off-canvas elements.

  geometry_facts()  -> the model-readable digest (the diagnoser's input)
  defect_summary()  -> {defects, textlen} — the scalar pair the repair loop's
                       verify gate compares before vs after a fix.
"""
from __future__ import annotations

import json
from pathlib import Path

import pdfplumber


def _lines_from_words(words):
    out = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        placed = False
        for ln in out:
            if abs(ln["top"] - w["top"]) <= 3 and 0 <= w["x0"] - ln["x1"] <= 40:
                ln["text"] += " " + w["text"]
                ln["x1"] = max(ln["x1"], w["x1"])
                ln["top"] = min(ln["top"], w["top"])
                ln["bottom"] = max(ln["bottom"], w["bottom"])
                placed = True
                break
        if not placed:
            out.append({k: w[k] for k in ("text", "x0", "x1", "top", "bottom")})
    return out


def _inter(a, b):
    x = max(0.0, min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]))
    y = max(0.0, min(a["bottom"], b["bottom"]) - max(a["top"], b["top"]))
    return x * y


def _area(a):
    return max(0.0, a["x1"] - a["x0"]) * max(0.0, a["bottom"] - a["top"])


def _glyph_overlap(chars):
    """Lines whose consecutive characters collide — negative charSpacing
    overlaps the glyphs. extract_words sees only whole-word boxes, so this is
    a character-level check.

    A real defect compresses the WHOLE line: a strongly negative MEAN gap and
    a deep WORST gap. mean + worst, not a raw count of negative pairs, is the
    discriminator.

    The baseline thresholds (mean < -0.045in, worst < -0.07in) are calibrated
    for ~24pt text. Bold display fonts at 60pt+ naturally render with
    proportionally tighter kerning — same negative-gap *reading* scales with
    the glyph itself — so the same absolute thresholds false-positive on big
    headlines (cover titles, mega-stats, closing thematics). Scale the
    thresholds by the line's median character size: at 24pt the baseline
    holds; at 72pt the thresholds become 3x looser. Stops bold display
    headlines from being flagged while keeping small/medium-text detection
    sharp. Returns inch-unit hit dicts."""
    bylines: dict = {}
    for c in chars:
        bylines.setdefault(round(c["top"] / 72.0, 1), []).append(c)
    hits = []
    for cs in bylines.values():
        if len(cs) < 6:
            continue
        cs.sort(key=lambda c: c["x0"])
        gaps = [(cs[i + 1]["x0"] - cs[i]["x1"]) / 72.0
                for i in range(len(cs) - 1)]
        mean = sum(gaps) / len(gaps)
        neg = sum(1 for g in gaps if g < 0)
        sizes = sorted(c.get("size", 14) for c in cs)
        median_size = sizes[len(sizes) // 2]
        scale = max(1.0, median_size / 24.0)
        mean_thr = -0.045 * scale
        worst_thr = -0.07 * scale
        if mean < mean_thr and min(gaps) < worst_thr and neg >= 0.7 * len(gaps):
            hits.append({
                "text": "".join(c["text"] for c in cs),
                "x0": min(c["x0"] for c in cs) / 72.0,
                "x1": max(c["x1"] for c in cs) / 72.0,
                "top": min(c["top"] for c in cs) / 72.0,
                "bottom": max(c["bottom"] for c in cs) / 72.0,
                "mean": mean,
                "worst": min(gaps),
                "size_pt": median_size,
            })
    return hits


def _text_under_shape(L, R, W, H):
    """Text lines accidentally covered by a non-card filled rect — a table-
    header strip overlapping a subtitle, an accent bar drawn on top of text,
    etc. The regular text-on-text overlap check is blind to these because
    the offender is a SHAPE, not text.

    Discriminator: a text bbox FULLY CONTAINED inside the rect is intentional
    design (column header, numbered badge, button label). A text bbox that
    EXTENDS PAST the rect in any direction is accidental occlusion (something
    drawn over text). We flag only the second case.

    Also skips full-canvas backgrounds, thin accent rules, and tall card/
    panel containers. Returns (line, rect, overlap_ratio) tuples."""
    TOL = 0.02
    hits = []
    for ln in L:
        line_area = _area(ln)
        if line_area < 0.001:
            continue
        for r in R:
            rh = r["bottom"] - r["top"]
            rw = r["x1"] - r["x0"]
            if rh < 0.12:
                continue   # accent rule / divider line — too thin
            if rh >= 0.80:
                continue   # card/panel — text inside is intentional
            if rw >= W - 0.2 and rh >= H - 0.6:
                continue   # full-canvas background
            ia = _inter(ln, r)
            ratio = ia / line_area
            if ratio < 0.40:
                continue
            # text fully inside the rect → designed (column header, badge); skip
            fully_inside = (ln["x0"] >= r["x0"] - TOL
                            and ln["x1"] <= r["x1"] + TOL
                            and ln["top"] >= r["top"] - TOL
                            and ln["bottom"] <= r["bottom"] + TOL)
            if fully_inside:
                continue
            hits.append((ln, r, ratio))
            break
    return hits


def _timeline_collisions(L, R):
    """Adjacent timeline-station labels with insufficient horizontal clearance.

    Recognizes a 'timeline': >=4 small near-circular shapes at a similar y
    AND evenly spaced (the row of dots along the timeline's spine). For each
    pair of text lines in the same y-band, if they belong to different
    stations (their nearest dots differ) and their horizontal clearance is
    < 0.08in, flag as visually crowded. (Actual bbox overlap is caught by the
    text-overlap check; this catches "almost touching" — the visual collision
    pattern the existing check misses on tight timelines.)

    Returns list of (left_line, right_line, gap) tuples."""
    dots = []
    for r in R:
        w = r["x1"] - r["x0"]
        h = r["bottom"] - r["top"]
        if 0.10 < w < 0.40 and 0.10 < h < 0.40 and abs(w - h) < 0.12:
            dots.append({"cx": (r["x0"] + r["x1"]) / 2,
                         "cy": (r["top"] + r["bottom"]) / 2})
    if len(dots) < 4:
        return []
    y_groups: dict = {}
    for d in dots:
        y_groups.setdefault(round(d["cy"], 1), []).append(d)
    cluster = max(y_groups.values(), key=len)
    if len(cluster) < 4:
        return []
    cluster.sort(key=lambda d: d["cx"])
    # consistent spacing test — guards against false positives where 4+ small
    # near-circular things happen to share a y but are NOT a timeline (chart
    # legend swatches, four stat icons, etc.)
    spacings = [cluster[i + 1]["cx"] - cluster[i]["cx"]
                for i in range(len(cluster) - 1)]
    mean_s = sum(spacings) / len(spacings)
    if mean_s < 0.6:
        return []   # too tight, not a timeline spine
    if max(spacings) > mean_s * 1.6 or min(spacings) < mean_s * 0.5:
        return []   # uneven spacing, not a timeline
    dot_xs = [d["cx"] for d in cluster]
    line_y = sum(d["cy"] for d in cluster) / len(cluster)

    candidates = [ln for ln in L
                  if abs((ln["top"] + ln["bottom"]) / 2 - line_y) < 1.5]

    def _station(ln):
        cx = (ln["x0"] + ln["x1"]) / 2
        return min(range(len(dot_xs)), key=lambda i: abs(dot_xs[i] - cx))

    hits = []
    seen: set = set()
    for i in range(len(candidates)):
        for j in range(i + 1, len(candidates)):
            a, b = candidates[i], candidates[j]
            if abs((a["top"] + a["bottom"]) / 2
                   - (b["top"] + b["bottom"]) / 2) > 0.20:
                continue
            if _station(a) == _station(b):
                continue
            if a["x1"] <= b["x0"]:
                gap, left, right = b["x0"] - a["x1"], a, b
            elif b["x1"] <= a["x0"]:
                gap, left, right = a["x0"] - b["x1"], b, a
            else:
                continue   # actual overlap — caught by text-overlap check
            if gap < 0.08:
                key = (id(left), id(right))
                if key not in seen:
                    seen.add(key)
                    hits.append((left, right, gap))
    return hits


def _text_clipped_by_shape(L, R, W, H):
    """Text whose bbox is cut by a rect's top or bottom edge — the rect's
    edge passes THROUGH the text vertically, cropping where the rect was
    drawn over the text. Catches cases like a subtitle whose bottom is
    clipped by a card's top edge (subtitle.top=1.89, card.top=2.00,
    subtitle.bottom=2.07 → card's top edge bisects the subtitle).

    Distinct from `_text_under_shape` (HIGH coverage by SHORT strips):
    this flags edge-bisection regardless of coverage, and works on tall
    cards/panels because we only care that the EDGE crosses the text — the
    rect's own height doesn't matter for the discrimination.

    A text bbox fully inside the rect (text-on-card, designed) has the
    rect's top ABOVE text.top, so no cut → no flag.
    """
    hits = []
    TOL = 0.015
    for ln in L:
        if ln["bottom"] - ln["top"] < 0.06:
            continue
        for r in R:
            rh = r["bottom"] - r["top"]
            rw = r["x1"] - r["x0"]
            if rw >= W - 0.2 and rh >= H - 0.6:
                continue   # full canvas
            if rh < 0.20:
                continue   # too thin to be a card/panel/strip
            if rw < 0.50:
                continue   # too narrow — likely a dot/badge/icon, not a clipper
            hx = min(ln["x1"], r["x1"]) - max(ln["x0"], r["x0"])
            if hx < 0.10:
                continue
            cuts_top = (ln["top"] + TOL < r["top"] < ln["bottom"] - TOL)
            cuts_bot = (ln["top"] + TOL < r["bottom"] < ln["bottom"] - TOL)
            if cuts_top or cuts_bot:
                hits.append((ln, r, "top" if cuts_top else "bottom"))
                break
    return hits


def _crowded_text(L):
    """Text lines that don't strictly overlap (so the overlap check is
    silent) but the vertical clearance between them is < 0.025in AND the
    two lines are clearly DIFFERENT visual elements — one's width is at
    least 2.5x the other's. The width-ratio test is the discriminator: it
    excludes consecutive lines of a wrapped paragraph and indented bullet
    items (similar widths, no flag), while flagging cases like a wide
    caption sitting microscopically below a narrow table-row cell.

    Returns (line_a, line_b, gap) tuples — one hit per "wider" line so a
    single wide caption near many narrow neighbours surfaces once."""
    hits = []
    flagged_wider: set = set()
    for i in range(len(L)):
        for j in range(i + 1, len(L)):
            a, b = L[i], L[j]
            hx = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
            if hx < 0.10:
                continue
            if a["bottom"] <= b["top"]:
                gap = b["top"] - a["bottom"]
            elif b["bottom"] <= a["top"]:
                gap = a["top"] - b["bottom"]
            else:
                continue   # actual overlap — caught by overlap check
            if not (0.0 <= gap < 0.025):
                continue
            wa = a["x1"] - a["x0"]
            wb = b["x1"] - b["x0"]
            if wa <= 0 or wb <= 0:
                continue
            if max(wa, wb) / min(wa, wb) < 2.5:
                continue   # similar widths => same paragraph/block, skip
            wider = a if wa > wb else b
            if id(wider) in flagged_wider:
                continue
            flagged_wider.add(id(wider))
            hits.append((a, b, gap))
    return hits


def _fitz_colocated_overlaps(pdf_path: Path, page_no: int, existing):
    """G7 additive pass — catch two text draws at the SAME anchor that
    pdfplumber merges into one garbled line (so the text-on-text overlap check
    sees one line and misses it). PyMuPDF keeps them as separate spans, so the
    same overlap criterion fires. Returns (a, b) line-dict pairs (same shape as
    the pdfplumber `overlaps` entries) NOT already in `existing`.

    GUARDED: if PyMuPDF is unavailable, returns [] and the detector is exactly
    the pdfplumber detector — zero behaviour change. Gated to FP=0 on the
    49-slide qwen sweep: cross-line spans only (same-line spans are intentional
    rich-text runs), baselines coincide (Δtop<0.12), similar font size
    (height-ratio>0.6), and substantial coverage of the smaller span (>0.40) —
    which suppresses the big-numeral-near-caption marginal class."""
    try:
        import fitz  # PyMuPDF
    except Exception:
        return []
    try:
        doc = fitz.open(str(pdf_path))
        page = doc[page_no - 1]
        spans = []
        for blk in page.get_text("dict")["blocks"]:
            for line in blk.get("lines", []):
                for sp in line.get("spans", []):
                    t = sp["text"].strip()
                    if not t:
                        continue
                    x0, y0, x1, y1 = (c / 72.0 for c in sp["bbox"])
                    spans.append({"text": t, "x0": x0, "x1": x1, "top": y0,
                                  "bottom": y1, "_line": id(line)})
        doc.close()
    except Exception:
        return []

    def _bb(p, q):
        """Do bboxes p and q meaningfully intersect?"""
        ox = min(p["x1"], q["x1"]) - max(p["x0"], q["x0"])
        oy = min(p["bottom"], q["bottom"]) - max(p["top"], q["top"])
        return ox > 0 and oy > 0 and ox * oy > 0.02

    def _dup(a, b, pairs):
        # same defect if each member overlaps a member of an existing pair —
        # bbox-based so it matches even when the two sources extracted the
        # text differently (pdfplumber merges, PyMuPDF splits).
        for c, d in pairs:
            if (_bb(a, c) and _bb(b, d)) or (_bb(a, d) and _bb(b, c)):
                return True
        return False

    out = []
    for i in range(len(spans)):
        for j in range(i + 1, len(spans)):
            a, b = spans[i], spans[j]
            if a["_line"] == b["_line"]:
                continue  # intentional adjacent rich-text runs, not an overlap
            ox = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
            oy = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
            if ox <= 0.10 or oy <= 0:
                continue
            ia = ox * oy
            if ia <= 0.012:
                continue
            ha, hb = a["bottom"] - a["top"], b["bottom"] - b["top"]
            amin = min((a["x1"] - a["x0"]) * ha, (b["x1"] - b["x0"]) * hb)
            ratio = ia / amin if amin > 0 else 0.0
            dtop = abs(a["top"] - b["top"])
            hratio = min(ha, hb) / max(ha, hb) if max(ha, hb) > 0 else 0.0
            if dtop < 0.12 and hratio > 0.6 and ratio > 0.40:
                if _dup(a, b, existing) or _dup(a, b, out):
                    continue
                out.append((a, b))
    return out


def _detect(pdf_path: Path, page_no: int):
    """Run the deterministic detection. Returns
    (W, H, L, overlaps, glyph_hits, overflow, oc): W/H the canvas in inches,
    L the grouped text lines, and the four defect lists. Both geometry_facts()
    and defect_summary() are thin wrappers over this."""
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_no - 1]
        W, H = page.width / 72.0, page.height / 72.0
        lines = _lines_from_words(page.extract_words())
        # soffice splits filled shapes across rects and curves — use both
        shapes = list(page.rects) + list(page.curves)
        glyph_hits = _glyph_overlap(page.chars)

    def to_in(d, keys):
        return {k: d[k] / 72.0 for k in keys}

    L = [{"text": ln["text"], **to_in(ln, ("x0", "x1", "top", "bottom"))}
         for ln in lines]
    R = [to_in(s, ("x0", "x1", "top", "bottom")) for s in shapes]

    overlaps = []
    for i in range(len(L)):
        for j in range(i + 1, len(L)):
            a, b = L[i], L[j]
            ia = _inter(a, b)
            if ia <= 0.012:
                continue
            ox = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
            oy = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
            ratio = ia / min(_area(a), _area(b))
            # ratio path — overlap is a real fraction of the smaller element;
            # depth path — a thin but deep band, which catches large elements
            # (stacked mega-stats) whose overlap is small vs their big area
            # and so slips under the ratio test.
            if ox > 0.10 and (ratio > 0.18 or oy > 0.06):
                overlaps.append((a, b))

    tol = 0.05
    oc = []
    for ln in L:
        if (ln["x0"] < -tol or ln["x1"] > W + tol
                or ln["bottom"] > H + tol or ln["top"] < -tol):
            oc.append(("text \"%s\"" % ln["text"][:38], ln["x0"], ln["x1"],
                       ln["top"], ln["bottom"]))
    for r in R:
        if r["x0"] < -tol or r["x1"] > W + tol or r["bottom"] > H + tol:
            oc.append(("shape", r["x0"], r["x1"], r["top"], r["bottom"]))

    # Container overflow — a box too small for the text that belongs to it.
    # Only boxes tall enough to be a content panel qualify (>= 0.6in high — a
    # shorter box is a table cell / chip / label, which does not "overflow").
    # A line sitting properly inside a DIFFERENT qualifying box belongs to that
    # box, so it is excluded here; the rest are grown into a contiguous run and
    # spill past the box's top OR bottom edge is measured in full.
    boxes = []
    seen_box = set()
    for r in R:
        w, h = r["x1"] - r["x0"], r["bottom"] - r["top"]
        if _area(r) < 0.6 or h < 0.6:
            continue
        if w >= W - 0.2 and h >= H - 0.6:
            continue  # a full-canvas background, not a container
        k = (round(r["x0"], 1), round(r["x1"], 1),
             round(r["top"], 1), round(r["bottom"], 1))
        if k not in seen_box:
            seen_box.add(k)
            boxes.append(r)

    def _well_inside(ln, b):
        return (ln["x0"] >= b["x0"] - 0.10 and ln["x1"] <= b["x1"] + 0.10
                and ln["top"] >= b["top"] - 0.10
                and ln["bottom"] <= b["bottom"] + 0.10)

    def _box_inside(B, A, tol=0.10):
        """Is box B nested fully inside box A?"""
        return (B["x0"] >= A["x0"] - tol and B["x1"] <= A["x1"] + tol
                and B["top"] >= A["top"] - tol
                and B["bottom"] <= A["bottom"] + tol)

    overflow = []
    for ai, A in enumerate(boxes):
        # Exclude only lines well-inside a smaller box NESTED inside A. The
        # earlier `bi != ai` form excluded text owned by ANY other box,
        # including the OUTER of two nested boxes — which made nested
        # overflow undetectable (slide-9 code panel: outer card contained an
        # inner code-block; the inner's text was kicked out of BOTH boxes'
        # fit and the overflow at `});` was missed). Restricting to nested
        # children keeps sibling-box double-counting prevention intact.
        others = [B for bi, B in enumerate(boxes)
                  if bi != ai and _box_inside(B, A)]
        fit = [ln for ln in L
               if ln["x0"] >= A["x0"] - 0.15 and ln["x1"] <= A["x1"] + 0.45
               and not any(_well_inside(ln, B) for B in others)]
        run = [ln for ln in fit
               if ln["bottom"] > A["top"] + 0.02
               and ln["top"] < A["bottom"] - 0.02]
        if not run:
            continue
        run_ids = {id(ln) for ln in run}
        grew = True
        while grew:
            grew = False
            lo = min(ln["top"] for ln in run)
            hi = max(ln["bottom"] for ln in run)
            for ln in fit:
                if id(ln) in run_ids:
                    continue
                if ln["top"] <= hi + 0.34 and ln["bottom"] >= lo - 0.34:
                    run.append(ln)
                    run_ids.add(id(ln))
                    grew = True
        dy_bot = max(ln["bottom"] for ln in run) - A["bottom"]
        dy_top = A["top"] - min(ln["top"] for ln in run)
        if max(dy_bot, dy_top) > 0.12:
            overflow.append((A, run, dy_top, dy_bot))

    # G7 — additive PyMuPDF pass: catch co-located text draws that pdfplumber
    # merges into one garbled line (invisible to the text-on-text check above).
    # Additive + guarded: never removes a pdfplumber finding, no-op if PyMuPDF
    # is missing.
    overlaps.extend(_fitz_colocated_overlaps(pdf_path, page_no, overlaps))

    # Four additional checks the original four were blind to:
    under_shape = _text_under_shape(L, R, W, H)
    timeline_hits = _timeline_collisions(L, R)
    clipped = _text_clipped_by_shape(L, R, W, H)
    crowded = _crowded_text(L)

    return (W, H, L, overlaps, glyph_hits, overflow, oc,
            under_shape, timeline_hits, clipped, crowded)


def geometry_facts(pdf_path: Path, page_no: int) -> str:
    """Deterministic geometry digest — no model. Eight defect classes:
    text-on-text overlap, glyph overlap, container overflow, off-canvas
    elements, text occluded by a filled shape, timeline-station crowding,
    text clipped by a rect edge, and crowded text (tight vertical gap)."""
    (W, H, L, overlaps, glyph_hits, overflow, oc,
     under_shape, timeline_hits, clipped, crowded) = _detect(pdf_path, page_no)

    out = [f"Canvas: {W:.2f}in wide x {H:.2f}in tall. Units below are inches.",
           ""]
    if sum(len(ln["text"]) for ln in L) < 15:
        # <15 chars = effectively no text — a silent render failure (valid JS
        # that drew nothing). Kept tight: a one-line divider/closing slide runs
        # ~40-60 chars and is legitimately sparse, not blank.
        out.insert(1, "WARNING: slide is BLANK or near-empty — likely a silent "
                      "render failure (valid JS that drew nothing). Inspect.")
    out.append(f"OVERLAPPING TEXT REGIONS ({len(overlaps)} pair(s) collide):")
    for a, b in overlaps[:14]:
        out.append(f"  \"{a['text'][:34]}\" [x {a['x0']:.2f}-{a['x1']:.2f}, "
                   f"y {a['top']:.2f}-{a['bottom']:.2f}]")
        out.append(f"    collides with  \"{b['text'][:34]}\" "
                   f"[x {b['x0']:.2f}-{b['x1']:.2f}, "
                   f"y {b['top']:.2f}-{b['bottom']:.2f}]")
    if not overlaps:
        out.append("  (none)")
    out.append("")
    out.append(f"GLYPH OVERLAP ({len(glyph_hits)} line(s) whose characters "
               f"collide):")
    for g in glyph_hits[:8]:
        out.append(f"  \"{g['text'][:40]}\" [x {g['x0']:.2f}-{g['x1']:.2f}, "
                   f"y {g['top']:.2f}-{g['bottom']:.2f}] — characters collide "
                   f"(mean gap {g['mean']:.3f}in, worst {g['worst']:.3f}in); "
                   f"negative charSpacing")
    if not glyph_hits:
        out.append("  (none)")
    out.append("")
    out.append(f"CONTAINER OVERFLOW ({len(overflow)} box(es) too small for "
               f"their content):")
    for r, run, dy_top, dy_bot in overflow[:10]:
        txt = " | ".join(ln["text"] for ln in
                         sorted(run, key=lambda l: l["top"]))[:80]
        spill = []
        if dy_bot > 0.08:
            spill.append(f"{dy_bot:.2f}in past its bottom edge")
        if dy_top > 0.08:
            spill.append(f"{dy_top:.2f}in past its top edge")
        out.append(f"  box [x {r['x0']:.2f}-{r['x1']:.2f}, "
                   f"y {r['top']:.2f}-{r['bottom']:.2f}] — its text spills "
                   + " and ".join(spill))
        out.append(f"    contains: \"{txt}\"")
    if not overflow:
        out.append("  (none)")
    out.append("")
    out.append(f"OFF-CANVAS ELEMENTS ({len(oc)} extend past a slide edge):")
    for desc, x0, x1, t, b in oc[:12]:
        out.append(f"  {desc} -- bbox x {x0:.2f}-{x1:.2f}, y {t:.2f}-{b:.2f}")
    if not oc:
        out.append("  (none)")
    out.append("")
    out.append(f"TEXT OCCLUDED BY A SHAPE ({len(under_shape)} line(s) covered "
               f"by a filled rect):")
    for ln, r, ratio in under_shape[:8]:
        out.append(f"  \"{ln['text'][:40]}\" [x {ln['x0']:.2f}-{ln['x1']:.2f}, "
                   f"y {ln['top']:.2f}-{ln['bottom']:.2f}] is "
                   f"{int(ratio*100)}% covered by rect "
                   f"[x {r['x0']:.2f}-{r['x1']:.2f}, "
                   f"y {r['top']:.2f}-{r['bottom']:.2f}]")
    if not under_shape:
        out.append("  (none)")
    out.append("")
    out.append(f"TIMELINE-STATION CROWDING ({len(timeline_hits)} adjacent "
               f"label(s) too close):")
    for left, right, gap in timeline_hits[:8]:
        out.append(f"  \"{left['text'][:30]}\" (right edge x {left['x1']:.2f}) "
                   f"sits {gap:.2f}in from "
                   f"\"{right['text'][:30]}\" (left edge x {right['x0']:.2f}) "
                   f"— different timeline stations, near-touching labels")
    if not timeline_hits:
        out.append("  (none)")
    out.append("")
    out.append(f"TEXT CLIPPED BY A SHAPE EDGE ({len(clipped)} line(s) cut "
               f"vertically by a rect's edge):")
    for ln, r, edge in clipped[:8]:
        out.append(f"  \"{ln['text'][:40]}\" [y {ln['top']:.2f}-{ln['bottom']:.2f}, "
                   f"x {ln['x0']:.2f}-{ln['x1']:.2f}] is cut by the {edge} "
                   f"edge of a rect at y {r['top']:.2f}-{r['bottom']:.2f}")
    if not clipped:
        out.append("  (none)")
    out.append("")
    out.append(f"CROWDED TEXT ({len(crowded)} pair(s) with vertical clearance "
               f"< 0.025in):")
    for a, b, gap in crowded[:8]:
        out.append(f"  \"{a['text'][:30]}\" and \"{b['text'][:30]}\" share a "
                   f"{gap:.3f}in vertical gap — visually touching")
    if not crowded:
        out.append("  (none)")
    out.append("")
    manifest_hits = _manifest_collisions(_manifest_path_for(pdf_path), page_no)
    out.append(f"TEXT COLLIDING WITH A SHAPE ({len(manifest_hits)} — exact, from "
               f"the draw manifest):")
    for t, s, cls, ratio in manifest_hits[:10]:
        tb, sb = _mbox(t), _mbox(s)
        if not tb or not sb:
            continue
        why = ("is hidden behind the shape (the shape is drawn on top of it)"
               if cls == "occluded"
               else f"is the same color as the {s.get('fill')} shape it sits on "
                    f"— the text is invisible there")
        out.append(f"  \"{(t.get('text') or '')[:34]}\" "
                   f"[x {tb[0]:.2f}-{tb[2]:.2f}, y {tb[1]:.2f}-{tb[3]:.2f}] "
                   f"{why}; {int(ratio * 100)}% overlaps the {s.get('kind')} "
                   f"at [x {sb[0]:.2f}-{sb[2]:.2f}, y {sb[1]:.2f}-{sb[3]:.2f}]")
    if not manifest_hits:
        out.append("  (none)")
    return "\n".join(out)


def _manifest_path_for(pdf_path: Path) -> Path:
    """The render drops elements.json (the exact per-slide draw manifest) next
    to the pptx/pdf. Same dir as the PDF the detector reads."""
    return Path(pdf_path).parent / "elements.json"


def _mbox(e: dict):
    """(x0, y0, x1, y1) in inches from a manifest element, or None if the
    element didn't declare a full box."""
    x, y, w, h = e.get("x"), e.get("y"), e.get("w"), e.get("h")
    if any(v is None for v in (x, y, w, h)):
        return None
    try:
        x, y, w, h = float(x), float(y), float(w), float(h)
    except (TypeError, ValueError):
        return None
    return (x, y, x + abs(w), y + abs(h))


def _glyph_box(t: dict):
    """A text element's DECLARED box shrunk to an estimate of the actual glyph
    extent. Declared text boxes are usually full-column-width (w up to ~11.7in)
    while the words occupy only a fraction, so the raw box over-reports where the
    text is — a top-right corner icon 'overlaps' a left-aligned eyebrow's full-
    width box even though the glyphs are far to the left. Estimate glyph width
    from char-count x fontSize (~0.6 em average advance for proportional sans)
    and anchor it by the element's alignment. Falls back to the declared box when
    there's no fontSize/text to estimate from. Returns (x0, y0, x1, y1) or None.
    """
    b = _mbox(t)
    if not b:
        return None
    x0, y0, x1, y1 = b
    txt = (t.get("text") or "").strip()
    try:
        fs = float(t.get("fontSize"))
    except (TypeError, ValueError):
        return b
    if not txt or fs <= 0:
        return b
    est = min(x1 - x0, len(txt) * fs / 72.0 * 0.6 + 0.1)
    align = (t.get("align") or "left")
    if align == "center":
        cx = (x0 + x1) / 2
        return (cx - est / 2, y0, cx + est / 2, y1)
    if align == "right":
        return (x1 - est, y0, x1, y1)
    return (x0, y0, x0 + est, y1)


def _manifest_collisions(manifest_path: Path, slide_n: int):
    """Exact text-occlusion collisions from the render manifest — the check
    that pixel-archaeology can't do reliably on hand-drawn charts.

    A text element whose box overlaps a FILLED shape (rect/ellipse) drawn AFTER
    it is occluded by that shape — the hero-behind-bar, bar-over-eyebrow,
    annotation-over-plot class. The geometry is exact (resolved inch coords from
    the draw call), so no region clustering or font-height guessing is needed.

    Draw order is the precision key, not a threshold: a shape drawn BEFORE the
    text (text sits on top — a value label on a bar, a title on a panel, a label
    on a cylinder) is intentional and skipped; only a shape drawn ON TOP of text
    hides it and is flagged. The only tolerance is a sub-pixel graze floor so an
    incidental edge-touch isn't reported.

    Returns [(text_el, shape_el, occluded_ratio), ...]. Empty if no manifest.
    """
    try:
        m = json.loads(Path(manifest_path).read_text())
    except (OSError, ValueError):
        return []
    els = m.get(str(slide_n)) or []
    texts = [e for e in els
             if e.get("kind") == "text" and (e.get("text") or "").strip()]
    shapes = [e for e in els
              if e.get("kind") in ("rect", "ellipse") and e.get("fill")]

    def _norm(c):
        return c.lstrip("#").upper() if isinstance(c, str) else None

    hits = []
    for t in texts:
        tb = _mbox(t)
        if not tb:
            continue
        t_area = max((tb[2] - tb[0]) * (tb[3] - tb[1]), 1e-6)
        t_order = t.get("order") or 0
        # every color this text draws in — outer option plus each rich-text run
        t_colors = {_norm(t.get("color"))}
        for r in (t.get("runs") or []):
            t_colors.add(_norm(r.get("color")))
        t_colors.discard(None)
        for s in shapes:
            sb = _mbox(s)
            if not sb:
                continue
            ox = min(tb[2], sb[2]) - max(tb[0], sb[0])
            oy = min(tb[3], sb[3]) - max(tb[1], sb[1])
            if ox <= 0 or oy <= 0:
                continue
            area = ox * oy
            # sub-pixel graze floor — not a semantic threshold, just noise cut
            if area < 0.02 or (area / t_area < 0.08 and min(ox, oy) < 0.05):
                continue
            s_fill = _norm(s.get("fill"))
            if (s.get("order") or 0) > t_order:
                cls = "occluded"      # shape drawn on top hides the text
            elif s_fill and s_fill in t_colors:
                cls = "same_color"    # text on top, same color as fill → invisible
            else:
                continue              # text on top, contrasting → intentional
            hits.append((t, s, cls, round(area / t_area, 2)))
            break  # one collision per text element is enough to flag it
    return hits


def _manifest_placement_collisions(manifest_path: Path, slide_n: int):
    """Additive element-placement collisions from the render manifest — the
    classes both the pixel/PDF path and the text-vs-filled-shape occlusion
    check (`_manifest_collisions`) structurally miss:

      icon_over_text     an image (Carbon icon) landing on text — an icon
                         dropped on an agenda number, an eyebrow, or a
                         headline (agenda badges, stray corner icons). The PDF
                         detector never extracts images, so it is blind to this.
      rule_strikethrough a THIN filled shape (accent rule / divider, h<0.12)
                         cutting through a text line's vertical interior — an
                         eyebrow or headline struck out by its own accent bar.
                         `_manifest_collisions`' graze floor drops these because
                         a thin bar's overlap area is tiny; the PDF path's
                         `_text_under_shape` skips any shape with rh<0.12.
      text_crowd         two body-text elements in the same column whose
                         declared boxes bleed into each other vertically —
                         stacked mega-stats/captions colliding, which the PDF
                         text-overlap pass suppresses on big-numeral slides.

    Exact inch coords from the draw call, so no font-height guessing. Kept
    SEPARATE from `_manifest_collisions` so that check's tuning is untouched;
    this only ever ADDS hits. Returns a flat list of (a, b, cls, metric)
    tuples; empty if no manifest.
    """
    try:
        m = json.loads(Path(manifest_path).read_text())
    except (OSError, ValueError):
        return []
    els = m.get(str(slide_n)) or []
    texts = [e for e in els
             if e.get("kind") == "text" and (e.get("text") or "").strip()]
    images = [e for e in els if e.get("kind") == "image" and _mbox(e)]
    thin = [e for e in els
            if e.get("kind") in ("rect", "ellipse", "line") and e.get("fill")
            and _mbox(e) and (_mbox(e)[3] - _mbox(e)[1]) < 0.12]

    def _ov(a, b):
        return (min(a[2], b[2]) - max(a[0], b[0]),   # ox
                min(a[3], b[3]) - max(a[1], b[1]))    # oy

    hits = []

    # 1) icon (image) landing on a text box — real 2D overlap, and the icon
    #    meaningfully lands on the text (not an incidental corner graze). Uses
    #    the glyph extent, not the declared box, so a corner icon over a full-
    #    width eyebrow's empty right end doesn't false-positive.
    for im in images:
        ib = _mbox(im)
        icon_area = max((ib[2] - ib[0]) * (ib[3] - ib[1]), 1e-6)
        for t in texts:
            tb = _glyph_box(t)
            if not tb:
                continue
            ox, oy = _ov(ib, tb)
            if ox > 0.04 and oy > 0.04 and (ox * oy) / icon_area > 0.15:
                hits.append((im, t, "icon_over_text",
                             round((ox * oy) / icon_area, 2)))
                break

    # 2) thin accent rule / divider whose y-center sits INSIDE a text line's
    #    interior band (a strike-through), with real horizontal overlap. The
    #    interior test (not the edge) is what separates "bar drawn through the
    #    eyebrow" from "bar correctly resting just below the headline".
    for s in thin:
        sb = _mbox(s)
        s_yc = (sb[1] + sb[3]) / 2
        for t in texts:
            tb = _glyph_box(t)
            if not tb:
                continue
            th = tb[3] - tb[1]
            if th <= 0:
                continue
            ox, _ = _ov(sb, tb)
            if ox > 0.10 and (tb[1] + 0.15 * th) < s_yc < (tb[3] - 0.15 * th):
                hits.append((s, t, "rule_strikethrough", round(ox, 2)))
                break

    # 3) stacked body text whose glyph boxes overlap vertically in the same
    #    column — real horizontal coincidence AND meaningful vertical bleed.
    tb_boxes = [(t, _glyph_box(t)) for t in texts if _glyph_box(t)]
    for i in range(len(tb_boxes)):
        (a, ab) = tb_boxes[i]
        for j in range(i + 1, len(tb_boxes)):
            (b, bb) = tb_boxes[j]
            ox, oy = _ov(ab, bb)
            # 0.12in vertical floor: below this, an overlap is usually just a
            # generously-sized multi-line box (e.g. a timeline title over its
            # sublabel), not a real collision. Genuine stat/caption crowding
            # runs 0.15in+. ox>0.5 keeps it to same-column glyph overlap.
            if ox > 0.5 and oy > 0.12:
                hits.append((a, b, "text_crowd", round(oy, 2)))
                break
    return hits


# Placement-collision classes the qwen repair editor reliably REVERTS (verify
# sees no improvement → reverts). Attempting a full-slide rewrite for them is
# minutes of wasted teacher time with an identical finalized deck. rule_
# strikethrough is NOT here — qwen fixes it ~60% of the time, so it stays
# repairable. The gate uses `always_revert_count` to skip repair on slides whose
# ONLY defects are these, while still flagging them in `defect_summary`.
ALWAYS_REVERT_CLASSES = ("icon_over_text", "text_crowd")


def always_revert_count(pdf_path: Path, page_no: int) -> int:
    """How many of a slide's defects are placement collisions in the always-
    revert classes. Subtract from the total defect count to decide whether a
    slide has any REPAIRABLE defect worth a teacher rewrite. Detection and
    flagging are unaffected — these still count in `defect_summary`."""
    hits = _manifest_placement_collisions(_manifest_path_for(pdf_path), page_no)
    return sum(1 for h in hits if h[2] in ALWAYS_REVERT_CLASSES)


def defect_summary(pdf_path: Path, page_no: int) -> dict:
    """Scalar health of one slide — the input to the repair loop's verify gate.

      defects  total count across the four defect classes.
      textlen  total characters of rendered text on the slide. A content-
               survival proxy: if a 'fix' makes the slide go blank or drops
               content, defects can fall to ~0 while textlen collapses — so
               the gate must check BOTH (fewer defects AND content intact).
    """
    (_, _, L, overlaps, glyph_hits, overflow, oc,
     under_shape, timeline_hits, clipped, crowded) = _detect(pdf_path, page_no)
    manifest_hits = _manifest_collisions(_manifest_path_for(pdf_path), page_no)
    placement_hits = _manifest_placement_collisions(
        _manifest_path_for(pdf_path), page_no)
    return {
        "defects": (len(overlaps) + len(glyph_hits) + len(overflow)
                    + len(oc) + len(under_shape) + len(timeline_hits)
                    + len(clipped) + len(crowded) + len(manifest_hits)
                    + len(placement_hits)),
        "textlen": sum(len(ln["text"]) for ln in L),
    }
