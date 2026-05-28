"""Stage 2 — plan.md -> rendered deck.

The fixed generation pipeline (no agent loop here): the designer turns a plan
into a deck brief, the coder turns each slide of the brief into pptxgenjs JS,
then the deck is stitched, rendered, and rasterized to PNG previews.

The designer and coder are the fine-tuned model's two jobs; they are called
through config.ROSTER, so during the stand-in phase they hit gpt-oss-120b and
later the fine-tuned adapter with no change here.
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
import llm
from postprocess import lint_slide_y_bounds, postprocess_deck
from prompts import (
    CODER_SYSTEM_PROMPT,
    DESIGNER_SYSTEM_PROMPT,
    build_coder_user_message,
    build_designer_user_message,
    parse_designer_output,
    split_trace_and_code,
)
from refine import geometry_refine_pass, repair_render_errors
from render import render, render_previews

log = logging.getLogger("pipeline")

Progress = Callable[[str, int, int], None]

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*\n(.*?)\n?\s*```\s*$", re.DOTALL)


def _strip_code_fences(code: str) -> str:
    """Drop a wrapping ```...``` fence if the model added one. split_trace_
    and_code handles <think>, but not markdown fences around the JS body."""
    m = _FENCE.match(code.strip())
    return m.group(1).strip() if m else code.strip()


_ASSET_REF = re.compile(r"""(["'])(assets/[^"']+)\1""")

# The coder sometimes drops the carbon/ segment: 'icons/NAME.svg' instead of
# 'icons/carbon/NAME.svg'. Every icon on disk lives under icons/carbon/, so any
# icons/ path not already pointing there is repaired; (?!carbon/) leaves a
# correct path untouched.
_ICON_BARE = re.compile(r"icons/(?!carbon/)([\w.+-]+\.svg)")


def _remap_asset_path(path: str) -> str:
    """Snap an asset path onto a real file on disk. An exact match passes
    through; a near-miss logo path goes to the real logo; a near-miss cover
    path fuzzy-matches a real cover by filename tokens. A truly unrecognizable
    assets/ path is left as-is — the renderer's missing-image guard skips it
    harmlessly."""
    if (config.ROOT / path).exists():
        return path
    assets = config.available_assets()
    low = path.lower()
    if "/logos/" in low or "logo" in low:
        return assets["logos"][0] if assets["logos"] else path
    if "/covers/" in low or "cover" in low:
        covers = assets["covers"]
        if not covers:
            return path
        want = set(re.split(r"[^a-z0-9]+", Path(path).stem.lower()))
        return max(covers, key=lambda c: len(
            want & set(re.split(r"[^a-z0-9]+", Path(c).stem.lower()))))
    return path


def _apply_deterministic_js_fixes(js_dir: Path) -> None:
    """Tier-1 deterministic fixes on generated slide JS — known categorical
    bugs repaired by string rewrite, no model call:
      - iconify namespace bug: 'icons/carbon:NAME' -> 'icons/carbon/NAME'
      - bare icon path:        'icons/NAME.svg'    -> 'icons/carbon/NAME.svg'
      - asset-path remap: a hallucinated/misnamed assets/ path -> a real file
    Bugs a regex cannot safely fix (apostrophe quoting, addText string arrays)
    are left to the render-error repair loop."""
    for p in sorted(js_dir.glob("*.js")):
        if not p.stem.isdigit():
            continue
        src = p.read_text()
        fixed = src.replace("icons/carbon:", "icons/carbon/")
        fixed = _ICON_BARE.sub(r"icons/carbon/\1", fixed)
        fixed = _ASSET_REF.sub(
            lambda m: m.group(1) + _remap_asset_path(m.group(2)) + m.group(1),
            fixed)
        if fixed != src:
            p.write_text(fixed)
            log.info("deterministic JS fixes applied to %s", p.name)


# ---------------------------------------------------------------------------
# designer
# ---------------------------------------------------------------------------

def run_designer(plan_md: str, available_icons: list[str],
                 palette_family: str, *, attempts: int = 4
                 ) -> tuple[str, dict[str, Any] | None, int]:
    """plan.md -> deck brief. Returns (raw_output, parsed_deck_or_None).

    Attempt 1 runs at the adapter's configured temperature (0.0 — deterministic
    and free of JSON-syntax sampling slips). Retries use a SMALL bump
    (temperature=0.3): a temp-0 failure — a rare harmony-token degeneration
    (HTTP 500) or a JSON parse slip — repeats verbatim at temp 0, so a small
    resampling is needed to escape it. NOT 1.0: at temp=1.0 the LoRA goes off
    the rails (verified — for a plan about IBM Agentic Middleware, temp=1.0
    produced a 5-slide deck about space launches: "Axiom fresh", "Vega and
    Polaris", "International Institute of Space Systems")."""
    messages = [
        {"role": "system", "content": DESIGNER_SYSTEM_PROMPT},
        {"role": "user", "content": build_designer_user_message(
            plan_md, available_icons, palette_family)},
    ]
    spec = config.ROSTER["designer"]
    last = ""
    for i in range(1, attempts + 1):
        temp = None if i == 1 else 0.3   # attempt 1: spec temp; retries: small bump
        try:
            content, _ = llm.chat(spec, messages, temperature=temp)
            last = content
            return content, parse_designer_output(content), i
        except Exception as e:
            log.warning("designer attempt %d/%d failed (temp=%s): %s", i,
                        attempts, spec.temperature if temp is None else temp, e)
    return last, None, attempts


# ---------------------------------------------------------------------------
# coder
# ---------------------------------------------------------------------------

def _code_one_slide(deck: dict[str, Any], slide: dict[str, Any],
                    prior_titles: list[str], *, attempts: int = 3
                    ) -> tuple[int, str | None, str, int]:
    """Code one slide. Attempt 1 at the adapter's temperature (0.0); retries
    use a SMALL bump (0.3) to resample out of a rare harmony-token
    degeneration. NEVER 1.0 — that sends the LoRA off the rails and produces
    content unrelated to the brief. If every attempt fails the slide yields
    empty JS — the render's per-slide guard keeps the rest of the deck intact
    rather than aborting the build."""
    messages = [
        {"role": "system", "content": CODER_SYSTEM_PROMPT},
        {"role": "user", "content": build_coder_user_message(
            deck, slide, prior_titles)},
    ]
    spec = config.ROSTER["coder"]
    for i in range(1, attempts + 1):
        temp = None if i == 1 else 0.3
        try:
            content, _ = llm.chat(spec, messages, temperature=temp)
            trace, code = split_trace_and_code(content)
            return slide["n"], trace, _strip_code_fences(code), i
        except Exception as e:
            log.warning("coder slide %d attempt %d/%d failed (temp=%s): %s",
                        slide["n"], i, attempts,
                        spec.temperature if temp is None else temp, e)
    return slide["n"], None, "", attempts


def retry_slide(deck: dict[str, Any], out_dir: Path, slide_n: int,
                *, temperature: float = 0.3) -> dict[str, Any]:
    """Re-run the coder for one slide with a small temperature kick (default
    0.3 — same as the run_coder retry temp). Same brief, same plan; the only
    knob is sampling. Used by the per-slide Retry button in the UI when a
    slide looks like a bad roll.

    Then runs a single-slide geometry pass — detector + editor + verify gate —
    so a re-rolled slide gets the same post-processing the full build gives.
    Without this, Retry would publish raw coder output and silently skip the
    correction layer.

    Writes the new JS in place, keeping a .prev.js backup so a regeneration
    can be inspected against the previous attempt.

    Returns a summary {'slide_n', 'geometry'} with the geometry-pass result
    (flagged/accepted/reverted/passes), for the UI to surface."""
    # late import — refine imports pipeline transitively only for the editor
    # path, so importing refine at module top creates a cycle.
    from refine import geometry_refine_pass

    slide = next((s for s in deck["slides"] if s["n"] == slide_n), None)
    if slide is None:
        raise ValueError(f"slide {slide_n} not in deck")
    prior = [s["slide_title"] for s in deck["slides"] if s["n"] < slide_n]
    messages = [
        {"role": "system", "content": CODER_SYSTEM_PROMPT},
        {"role": "user", "content": build_coder_user_message(
            deck, slide, prior)},
    ]
    spec = config.ROSTER["coder"]
    content, _ = llm.chat(spec, messages, temperature=temperature)
    _, code = split_trace_and_code(content)
    code = _strip_code_fences(code)
    js_path = out_dir / "output_js" / f"{slide_n:02d}.js"
    if js_path.exists():
        (out_dir / "output_js" / f"{slide_n:02d}.prev.js").write_text(
            js_path.read_text())
    js_path.write_text(code)
    log.info("retried slide %d at temp %.1f (%d chars)",
             slide_n, temperature, len(code))

    # Re-render once so the detector sees the new JS, then geometry-pass
    # just this slide. The caller still does the final render to refresh
    # all previews (the .pptx + PNGs the UI shows).
    render(out_dir, out_dir / "deck.json")
    geometry = geometry_refine_pass(out_dir, deck,
                                    slide_filter={slide_n},
                                    max_passes=1)
    return {"slide_n": slide_n, "geometry": geometry}


def run_coder(deck: dict[str, Any], out_dir: Path, *,
              workers: int, progress: Progress | None = None) -> list[int]:
    """Code every slide. Coder calls fan out in parallel — each slide's
    prior_titles are fully known from the brief, so there is no dependency
    between calls. Returns the sorted list of slide numbers that needed a
    temperature-bumped retry (attempts > 1) — the caller surfaces this so the
    user knows which slides were generated off the LoRA's primary trajectory."""
    js_dir = out_dir / "output_js"
    js_dir.mkdir(parents=True, exist_ok=True)
    slides = deck["slides"]
    titles = [s["slide_title"] for s in slides]
    total = len(slides)

    preds: list[dict[str, Any]] = []
    retried: list[int] = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        # copy_context() per submission so the per-session log handler's
        # contextvar filter sees this build's session id from every worker
        # thread. Without this, ThreadPoolExecutor workers run in a fresh
        # context and their LLM-call logs go to nobody's file.
        futures = [
            ex.submit(contextvars.copy_context().run,
                      _code_one_slide, deck, s, titles[:i])
            for i, s in enumerate(slides)
        ]
        for fut in as_completed(futures):
            n, trace, code, attempt_used = fut.result()
            (js_dir / f"{n:02d}.js").write_text(code)
            preds.append({"slide_n": n, "trace": trace, "output_js": code})
            if attempt_used > 1:
                retried.append(n)
            done += 1
            if progress:
                progress("coding slides", done, total)

    preds.sort(key=lambda p: p["slide_n"])
    (out_dir / "predictions.jsonl").write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in preds))
    log.info("coded %d slides", total)
    return sorted(retried)


