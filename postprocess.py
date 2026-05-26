"""Deterministic fixes for designer hallucinations.

Three failure modes observed on held-out eval that this module repairs in
one pass over a deck.json:

  1. Hex tokens emitted with `0x` or `#` prefix. pptxgenjs falls back to
     "000000" for any unrecognized color, producing all-black decks.
     Fix: strip the prefix, validate 6-char hex.

  2. is_dark inconsistent with bg brightness, AND/OR dark_text or primary
     have the same brightness as bg. Result: titles and naked-on-bg text
     render invisibly. Fix: compute W3C-style luminance from bg, force
     is_dark to match, replace any text token that has the same dark/light
     classification as bg with a sane contrasting default.

  3. deck_id hallucinated (e.g. "p002" for a plan named cpt009.md).
     Doesn't affect rendering but breaks anything keyed by id.
     Fix: force-set to the expected id (filename stem).

Designed to be:
  - Deterministic. Same input always produces same output.
  - Idempotent. Running twice is a no-op.
  - Surgical. Only touches the fields it knows about; passes everything
    else through unchanged.
  - Verbose. Returns a list of warnings describing every change so the
    fix is auditable, not magic.

CLI:
    # in-place fix one deck.json:
    python -m palette_training.postprocess eval/pipeline/cpt009/deck.json --write

    # dry-run, just show what would change:
    python -m palette_training.postprocess eval/pipeline/cpt009/deck.json

    # batch — every deck.json under a tree:
    python -m palette_training.postprocess eval/pipeline/ --write
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


_HEX6 = re.compile(r"^[0-9A-Fa-f]{6}$")

# Sane defaults to use when we have to replace an invisible text token.
# Picked to be near-pure on either end so the contrast hit is decisive.
_DEFAULT_LIGHT_TEXT = "E0E0E0"
_DEFAULT_DARK_TEXT = "1A1A1A"
_DEFAULT_LIGHT_FILL = "FFFFFF"

# Luminance threshold for dark/light classification. 0.18 is the W3C-suggested
# midpoint for body-text contrast in the WCAG 2.1 reasoning. Empirically:
# values below ~0.18 all read "this is a dark deck" to the eye.
_LUMINANCE_DARK_THRESHOLD = 0.18


# ---------------------------------------------------------------------------
# Hex normalization
# ---------------------------------------------------------------------------

def _strip_hex_prefix(token: str) -> tuple[str, str | None]:
    """Return (cleaned_token, prefix_stripped_or_None)."""
    s = token.strip()
    if s.startswith(("0x", "0X")):
        return s[2:], "0x"
    if s.startswith("#"):
        return s[1:], "#"
    return s, None


def normalize_hex(token: str) -> tuple[str, list[str]]:
    """Strip prefix + uppercase + validate. Returns (clean_or_original, warns)."""
    if not isinstance(token, str):
        return token, [f"non-string hex value {token!r} (left as-is)"]
    cleaned, prefix = _strip_hex_prefix(token)
    warns: list[str] = []
    if prefix is not None:
        warns.append(f"stripped {prefix!r} prefix from {token!r}")
    if not _HEX6.match(cleaned):
        warns.append(f"invalid hex {token!r} after cleanup ({cleaned!r}); left as-is")
        return token, warns
    return cleaned.upper(), warns


# ---------------------------------------------------------------------------
# Luminance / contrast logic
# ---------------------------------------------------------------------------

def _srgb_linear(c: int) -> float:
    """sRGB → linear-light per WCAG 2.x."""
    f = c / 255.0
    return f / 12.92 if f <= 0.03928 else ((f + 0.055) / 1.055) ** 2.4


def relative_luminance(hex6: str) -> float:
    """W3C relative luminance, 0..1. Caller must pass a clean 6-char hex."""
    r = int(hex6[0:2], 16)
    g = int(hex6[2:4], 16)
    b = int(hex6[4:6], 16)
    return 0.2126 * _srgb_linear(r) + 0.7152 * _srgb_linear(g) + 0.0722 * _srgb_linear(b)


def is_dark_color(hex6: str) -> bool:
    """True if the 6-char hex reads as a dark color."""
    return relative_luminance(hex6) < _LUMINANCE_DARK_THRESHOLD


# ---------------------------------------------------------------------------
# Palette repair
# ---------------------------------------------------------------------------

def _normalize_all_tokens(tokens: dict[str, Any], warns: list[str]) -> None:
    """In-place hex cleanup on every string-valued token."""
    for k, v in list(tokens.items()):
        if isinstance(v, str):
            cleaned, sub_warns = normalize_hex(v)
            tokens[k] = cleaned
            for w in sub_warns:
                warns.append(f"palette.tokens.{k}: {w}")


def _fix_is_dark(palette: dict[str, Any], bg: str, warns: list[str]) -> bool:
    """Force is_dark to match bg's actual brightness. Returns the resolved bool."""
    bg_dark = is_dark_color(bg)
    declared = palette.get("is_dark")
    if declared != bg_dark:
        warns.append(
            f"palette.is_dark: {declared!r} → {bg_dark} "
            f"(corrected from bg luminance of {bg!r})"
        )
        palette["is_dark"] = bg_dark
    return bg_dark


