"""Deterministic slide-geometry detector — the DETECT stage of the repair loop.

Given a rendered deck PDF and a page number, report the layout defects that can
be measured without a model: text-on-text overlap, glyph overlap, container
overflow, and off-canvas elements.

  geometry_facts()  -> the model-readable digest (the diagnoser's input)
  defect_summary()  -> {defects, textlen} — the scalar pair the repair loop's
                       verify gate compares before vs after a fix.
"""
from __future__ import annotations

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
    return "\n".join(out)


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
    return {
        "defects": (len(overlaps) + len(glyph_hits) + len(overflow)
                    + len(oc) + len(under_shape) + len(timeline_hits)
                    + len(clipped) + len(crowded)),
        "textlen": sum(len(ln["text"]) for ln in L),
    }
