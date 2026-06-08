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

# The LoRA sometimes emits a broken "accent-highlight" pattern: it splits a
# label using `label.replace(fact, "")` and appends the fact as a separately
# styled fragment. When the fact is not actually a substring of the label
# the replace is a no-op and the fact gets DUPLICATED at the end of the
# rendered line ("founded in 1995.Shenzhen, 1995."). When the fact IS a
# substring the fact gets moved to the end after the period ("rechargeable
# — not cars.batteries."). Either way the rendered bullet is broken.
#
# Diagnosed 2026-05-27 on PresentBench MS-01 slides 3 + 4 (BYD speech) and
# ted_chinese_10 slide 6. Hits judge items Visual 2.16 (malformed text) and
# Fundamentals 1.12 (grammatical accuracy) directly.
#
# We handle this by injecting a tiny helper, _hSplit, at the top of any JS
# file that contains `.replace(<expr>, "")`, and rewriting both pattern
# shapes to call the helper. The helper places the accent-styled fact in
# its correct position inside the label rather than at the end.

_HIGHLIGHT_HELPER_JS = """\
// Auto-injected highlight-split helper: places an accent-styled `fact`
// at its correct position inside `label`. Replaces a broken LoRA pattern
// that used label.replace(fact, "") + a trailing accent fragment.
function _hSplit(label, fact, normalOpt, accentOpt) {
  var s = String(label == null ? "" : label);
  var h = String(fact == null ? "" : fact);
  if (!h) return [{ text: s, options: normalOpt }];
  var i = s.indexOf(h);
  if (i < 0) return [{ text: s, options: normalOpt }];
  var runs = [];
  if (i > 0) runs.push({ text: s.slice(0, i), options: normalOpt });
  runs.push({ text: h, options: accentOpt });
  var tail = s.slice(i + h.length);
  if (tail) runs.push({ text: tail, options: normalOpt });
  return runs;
}

"""

# Pattern A: 3-fragment addText with .replace + bold fact + trailing "."
# As seen on MS-01 slides 3 and 4. Captures the label expr, fact expr, and
# the two options blocks so we can hand them to _hSplit.
_HIGHLIGHT_BUG_3FRAG = re.compile(
    r"""slide\.addText\(\[\s*
        \{\s*text:\s*(?P<label>[^,]+?)\s*\.\s*replace\(\s*(?P<fact>[^,]+?)\s*,\s*""\s*\)\s*,
        \s*options:\s*(?P<normal>\{[^{}]*\})\s*\}\s*,\s*
        \{\s*text:\s*(?P=fact)\s*,\s*options:\s*(?P<accent>\{[^{}]*\bbold\s*:\s*true[^{}]*\})\s*\}\s*,\s*
        \{\s*text:\s*"\."\s*,\s*options:\s*\{[^{}]*\}\s*\}\s*,?\s*
        \](?P<rest>\s*,)""",
    re.VERBOSE | re.DOTALL,
)

# Pattern B: var-assigned + 2-fragment addText. Seen on ted_chinese_10
# slide 6. The var `cleanX = orig.replace(fact, "")` is rewritten to
# preserve the original (we don't need it any more), and the 2-fragment
# addText is rewritten to use _hSplit.
_HIGHLIGHT_BUG_2FRAG_VAR = re.compile(
    r"""var\s+(?P<var>\w+)\s*=\s*(?P<label>[^;]+?)\s*\.\s*replace\(\s*(?P<fact>[^,]+?)\s*,\s*""\s*\)\s*;
        \s*var\s+(?P<runs>\w+)\s*=\s*\[\s*
        \{\s*text:\s*(?P=var)\s*,\s*options:\s*(?P<normal>\{[^{}]*\})\s*\}\s*,\s*
        \{\s*text:\s*(?P=fact)\s*,\s*options:\s*(?P<accent>\{[^{}]*\bbold\s*:\s*true[^{}]*\})\s*\}\s*,?\s*
        \]\s*;""",
    re.VERBOSE | re.DOTALL,
)