# ---------------------------------------------------------------------------
# end to end
# ---------------------------------------------------------------------------

def generate_deck(plan_md: str, out_dir: Path | str, *,
                  deck_id: str = "deck", palette_family: str = "ibm_watsonx",
                  available_icons: list[str] | None = None,
                  workers: int | None = None,
                  progress: Progress | None = None) -> dict[str, Any]:
    """Run the full Stage-2 pipeline. Returns a dict with the parsed deck,
    the .pptx path, the preview PNG paths, and the y-bounds lint warnings."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if available_icons is None:
        available_icons = config.available_icons()
    if workers is None:
        workers = config.CODER_WORKERS

    # 1. designer
    if progress:
        progress("designing deck", 0, 1)
    raw, deck, designer_attempt = run_designer(
        plan_md, available_icons, palette_family)
    (out_dir / "raw_designer_output.txt").write_text(raw)
    if deck is None:
        raise RuntimeError(
            "designer output did not parse as a deck JSON — see "
            f"{out_dir / 'raw_designer_output.txt'}")

    # 2. deterministic post-processing of the brief
    deck, warns = postprocess_deck(deck, deck_id)
    for w in warns:
        log.info("postprocess: %s", w)
    (out_dir / "deck.json").write_text(
        json.dumps(deck, indent=2, ensure_ascii=False))

    n_slides = len(deck.get("slides", []))
    log.info("designed deck: %r (%d slides)",
             deck.get("deck_title", ""), n_slides)

    # 3. coder (parallel over slides)
    coder_retried = run_coder(deck, out_dir, workers=workers, progress=progress)

    # 4. deterministic JS fixes — known categorical bugs, no model call
    _apply_deterministic_js_fixes(out_dir / "output_js")

    # 5. render
    if progress:
        progress("rendering deck", 0, 1)
    render(out_dir, out_dir / "deck.json")

    # 6. build-time repair — self-heal render errors before handing the deck
    #    back (render error -> editor -> re-render, capped). Qwen is never
    #    called here, so a Qwen-VL outage cannot block a build.
    remaining = repair_render_errors(
        out_dir, deck, config.MAX_BUILD_REPAIR, progress)
    if remaining:
        log.warning("build: %d slide(s) still failing after repair: %s",
                    len(remaining), remaining)

    # 7. previews — needed by the UI and by the visual critic in step 8
    render_previews(out_dir, out_dir / "deck.pptx")

    # 8. deterministic geometry-repair pass — the detector flags layout
    #    defects, the repair model rewrites those slides, a verify gate keeps
    #    only the measurable wins. No VLM. Config-gated.
    geometry: dict[str, Any] = {"flagged": [], "accepted": [], "reverted": []}
    if config.AUTO_GEOMETRY_PASS:
        geometry = geometry_refine_pass(out_dir, deck, progress=progress)

    # 9. final state
    pptx = out_dir / "deck.pptx"
    previews = sorted(out_dir.glob("slide-*.png"))
    lint = lint_slide_y_bounds(out_dir)
    if lint:
        log.info("y-lint: %d issue(s)", len(lint))

    return {
        "deck": deck,
        "pptx": pptx,
        "previews": previews,
        "lint": lint,
        "repair_remaining": remaining,
        "geometry": geometry,
        "retries": {
            "designer_attempt": designer_attempt,
            "coder_retried_slides": coder_retried,
        },
        "out_dir": out_dir,
    }