def _fix_text_token_against_bg(
    tokens: dict[str, Any], key: str, bg_dark: bool, bg_hex: str, warns: list[str]
) -> None:
    """If tokens[key] has the same dark/light classification as bg, replace it
    with a sane contrasting default. dark_text and primary both go through here
    because both are used as text colors against bg."""
    val = tokens.get(key)
    if not isinstance(val, str) or not _HEX6.match(val):
        return
    if is_dark_color(val) == bg_dark:
        replacement = _DEFAULT_LIGHT_TEXT if bg_dark else _DEFAULT_DARK_TEXT
        warns.append(
            f"palette.tokens.{key}: {val!r} has same brightness as bg {bg_hex!r}; "
            f"replaced with {replacement!r} for contrast"
        )
        tokens[key] = replacement


def _fix_light_token(tokens: dict[str, Any], warns: list[str]) -> None:
    """`light` is used as card/lift fill. It must actually be light, regardless
    of whether the deck bg is dark or light — cards lift content off the bg."""
    val = tokens.get("light")
    if not isinstance(val, str) or not _HEX6.match(val):
        return
    if is_dark_color(val):
        warns.append(
            f"palette.tokens.light: {val!r} is dark; "
            f"replaced with {_DEFAULT_LIGHT_FILL!r} (light is always a light fill)"
        )
        tokens["light"] = _DEFAULT_LIGHT_FILL


