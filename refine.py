"""Stage 3 — refinement.

  - repair_render_errors  — build-time: fix slides whose JS errored at render.
  - geometry_refine_pass  — build-time: the deterministic geometry-repair loop
                            — the detector flags layout defects, the repair
                            model rewrites them, a verify gate keeps the wins.
  - visual_refine_pass    — a Qwen-VL critique pass; currently NOT in the build
                            (the geometry pass replaced it), kept for later.
  - apply_nl_edit         — a user's natural-language edit to one slide.

The build-time editing routes to gpt-oss-120b patching one slide's pptxgenjs
JS — the fine-tuned 20b is generation-only. The build-time passes
(repair_render_errors + geometry_refine_pass) run inside generate_deck.
"""
from __future__ import annotations

import json
import contextvars
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import config
import detector
import llm
from harness_prompts import (
    CRITIC_SYSTEM_PROMPT,
    EDITOR_SYSTEM_PROMPT,
    build_critic_user_message,
    build_editor_user_message,
)
from render import render, render_previews

log = logging.getLogger("refine")

Progress = Callable[[str, int, int], None]

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n?\s*```\s*$", re.DOTALL)
_JSON_OBJ = re.compile(r"\{.*\}", re.DOTALL)


def _strip_fence(text: str) -> str:
    m = _FENCE.match(text.strip())
    return m.group(1).strip() if m else text.strip()


def _slide_by_n(deck: dict[str, Any], n: int) -> dict[str, Any] | None:
    for s in deck.get("slides", []):
        if s.get("n") == n:
            return s
    return None


def _load_render_failures(out_dir: Path) -> dict[int, list[str]]:
    """slide_n -> render error descriptions, from render_failures.json (written
    by render()). A slide that errored at render time gets its actual error fed
    to the editor — far more actionable than the visual symptom (a blank slide
    the VL critic can only describe as 'content missing')."""
    p = Path(out_dir) / "render_failures.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[int, list[str]] = {}
    for f in data:
        n = f.get("slide")
        if not isinstance(n, int):
            continue
        kind = f.get("kind", "error")
        # missing_image is non-fatal — the renderer already skipped the image
        # and the deck still renders. Asset paths are handled deterministically
        # (the asset remap); a genuinely-missing icon staying skipped is fine.
        # Not worth an editor call, so it is not treated as a slide error.
        if kind == "missing_image":
            continue
        detail = f.get("message") or f.get("path") or ""
        line = f.get("line")
        loc = f" (line {line})" if isinstance(line, int) else ""
        out.setdefault(n, []).append(f"{kind}{loc}: {detail}".strip())
    return out


def _frame_render_problem(msgs: list[str]) -> str:
    return ("This slide's pptxgenjs code failed at render time — "
            + " | ".join(msgs)
            + ". Find and fix the cause(s) so the code runs cleanly and the "
            "slide's intended content (see the brief above) is drawn.")


# ---------------------------------------------------------------------------
# critique  (Qwen-VL)
# ---------------------------------------------------------------------------

def critique_slide(png_path: Path, slide_n: int, slide_title: str) -> dict[str, Any]:
    """Ask the visual critic to review one rendered slide image.

    Degrades gracefully: if the vision call fails outright (RITS overloaded /
    Qwen-VL unavailable, even after llm.py's retries), this returns verdict
    'skipped' rather than raising — one dead call must not abort the whole
    critique pass."""
    try:
        content, _ = llm.chat_vision(
            config.ROSTER["critic"], CRITIC_SYSTEM_PROMPT,
            build_critic_user_message(slide_n, slide_title), png_path)
    except Exception as e:  # noqa: BLE001
        log.warning("critique slide %d: vision call failed (%s) — skipping",
                    slide_n, e)
        return {"verdict": "skipped", "issues": []}
    m = _JSON_OBJ.search(content or "")
    if not m:
        return {"verdict": "ok", "issues": []}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"verdict": "ok", "issues": []}
    verdict = "needs_fix" if data.get("verdict") == "needs_fix" else "ok"
    issues = [str(x) for x in (data.get("issues") or []) if str(x).strip()]
    return {"verdict": verdict if issues else "ok", "issues": issues}


# ---------------------------------------------------------------------------
# edit  (gpt-oss-120b)
# ---------------------------------------------------------------------------

def edit_slide_js(deck: dict[str, Any], slide: dict[str, Any],
                  current_js: str, problem: str) -> str:
    """Ask the editor to fix `problem` in one slide's JS. Returns new JS."""
    messages = [
        {"role": "system", "content": EDITOR_SYSTEM_PROMPT},
        {"role": "user", "content": build_editor_user_message(
            deck, slide, current_js, problem)},
    ]
    content, _ = llm.chat(config.ROSTER["editor"], messages)
    return _strip_fence(content)


def _rewrite_slide_js(out_dir: Path, deck: dict[str, Any],
                      slide_n: int, problem: str) -> None:
    """Edit slide `slide_n`'s JS in place. Keeps a .prev.js backup (ignored by
    the renderer, whose slide collector only accepts integer-named files)."""
    slide = _slide_by_n(deck, slide_n)
    js_path = out_dir / "output_js" / f"{slide_n:02d}.js"
    if slide is None or not js_path.exists():
        raise ValueError(f"slide {slide_n} is not editable (no brief or JS)")
    current = js_path.read_text()
    new_js = edit_slide_js(deck, slide, current, problem)
    (out_dir / "output_js" / f"{slide_n:02d}.prev.js").write_text(current)
    js_path.write_text(new_js)
    log.info("rewrote slide %d JS (%d -> %d chars)",
             slide_n, len(current), len(new_js))


def _rerender(out_dir: Path) -> tuple[Path, list[Path]]:
    pptx = render(out_dir, out_dir / "deck.json")
    return pptx, render_previews(out_dir, pptx)


def apply_nl_edit(out_dir: Path, deck: dict[str, Any], slide_n: int,
                  instruction: str) -> dict[str, Any]:
    """One user-driven edit on one slide, then re-render the deck."""
    out_dir = Path(out_dir)
    _rewrite_slide_js(out_dir, deck, slide_n, instruction)
    pptx, previews = _rerender(out_dir)
    return {"pptx": pptx, "previews": previews}


def repair_render_errors(out_dir: Path, deck: dict[str, Any],
                         max_passes: int, progress: Progress | None = None,
                         ) -> list[int]:
    """Build-time self-heal: while the deck has render errors, fix the failing
    slides with the editor and re-render. Used inside generate_deck so a built
    deck's slides actually run.

    This path never calls Qwen — it only needs the renderer's error output and
    the gpt-oss-120b editor, so it is unaffected by a Qwen-VL outage.

    Returns the slide numbers still failing after max_passes (best-effort — the
    renderer's per-slide try/catch keeps a stubborn slide from breaking the
    rest of the deck)."""
    out_dir = Path(out_dir)
    for p in range(1, max_passes + 1):
        rfails = _load_render_failures(out_dir)
        if not rfails:
            return []
        log.info("build repair pass %d: %d slide(s) with render errors",
                 p, len(rfails))
        done = 0
        with ThreadPoolExecutor(max_workers=config.CODER_WORKERS) as ex:
            # copy_context() per submit so worker logs route to the active
            # session log (the contextvar lives in app.py).
            futs = {
                ex.submit(contextvars.copy_context().run,
                          _rewrite_slide_js, out_dir, deck, n,
                          _frame_render_problem(msgs)): n
                for n, msgs in rfails.items()
            }
            for fut in as_completed(futs):
                n = futs[fut]
                try:
                    fut.result()
                except Exception as e:  # noqa: BLE001
                    log.warning("repair slide %d failed: %s", n, e)
                done += 1
                if progress:
                    progress(f"repairing slides (pass {p})", done, len(rfails))
        render(out_dir, out_dir / "deck.json")
    return sorted(_load_render_failures(out_dir).keys())


def visual_refine_pass(out_dir: Path, deck: dict[str, Any],
                       progress: Progress | None = None) -> dict[str, Any]:
    """ONE automatic visual-critique pass, run inside the build.

    Qwen-VL reviews each rendered slide; the slides it flags go to the editor;
    the deck re-renders once. This is the whole pass — there is no loop and no
    explicit trigger.

    Per-slide issue text goes to the log only. The caller gets a concise
    summary — {critiqued, skipped, flagged, fixed} — for a one-line UI message,
    not a wall of per-issue text.

    Degrades cleanly: if the critic is unavailable every slide comes back
    'skipped' and the pass is a no-op.
    """
    out_dir = Path(out_dir)
    slides = deck.get("slides", [])
    previews = sorted(out_dir.glob("slide-*.png"))

    flagged: dict[int, str] = {}
    critiqued = 0
    skipped = 0
    for idx, slide in enumerate(slides):
        n = slide.get("n")
        if not isinstance(n, int) or n - 1 >= len(previews):
            continue
        if progress:
            progress("reviewing slides", idx + 1, len(slides))
        c = critique_slide(previews[n - 1], n, slide.get("slide_title", ""))
        if c["verdict"] == "skipped":
            skipped += 1
            continue
        critiqued += 1
        if c["verdict"] == "needs_fix":
            flagged[n] = "; ".join(c["issues"])
            log.info("visual critique slide %d: %s", n, flagged[n])

    fixed: list[int] = []
    if flagged:
        items = list(flagged.items())
        with ThreadPoolExecutor(max_workers=config.CODER_WORKERS) as ex:
            futs = {ex.submit(contextvars.copy_context().run,
                              _rewrite_slide_js, out_dir, deck, n, prob): n
                    for n, prob in items}
            done = 0
            for fut in as_completed(futs):
                n = futs[fut]
                try:
                    fut.result()
                    fixed.append(n)
                except Exception as e:  # noqa: BLE001
                    log.warning("visual fix slide %d failed: %s", n, e)
                done += 1
                if progress:
                    progress("fixing slides", done, len(items))
        _rerender(out_dir)

    return {
        "critiqued": critiqued,
        "skipped": skipped,
        "flagged": sorted(flagged.keys()),
        "fixed": sorted(fixed),
    }


# ---------------------------------------------------------------------------
# geometry repair  (deterministic detector + gpt-oss-120b, no VLM)
# ---------------------------------------------------------------------------

GEOMETRY_REPAIR_PROMPT = """You are a slide-layout repair engine for a pptxgenjs deck. You are given:
  1. The pptxgenjs JavaScript that draws ONE slide. This slide is BROKEN.
  2. Geometry facts measured from that slide's rendered output.

THE GEOMETRY FACTS ARE GROUND TRUTH. They were measured by a precise, deterministic tool with ZERO false positives - every overlap, overflow, glyph collision, and off-canvas element listed is real and visible in the render. Do not dismiss any of them as intentional, minor, or acceptable. Every one must be gone from your output.

YOUR TASK is a real code-editing job, not a description task: rewrite this slide's JavaScript so that EVERY measured defect is eliminated. You must change the code - coordinates, sizes, font sizes, box dimensions, or the layout structure - and the code you output must reflect those changes. Do not output the original code unchanged.

YOUR CHANGES MUST BE BIG ENOUGH. Size each fix to the measured number: if the facts report a 0.6in overflow, free at least 0.6in; if two regions overlap, move them until they are clearly apart. A change too small to clear the measured defect is a failure - the slide stays broken. Do not under-correct.

NAME THE ROOT CAUSE in terms of the code: which elements, which coordinates / sizes / dimensions are responsible, and WHY they produce the measured defect. The geometry tells you WHERE the problem shows; the code tells you WHY.

HOW TO FIX - you may change the layout freely, you are not limited to small nudges. Moves to weigh (options, not an exhaustive list):
  - RESIZE a box taller or wider.
  - RESIZE FONTS - change the font SIZE (and line spacing) so content fits. Only the size, never the typeface.
  - REPOSITION / CASCADE - move an element, and everything stacked below it, to new coordinates.
  - SPLIT or RE-WRAP TEXT - insert a newline escape (backslash-n) inside a string to wrap a long line across two or three lines, or merge short ones. The words stay the same; only their line layout changes. This is often the simplest fix for a too-long title overflowing its box.
  - REFLOW INTO COLUMNS - a tall list or block that does not fit its vertical space becomes 2, 3, or 4 side-by-side columns.
  - RE-LAY-OUT THE WHOLE SLIDE - replace a cramped arrangement with a cleaner one, such as a single row of equal columns.

FEASIBILITY - the most common mistake, do not make it: before enlarging a box, check there is room to grow into. Add up the vertical space the stacked elements need and compare it to the height available. If enlarging would push content off-slide or into the footer, enlarging is NOT the fix - reflow into columns, shrink the font, or re-lay-out instead.

CONSTRAINTS - this is a layout fix only:
  - Do NOT invent, drop, or reword content. "Content" means the WORDS - the actual text strings. Every text string from the original must still appear with the same words in the same order. FORMATTING is fully yours to change: you CAN and SHOULD insert newline escapes (the JavaScript two-character backslash-n) inside a string to wrap a long title across two or three lines, adjust font sizes, resize and reposition boxes, change spacing, change line height, change box widths. Adding a newline or whitespace inside a string to wrap it is a LAYOUT change, NOT a content change - that is exactly what you are here to do.
  - PRESERVE THE DECK'S IDENTITY: never change a typeface (fontFace) or any colour. Font SIZE may change; the typeface and colours may not.

CANVAS: 13.333in wide x 7.5in tall. A footer occupies the strip below y 6.9.

OUTPUT - two parts, nothing else:
  1. DIAGNOSIS: go through the geometry facts one defect at a time. For each, name it and state the exact code change that fixes it - which element, which property, old value -> new value.
  2. The complete corrected JavaScript for this slide, in a single ```js ... ``` code fence, with every change from your diagnosis applied."""


_JS_FENCE = re.compile(r"```(?:js|javascript)?\s*\n(.*?)\n```", re.DOTALL)


def _extract_fenced_js(text: str) -> str:
    """Pull the fenced JS out of the repair model's 'diagnosis + code' reply.
    A reply with no fence is almost certainly unusable — but that is left to
    the verify gate to catch (a broken slide fails the defect/content check
    and is reverted), so this never has to guess."""
    m = _JS_FENCE.search(text)
    return m.group(1).strip() if m else _strip_fence(text)


def _geometry_rewrite(current_js: str, facts: str) -> str:
    """One-shot diagnose + rewrite for a geometry-flagged slide."""
    user = (f"SLIDE JAVASCRIPT\n{current_js}\n\n"
            f"MEASURED GEOMETRY (from the rendered output)\n{facts}\n\n"
            f"Diagnose the root cause, then rewrite the slide's JavaScript so "
            f"the measured defects are gone.")
    content, _ = llm.chat(config.ROSTER["editor"], [
        {"role": "system", "content": GEOMETRY_REPAIR_PROMPT},
        {"role": "user", "content": user},
    ], max_tokens=16000)
    return _extract_fenced_js(content)


def geometry_refine_pass(out_dir: Path, deck: dict[str, Any],
                         progress: Progress | None = None,
                         max_passes: int = 2) -> dict[str, Any]:
    """Deterministic geometry-repair pass — run inside the build, no VLM.

    ITERATIVE: each pass re-detects, repairs the still-flagged slides, and
    verifies. The verify gate accepts on *strictly fewer* defects, not zero —
    so a slide that improves from 5 → 3 defects is accepted but still broken.
    A second pass picks up that under-correction. Slides reverted on a pass
    are NOT retried in later passes — same prompt + temp 0 = same revert.

    Returns {flagged, accepted, reverted, passes} — slide-number lists across
    all passes, for a one-line UI summary.
    """
    out_dir = Path(out_dir)
    pdf = out_dir / "deck.pdf"
    empty = {"flagged": [], "accepted": [], "reverted": [], "passes": 0}
    if not pdf.exists():
        log.warning("geometry_refine_pass: no deck.pdf — skipping")
        return empty

    slides = deck.get("slides", [])
    n_slides = len(slides)

    def _repair(n: int) -> str:
        """Rewrite slide n's JS in place; return the JS we started from."""
        js_path = out_dir / "output_js" / f"{n:02d}.js"
        cur = js_path.read_text()
        new = _geometry_rewrite(cur, detector.geometry_facts(pdf, n))
        js_path.write_text(new)
        return cur

    flagged_total: set[int] = set()
    accepted_total: set[int] = set()
    reverted_total: set[int] = set()
    passes_run = 0

    for pass_num in range(1, max_passes + 1):
        # 1. detect — read the CURRENT deck.pdf (after any prior pass's edits)
        before: dict[int, dict] = {}
        for idx, slide in enumerate(slides):
            n = slide.get("n")
            if not isinstance(n, int):
                continue
            if progress:
                progress(f"identifying layout errors (pass {pass_num})",
                         idx + 1, n_slides)
            try:
                summary = detector.defect_summary(pdf, n)
            except Exception as e:  # noqa: BLE001
                log.warning("geometry detect slide %d failed: %s", n, e)
                continue
            # skip slides we already attempted and reverted on an earlier pass
            # — same input + same temp=0 repair would just revert again
            if summary["defects"] > 0 and n not in reverted_total:
                before[n] = summary

        if not before:
            log.info("geometry pass %d: nothing to repair — stopping",
                     pass_num)
            break

        flagged_total |= set(before.keys())
        log.info("geometry pass %d: %d slide(s) flagged: %s",
                 pass_num, len(before), sorted(before))

        # 2. repair flagged slides — parallel one-shot model calls
        originals: dict[int, str] = {}
        with ThreadPoolExecutor(max_workers=config.CODER_WORKERS) as ex:
            futs = {ex.submit(contextvars.copy_context().run, _repair, n): n
                    for n in before}
            done = 0
            for fut in as_completed(futs):
                n = futs[fut]
                try:
                    originals[n] = fut.result()
                except Exception as e:  # noqa: BLE001
                    log.warning("geometry repair slide %d failed: %s", n, e)
                done += 1
                if progress:
                    progress(f"repairing layout (pass {pass_num})",
                             done, len(before))

        # 3. re-render with the rewrites applied
        _rerender(out_dir)

        # 4. verify gate — keep a rewrite only if it measurably improved
        accepted_this: list[int] = []
        reverted_this: list[int] = []
        for n, cur in originals.items():
            try:
                after = detector.defect_summary(out_dir / "deck.pdf", n)
            except Exception:  # noqa: BLE001 — unreadable slide => failed
                after = {"defects": before[n]["defects"] + 1, "textlen": 0}
            improved = after["defects"] < before[n]["defects"]
            kept = after["textlen"] >= 0.85 * before[n]["textlen"]
            if improved and kept:
                accepted_this.append(n)
                log.info("  pass %d slide %d ACCEPT  defects %d -> %d",
                         pass_num, n, before[n]["defects"], after["defects"])
            else:
                (out_dir / "output_js" / f"{n:02d}.js").write_text(cur)
                reverted_this.append(n)
                log.info("  pass %d slide %d REVERT  defects %d -> %d",
                         pass_num, n, before[n]["defects"], after["defects"])

        # 5. re-render if anything reverted, so deck.pdf matches the JS
        if reverted_this:
            _rerender(out_dir)

        accepted_total |= set(accepted_this)
        reverted_total |= set(reverted_this)
        passes_run = pass_num

        log.info("geometry pass %d: %d fixed, %d reverted",
                 pass_num, len(accepted_this), len(reverted_this))

        # nothing accepted this pass => no progress possible, stop
        if not accepted_this:
            log.info("geometry pass %d: no progress — stopping", pass_num)
            break

    # final reverted = flagged but never accepted by any pass
    final_reverted = flagged_total - accepted_total
    log.info("geometry pass complete: %d fixed, %d reverted (%d pass(es))",
             len(accepted_total), len(final_reverted), passes_run)
    return {
        "flagged": sorted(flagged_total),
        "accepted": sorted(accepted_total),
        "reverted": sorted(final_reverted),
        "passes": passes_run,
    }