# Pattern C: 2-fragment inline addText, no terminal "." fragment, no var
# pre-assignment. Seen on MS-01 slide 4 (BYD timeline). Same shape as A
# but only two fragments. Kept as a separate regex rather than making A
# tolerant of either-arity, to keep each pattern's intent obvious.
_HIGHLIGHT_BUG_2FRAG = re.compile(
    r"""slide\.addText\(\[\s*
        \{\s*text:\s*(?P<label>[^,]+?)\s*\.\s*replace\(\s*(?P<fact>[^,]+?)\s*,\s*""\s*\)\s*,
        \s*options:\s*(?P<normal>\{[^{}]*\})\s*\}\s*,\s*
        \{\s*text:\s*(?P=fact)\s*,\s*options:\s*(?P<accent>\{[^{}]*\bbold\s*:\s*true[^{}]*\})\s*\}\s*,?\s*
        \](?P<rest>\s*,)""",
    re.VERBOSE | re.DOTALL,
)

# Adjacent-runs missing-space bug. The LoRA emits a 2-fragment array
#   [{ text: <X>, options: {color: palette.accent, bold: true} },
#    { text: <Y>, options: {color: palette.dark_text} }]
# meaning to render "<X> <Y>" (accent + body). It forgets the space:
# the rendered output is "<X><Y>" — "second-largestProducer of...",
# "AwardRecognition from...", "TodayChina's largest car company".
#
# Diagnosed 2026-05-27 on MS-01 neutral-palette slide 5. The fix
# wraps the second fragment's text expression in a runtime guard that
# inserts a leading space when the value is a non-empty string that
# doesn't already start with whitespace AND the first fragment's text
# doesn't end with whitespace or terminal punctuation. The wrapper is
# a no-op for the (rare) legitimate case where two adjacent runs are
# intentionally concatenated without a separator.
#
# We need a runtime guard rather than a static string-edit because the
# fragments' `text` values are commonly variable references (`p.rest`,
# `m.body`, `e.caption`) — we can't peek at them at rewrite time.
_ADJACENT_ACCENT_THEN_BODY = re.compile(
    r"""\{\s*text:\s*(?P<first>[^,{}]+?)\s*,
        \s*options:\s*\{(?P<first_opts>[^{}]*\bcolor\s*:\s*palette\.accent\b[^{}]*)\}\s*\}\s*,\s*
        \{\s*text:\s*(?P<second>[^,{}]+?)\s*,
        \s*options:\s*\{(?P<second_opts>(?:(?!\bcolor\s*:\s*palette\.accent\b)[^{}])*)\}\s*\}""",
    re.VERBOSE,
)


def _fix_adjacent_runs_missing_space(src: str) -> str:
    """Wrap the second fragment's text in a runtime space-prefix guard.

    Only fires when the first fragment's options name `palette.accent` and
    the second fragment's options do NOT. The runtime guard is conservative
    — it leaves the second fragment unchanged unless (a) its value is a
    non-empty string, (b) it doesn't already start with whitespace, and (c)
    the first fragment's value doesn't end with whitespace or terminal
    punctuation that already provides a separator."""
    def _sub(m: re.Match) -> str:
        first, first_opts = m["first"], m["first_opts"]
        second, second_opts = m["second"], m["second_opts"]
        # Wrap the second fragment's text expression. Use a runtime helper
        # rather than a static rewrite so the same guard works whether the
        # value is a literal string or a variable reference.
        wrapped = (f"(typeof {second} === \"string\" && {second}.length && "
                   f"!/^\\s/.test({second}) && "
                   f"!/[\\s.,!?:;\\-—\\u2014]$/.test(String({first})) "
                   f"? \" \" + {second} : {second})")
        return (f"{{ text: {first}, options: {{{first_opts}}} }}, "
                f"{{ text: {wrapped}, options: {{{second_opts}}} }}")
    return _ADJACENT_ACCENT_THEN_BODY.sub(_sub, src)


def _fix_highlight_split_bug(src: str) -> str:
    """Rewrite the broken accent-highlight pattern to use the _hSplit helper.

    Three pattern shapes handled (see comments next to the regexes above).
    The helper is injected exactly once per file, at the top, only when
    a rewrite actually fires."""
    def _sub_inline(m: re.Match) -> str:
        return (f"slide.addText(_hSplit({m['label']}, {m['fact']}, "
                f"{m['normal']}, {m['accent']}){m['rest']}")
    fixed = _HIGHLIGHT_BUG_3FRAG.sub(_sub_inline, src)
    fixed = _HIGHLIGHT_BUG_2FRAG.sub(_sub_inline, fixed)

    def _sub_var(m: re.Match) -> str:
        return (f"var {m['runs']} = _hSplit({m['label']}, {m['fact']}, "
                f"{m['normal']}, {m['accent']});")
    fixed = _HIGHLIGHT_BUG_2FRAG_VAR.sub(_sub_var, fixed)

    if fixed != src and "_hSplit" not in src:
        fixed = _HIGHLIGHT_HELPER_JS + fixed
    return fixed


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