def fix_palette(palette: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return (palette_after_fixes, warnings). Mutates a copy, not the input."""
    palette = json.loads(json.dumps(palette))  # deep copy
    warns: list[str] = []

    tokens = palette.setdefault("tokens", {})
    _normalize_all_tokens(tokens, warns)

    bg = tokens.get("bg")
    if isinstance(bg, str) and _HEX6.match(bg):
        bg_dark = _fix_is_dark(palette, bg, warns)
        _fix_text_token_against_bg(tokens, "dark_text", bg_dark, bg, warns)
        _fix_text_token_against_bg(tokens, "primary", bg_dark, bg, warns)
        _fix_light_token(tokens, warns)

    return palette, warns


# ---------------------------------------------------------------------------
# deck_id
# ---------------------------------------------------------------------------

def fix_deck_id(deck: dict[str, Any], expected_id: str) -> tuple[dict[str, Any], list[str]]:
    deck = json.loads(json.dumps(deck))  # deep copy
    warns: list[str] = []
    actual = deck.get("deck_id")
    if actual != expected_id:
        warns.append(f"deck_id: {actual!r} → {expected_id!r} (forced to filename stem)")
        deck["deck_id"] = expected_id
    return deck, warns


# ---------------------------------------------------------------------------
# Top-level entrypoint
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Y-bounds linter for output_js/<n>.js
# ---------------------------------------------------------------------------
#
# The coder occasionally miscomputes multi-row grid heights (e.g. cellH=4.3
# stacked twice on a 7.5-tall slide → row 2 lands at y=6.55 with content
# extending past the bottom edge). The renderer doesn't catch this because
# pptxgenjs happily accepts off-slide coordinates and just clips at write
# time.
#
# We catch it after-the-fact by sandboxing each slide JS in a Node probe
# that stubs `slide`/`pres`/`palette`/footer helpers, captures every
# addText/addShape/addImage call's bounding box, and flags any whose
# y_top + h exceeds the slide height (or pokes into the footer reserve).

_SLIDE_HEIGHT = 7.5     # pptxgenjs widescreen, matching defineLayout in render.py
_FOOTER_RESERVE = 0.4   # warn if anything bleeds past y=7.1 (footer band starts here)


_LINT_PROBE_TEMPLATE = r"""
const _calls = [];
const palette = {
  is_dark: false, bg: 'F8F9FB', primary: '111827', accent: '8B5CF6',
  secondary_accent: '14B8A6', light: 'FFFFFF', muted: '6B7280', dark_text: '111827',
  typography: { headline_font: 'Sans', body_font: 'Sans' }
};
const pres = { ShapeType: new Proxy({}, { get: () => 'shape' }) };
function _record(method, opts, extra) {
  const o = Object.assign({ method: method }, opts || {});
  if (extra) Object.assign(o, extra);
  _calls.push(o);
}
const slide = {
  background: null,
  addText:  function(t, o) { _record('addText',  o); },
  addShape: function(s, o) { _record('addShape', o, { shape: String(s) }); },
  addImage: function(o)    { _record('addImage', o); },
  addTable: function() {}, addChart: function() {}, addNotes: function() {}
};
const slide_n = __SLIDE_N__; const of_total = __OF_TOTAL__;
function makeShadow() { return {}; }
function softShadow() { return {}; }
function darkFooter() {}
function lightFooter() {}
function connector() {}
try {
  eval(__SLIDE_JS__);
} catch (e) {
  console.error('LINT_EVAL_ERROR:', e.message);
}
console.log(JSON.stringify(_calls));
"""


def _run_lint_probe(slide_js: str, slide_n: int, of_total: int,
                    timeout: int = 30) -> tuple[list[dict], str | None]:
    """Eval one slide's JS in a stubbed sandbox and return (calls, eval_error)."""
    node = shutil.which("node")
    if node is None:
        return [], "node not on PATH; lint skipped"
    probe = (_LINT_PROBE_TEMPLATE
             .replace("__SLIDE_N__", str(slide_n))
             .replace("__OF_TOTAL__", str(of_total))
             .replace("__SLIDE_JS__", json.dumps(slide_js)))
    try:
        proc = subprocess.run([node, "-e", probe],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return [], f"node probe timed out after {timeout}s"
    eval_err: str | None = None
    for line in (proc.stderr or "").splitlines():
        if line.startswith("LINT_EVAL_ERROR:"):
            eval_err = line[len("LINT_EVAL_ERROR:"):].strip()
    last_json = (proc.stdout.strip().splitlines() or [""])[-1]
    try:
        return json.loads(last_json), eval_err
    except json.JSONDecodeError:
        return [], eval_err or "probe produced no parseable JSON output"


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def lint_slide_y_bounds(eval_dir: Path,
                        slide_height: float = _SLIDE_HEIGHT,
                        footer_reserve: float = _FOOTER_RESERVE,
                        ) -> list[str]:
    """Walk eval_dir/output_js/*.js, sandbox-evaluate each slide, and warn for
    any addText/addShape/addImage call whose y_top + h pokes past the slide
    bottom or into the footer reserve. Returns one warning string per issue;
    empty list means clean.

    Hard overflow (y+h > slide_height): rendered slide will visibly clip.
    Soft overflow (slide_height - footer_reserve < y+h ≤ slide_height): the
    element collides with the footer band — usually a layout bug too.
    """
    js_dir = eval_dir / "output_js"
    if not js_dir.is_dir():
        return [f"y-lint: no output_js/ at {eval_dir}"]

    soft_threshold = slide_height - footer_reserve
    warns: list[str] = []
    js_files = sorted(js_dir.glob("*.js"))
    if not js_files:
        return [f"y-lint: no .js files under {js_dir}"]

    for js_path in js_files:
        try:
            slide_n = int(js_path.stem)
        except ValueError:
            continue
        slide_js = js_path.read_text()
        calls, eval_err = _run_lint_probe(slide_js, slide_n=slide_n, of_total=10)
        if eval_err:
            warns.append(f"slide {slide_n:>2}  probe error: {eval_err}")
        for c in calls:
            y = _to_float(c.get("y"))
            h = _to_float(c.get("h"))
            if y is None or h is None:
                continue
            bottom = y + h
            method = c.get("method", "?")
            if bottom > slide_height + 1e-6:
                warns.append(
                    f"slide {slide_n:>2}  HARD overflow  {method}  "
                    f"y={y:.2f} h={h:.2f} → bottom={bottom:.2f} > "
                    f"slide_height={slide_height}"
                )
            elif bottom > soft_threshold + 1e-6:
                warns.append(
                    f"slide {slide_n:>2}  SOFT overflow  {method}  "
                    f"y={y:.2f} h={h:.2f} → bottom={bottom:.2f} > "
                    f"footer_threshold={soft_threshold:.2f}"
                )
    return warns


# ---------------------------------------------------------------------------
# Top-level entrypoint
# ---------------------------------------------------------------------------

def postprocess_deck(deck: dict[str, Any], expected_id: str) -> tuple[dict[str, Any], list[str]]:
    """Run all fixes. Returns (fixed_deck, warnings).

    Idempotent: running on an already-clean deck returns the same dict and
    an empty warnings list.
    """
    deck, warns_id = fix_deck_id(deck, expected_id)
    palette, warns_palette = fix_palette(deck.get("palette", {}))
    deck["palette"] = palette
    return deck, warns_id + warns_palette


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _process_one(path: Path, write: bool) -> int:
    """Returns the number of warnings (= changes) for this file."""
    deck = json.loads(path.read_text())
    expected_id = path.parent.name  # eval/<variant>/<deck_id>/deck.json
    fixed, warns = postprocess_deck(deck, expected_id)
    print(f"\n{path}")
    if not warns:
        print("  clean (no changes)")
        return 0
    for w in warns:
        print(f"  - {w}")
    if write:
        path.write_text(json.dumps(fixed, indent=2, ensure_ascii=False))
        print(f"  wrote {path}")
    else:
        print("  (dry-run; pass --write to apply)")
    return len(warns)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("path", type=Path,
                   help="A deck.json file, or a directory to walk for deck.json files.")
    p.add_argument("--write", action="store_true",
                   help="Write fixes in place. Default is dry-run.")
    p.add_argument("--no-lint", action="store_true",
                   help="Skip the output_js/*.js y-bounds lint. Default is to run "
                        "it for any directory that contains an output_js/ subdir.")
    args = p.parse_args()

    if args.path.is_file():
        targets = [args.path]
    elif args.path.is_dir():
        targets = sorted(args.path.rglob("deck.json"))
        if not targets:
            raise SystemExit(f"no deck.json files found under {args.path}")
    else:
        raise SystemExit(f"path not found: {args.path}")

    total = 0
    for t in targets:
        total += _process_one(t, args.write)

    # Y-bounds lint runs on each eval dir (the parent of a deck.json) that has
    # an output_js/ subdir. Read-only — never auto-rewrites slide JS.
    if not args.no_lint:
        eval_dirs = sorted({t.parent for t in targets
                            if (t.parent / "output_js").is_dir()})
        for d in eval_dirs:
            print(f"\ny-lint: {d}")
            warns = lint_slide_y_bounds(d)
            if not warns:
                print("  clean (no overflows)")
            else:
                for w in warns:
                    print(f"  - {w}")
                total += len(warns)

    print(f"\n{len(targets)} file(s), {total} total fix(es).")


if __name__ == "__main__":
    main()