# Matches an `slide.addImage({ ... path: "assets/logos/<file>" ... });` call.
# Optional preceding whitespace on the line is consumed so the strip leaves
# no orphaned blank-indented line. Multi-line addImage calls (with the
# closing `});` on its own line) are matched via re.DOTALL — pptxgenjs
# accepts either layout, and the LoRA emits both shapes.
_BRANDING_LOGO_ADDIMAGE = re.compile(
    r"^[ \t]*slide\.addImage\(\s*\{[^{}]*?"
    r'path:\s*["\']assets/logos/[^"\']*["\'][^{}]*?'
    r"\}\s*\)\s*;\s*$\n?",
    re.MULTILINE | re.DOTALL,
)


def _strip_branding_assets(src: str) -> str:
    """Remove addImage calls whose path points at assets/logos/. PresentBench
    and other non-IBM render targets opt out of the IBM 8-bar brand mark via
    `include_branding_assets=False` on generate_deck; this is what enforces
    it deterministically on the rendered JS."""
    return _BRANDING_LOGO_ADDIMAGE.sub("", src)


# Matches `<key>: <value>;` immediately followed by `\s*\n\s*}` — i.e. a stray
# semicolon at the end of the last property of an object literal. The key must
# be a real identifier, the value cannot contain commas, braces, semicolons,
# or newlines (so we never match across multiple properties or across line
# breaks where the model meant a comma but typed a semicolon mid-block). The
# `\n\s*\}` tail ensures we only fire when the next token is a closing brace,
# which is the unambiguous bug signature; we won't touch semicolons inside
# function bodies, string literals, or `for(;;)` headers.
_SEMI_AS_PROP_TERM = re.compile(
    r"(\b[a-zA-Z_$][\w$]*\s*:\s*[^,{};\n]+?);(\s*\n\s*\})"
)


def _fix_semicolon_as_property_terminator(src: str) -> str:
    """Repair `key: value;` where the `;` is right before the object's closing
    `}`. Node rejects this with `Unexpected token ';'` at parse time, breaking
    the whole slide.

    First observed 2026-06-05 in v3_qwen25_combined's palette_demo slide 4:
    the LoRA emitted `color: palette.muted, charSpacing: 2;` and
    `color: palette.muted, align: "right";` inside two addText option blocks.
    Same root cause both times — the model confuses end-of-statement with
    end-of-property convention. Safe rewrite: drop the `;`.
    """
    return _SEMI_AS_PROP_TERM.sub(r"\1\2", src)


# Matches a single `slide.addText(<arg>, { <opts> });` call. The first arg
# tolerates one level of nested parens (e.g. `String(n)`); the opts tolerate
# one level of nested braces (e.g. `fill: { color: "..." }`). Non-greedy
# matching prevents one call from swallowing the next.
_ADDTEXT_CALL = re.compile(
    r"""slide\.addText\(
        \s*(?P<arg>(?:[^()]|\([^()]*\))*?)\s*,
        \s*\{(?P<opts>(?:[^{}]|\{[^{}]*\})*?)\}
        \s*\)\s*;""",
    re.VERBOSE | re.DOTALL,
)

# Extracts each of `x|y|w|h: <expr>` from an options block. The expression
# stops at the next comma, newline, or closing brace.
_COORD_KEY = re.compile(r"\b(x|y|w|h)\s*:\s*([^,}\n]+)")


def _coords_signature(opts: str) -> tuple | None:
    """Return a (x, y, w, h) tuple of stripped expression strings, or None
    if any of the four coords is missing."""
    found = {}
    for m in _COORD_KEY.finditer(opts):
        found[m.group(1)] = m.group(2).strip()
    if not all(k in found for k in ("x", "y", "w", "h")):
        return None
    return (found["x"], found["y"], found["w"], found["h"])


def _fix_redundant_addtext_at_same_coords(src: str) -> str:
    """Drop redundant `slide.addText(...)` calls that share `x/y/w/h` with a
    later call but have a different first argument.

    LoRA bug observed 2026-06-05 in v3_qwen25_combined's cuga_hackathon slide
    8 (`Policies — agent guardrails`): the model emitted a plain-text addText
    for the syntax-coloured code panel, immediately followed by a rich-text-
    runs addText at the same x/y/w/h. Both calls rendered on top of each
    other and every line appeared doubled. The detector caught the overlaps;
    the editor's fix removed the redundant call but the verify gate then
    reverted because the deduplication halved textlen and tripped the
    content-preservation check.

    Strategy:
      - find every slide.addText(arg, {opts}) block in the file
      - extract each block's coordinate signature (x, y, w, h) from opts
      - if an earlier block's coords match a later block's AND the first
        arguments differ, drop the earlier one
    Same coords + same first arg leaves both intact (rare but plausibly
    intentional). Same coords + different first arg is the bug signature.
    """
    blocks: list[tuple[int, int, tuple, str]] = []
    for m in _ADDTEXT_CALL.finditer(src):
        sig = _coords_signature(m.group("opts"))
        if sig is None:
            continue
        blocks.append((m.start(), m.end(), sig, m.group("arg").strip()))

    drop_indices: set[int] = set()
    for i in range(len(blocks)):
        for j in range(i + 1, len(blocks)):
            if blocks[i][2] == blocks[j][2] and blocks[i][3] != blocks[j][3]:
                drop_indices.add(i)
                break

    if not drop_indices:
        return src

    # Splice removals in reverse order so earlier (still-original) spans stay
    # valid as later spans drop out.
    out = src
    for i in sorted(drop_indices, reverse=True):
        s, e, _, _ = blocks[i]
        # Trim a trailing newline + leading-of-next-line whitespace so the
        # rewrite doesn't leave a blank line where the call used to be.
        while e < len(out) and out[e] in " \t":
            e += 1
        if e < len(out) and out[e] == "\n":
            e += 1
        out = out[:s] + out[e:]
    return out


def _apply_deterministic_js_fixes(js_dir: Path, *,
                                  include_branding_assets: bool = True) -> None:
    """Tier-1 deterministic fixes on generated slide JS — known categorical
    bugs repaired by string rewrite, no model call:
      - iconify namespace bug: 'icons/carbon:NAME' -> 'icons/carbon/NAME'
      - bare icon path:        'icons/NAME.svg'    -> 'icons/carbon/NAME.svg'
      - asset-path remap: a hallucinated/misnamed assets/ path -> a real file
      - highlight-split bug:   `<L>.replace(<F>, "")` + bold-<F> fragment
                               -> _hSplit(<L>, <F>, normalOpt, accentOpt)
      - semicolon-as-property-terminator (v3_qwen25_combined):
                               `key: value;}` -> `key: value}`
      - redundant addText at same coords (v3_qwen25_combined):
                               two slide.addText calls at the same x/y/w/h
                               with different first args -> drop the first
      - branding-asset strip (opt-in via include_branding_assets=False):
                               remove `slide.addImage({path: "assets/logos/..."})`
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
        fixed = _fix_highlight_split_bug(fixed)
        fixed = _fix_adjacent_runs_missing_space(fixed)
        fixed = _fix_semicolon_as_property_terminator(fixed)
        fixed = _fix_redundant_addtext_at_same_coords(fixed)
        if not include_branding_assets:
            fixed = _strip_branding_assets(fixed)
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
                  include_branding_assets: bool = True,
                  progress: Progress | None = None) -> dict[str, Any]:
    """Run the full Stage-2 pipeline. Returns a dict with the parsed deck,
    the .pptx path, the preview PNG paths, and the y-bounds lint warnings.

    include_branding_assets: when False, strips any
    `slide.addImage({path: "assets/logos/..."})` calls from the coder's
    output. Used for non-IBM render targets (e.g. PresentBench) where the
    LoRA's training-data-default of dropping the IBM 8-bar logo on cover
    and thank-you slides is inappropriate. Default True preserves the
    behavior for deck_forge UI and IBM-deck synthesis."""
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
    _apply_deterministic_js_fixes(
        out_dir / "output_js",
        include_branding_assets=include_branding_assets)

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
