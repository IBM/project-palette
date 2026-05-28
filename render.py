"""Render a deck output directory to a .pptx, plus per-slide PNG previews.

Synced from palette-training/src/palette_training/render.py @ 2026-05-21.
The stitch contract here MUST match the one the training data was rendered
with — keep this in sync with palette-training. The only deck_forge addition
is render_previews() (soffice + pdftoppm), used by the UI and Stage-3 critic.

Reads:
  <out_dir>/output_js/<n>.js     # one per slide
  <out_dir>/deck.json            # (or --deck override)

Writes:
  <out_dir>/deck.pptx
  <out_dir>/_runner.js           # the stitched runner, kept for debugging
  <out_dir>/slide-NN.png         # per-slide previews (render_previews)

Per-slide failures (broken JS, bad shape opts, missing methods) are caught and
printed at the end. A bad slide produces a blank slide rather than aborting
the whole deck.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import contextvars
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

log = logging.getLogger("render")


_DASH_NORMALIZE = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-",
})


_DEFAULT_PALETTE = {
    "is_dark": False,
    "tokens": {
        "bg": "F8F9FB", "primary": "1F2937", "accent": "8B5CF6",
        "secondary_accent": "14B8A6", "light": "FFFFFF",
        "muted": "6B7280", "dark_text": "111827",
    },
    "typography": {"headline_font": "Georgia", "body_font": "Calibri"},
}


_RUNNER_HEADER = """const PptxGenJS = require("pptxgenjs");
const pres = new PptxGenJS();
pres.defineLayout({ name: "WS", width: 13.333, height: 7.5 });
pres.layout = "WS";
const failures = [];
"""


_MASTER_DEFS = """
pres.defineSlideMaster({
  title: "COVER",
  background: { color: "FFFFFF" },
  objects: [],
});

pres.defineSlideMaster({
  title: "BODY",
  background: { color: "FFFFFF" },
  objects: [],
});
"""


def _runner_footer(out_path: Path) -> str:
    """The runner runs from the repo root (so icons/carbon/* and the
    node_modules pptxgenjs require both resolve), but the .pptx must land
    in the eval dir — pass writeFile an absolute path."""
    out_js = json.dumps(str(out_path))
    return f"""
pres.writeFile({{ fileName: {out_js} }}).then(() => {{
  if (failures.length > 0) {{
    console.error("FAILURES:", JSON.stringify(failures));
  }}
  process.exit(0);
}}).catch(e => {{
  console.error("WRITE_FAILED:", e.message);
  process.exit(1);
}});
"""


# Fonts we know are reliably present (or installable). The LoRA picks IBM
# Plex for IBM-family palettes — we install Plex on Mac + the Dockerfile. For
# anything else the LoRA emits (Inter, Arno Pro, "Sans"…), we substitute
# Calibri at render time: it's on every Mac/Windows with Office, and the
# Dockerfile installs `fonts-crosextra-carlito` (Calibri-metric-compatible)
# so LibreOffice on Linux substitutes Calibri → Carlito automatically.
_FONT_SAFELIST_PREFIXES = ("IBM Plex",)
_FONT_FALLBACK = "Calibri"


def _safe_font(name: str | None) -> str:
    """Pass IBM Plex through; replace anything else with Calibri."""
    if not name:
        return _FONT_FALLBACK
    return name if any(name.startswith(p) for p in _FONT_SAFELIST_PREFIXES) \
        else _FONT_FALLBACK


def _palette_runner_block(palette: dict[str, Any], deck_title: str) -> str:
    """Emit JS that pre-binds palette + helpers for every slide.

    The bound names match the CODER_SYSTEM_PROMPT contract: palette,
    makeShadow, softShadow, darkFooter, lightFooter, connector. slide,
    pres, slide_n, of_total are bound per-slide in the IIFE below.

    Typography is filtered through `_safe_font` before binding so the
    LoRA's font choice can't escape what we actually have installed
    (see _FONT_SAFELIST_PREFIXES).
    """
    p = palette or _DEFAULT_PALETTE
    tokens = p.get("tokens") or _DEFAULT_PALETTE["tokens"]
    typo = p.get("typography") or _DEFAULT_PALETTE["typography"]

    _color_tokens = {
        "bg": tokens.get("bg"),
        "primary": tokens.get("primary"),
        "accent": tokens.get("accent"),
        "secondary_accent": tokens.get("secondary_accent"),
        "light": tokens.get("light"),
        "muted": tokens.get("muted"),
        "dark_text": tokens.get("dark_text"),
    }
    _palette_obj = {
        "is_dark": bool(p.get("is_dark", False)),
        **_color_tokens,
        # The deck.json brief nests color tokens under `palette.tokens.*`, so
        # generated JS sometimes writes `palette.tokens.bg`. Bind a `tokens`
        # alias to the same values so BOTH `palette.bg` and `palette.tokens.bg`
        # resolve — eliminates a recurring "Cannot read properties of undefined"
        # render crash without depending on the model getting the flat form right.
        "tokens": _color_tokens,
        "typography": {
            "headline_font": _safe_font(typo.get("headline_font")),
            "body_font": _safe_font(typo.get("body_font")),
        },
    }
    palette_js = json.dumps(_palette_obj)
    deck_title_js = json.dumps((deck_title or "").translate(_DASH_NORMALIZE))

    return f"""
// ---------- Palette and helpers (deck-level, available to every slide) ----------
const palette = {palette_js};
const DECK_TITLE = {deck_title_js};

function makeShadow() {{
  return {{ type: "outer", blur: 8, offset: 2, color: "000000", opacity: 0.30, angle: 90 }};
}}
function softShadow() {{
  return {{ type: "outer", blur: 4, offset: 1, color: "000000", opacity: 0.15, angle: 90 }};
}}

function darkFooter(slide, num, total) {{
  slide.addText(String(num) + " / " + String(total), {{
    x: 12.4, y: 7.15, w: 0.7, h: 0.25,
    fontFace: palette.typography.body_font, fontSize: 9,
    color: palette.muted, align: "right", margin: 0,
  }});
  slide.addText(DECK_TITLE, {{
    x: 0.6, y: 7.15, w: 8, h: 0.25,
    fontFace: palette.typography.body_font, fontSize: 9,
    color: palette.muted, charSpacing: 2, margin: 0,
  }});
}}

const lightFooter = darkFooter;

function connector(slide, x1, y1, x2, y2, color, opts) {{
  const c = color || palette.muted;
  const o = opts || {{}};
  const arrow = o.arrow || "to";
  const width = o.width || 1.25;
  let beginArrowType = "none";
  let endArrowType = "triangle";
  if (arrow === "none") {{ beginArrowType = "none"; endArrowType = "none"; }}
  else if (arrow === "from") {{ beginArrowType = "triangle"; endArrowType = "none"; }}
  else if (arrow === "both") {{ beginArrowType = "triangle"; endArrowType = "triangle"; }}
  slide.addShape(pres.ShapeType.line, {{
    x: Math.min(x1, x2), y: Math.min(y1, y2),
    w: Math.max(Math.abs(x2 - x1), 0.001),
    h: Math.max(Math.abs(y2 - y1), 0.001),
    line: {{ color: c, width, beginArrowType, endArrowType }},
    flipV: y2 < y1, flipH: x2 < x1,
  }});
}}
"""


def stitch_runner(slide_js: list[str | None], deck_title: str,
                  palette: dict[str, Any], out_path: Path) -> str:
    parts = [_RUNNER_HEADER, _MASTER_DEFS,
             _palette_runner_block(palette, deck_title)]
    n_total = len(slide_js)
    for i, js in enumerate(slide_js, start=1):
        master = "COVER" if i == 1 else "BODY"
        parts.append(f"\n// slide {i}")
        if js is None:
            parts.append(f'pres.addSlide({{ masterName: "{master}" }});')
            parts.append(f"failures.push({{ slide: {i}, kind: 'no_code' }});")
            continue
        parts.append("(function() {")
        parts.append(f'  const slide = pres.addSlide({{ masterName: "{master}" }});')
        parts.append(f"  const slide_n = {i};")
        parts.append(f"  const of_total = {n_total};")
        # Wrap pptxgenjs methods so one bad shape doesn't kill the slide.
        parts.append("  ['addText','addShape','addTable','addChart','addNotes'].forEach(m => {")
        parts.append("    const orig = slide[m];")
        parts.append("    if (typeof orig === 'function') {")
        parts.append("      slide[m] = (...args) => {")
        parts.append("        try { return orig.apply(slide, args); }")
        parts.append(f"        catch (e) {{ failures.push({{ slide: {i}, kind: 'shape_error', method: m, message: e.message }}); }}")
        parts.append("      };")
        parts.append("    }")
        parts.append("  });")
        # addImage gets a dedicated wrapper. An image whose file is missing
        # would otherwise abort the WHOLE deck at writeFile() time — the file
        # read is deferred to write, so the per-method try/catch above never
        # sees it. Skip missing images (icon OR asset), record a failure, and
        # keep rendering. This is the universal safety net for hallucinated
        # icon names and unresolvable asset paths.
        parts.append("  (function() {")
        parts.append("    const _addImage = slide.addImage.bind(slide);")
        parts.append("    slide.addImage = (opts) => {")
        parts.append("      try {")
        parts.append("        if (opts && opts.path && !require('fs').existsSync(opts.path)) {")
        parts.append(f"          failures.push({{ slide: {i}, kind: 'missing_image', path: String(opts.path) }});")
        parts.append("          return;")
        parts.append("        }")
        parts.append("        return _addImage(opts);")
        parts.append("      } catch (e) {")
        parts.append(f"        failures.push({{ slide: {i}, kind: 'shape_error', method: 'addImage', message: e.message }});")
        parts.append("      }")
        parts.append("    };")
        parts.append("  })();")
        # eval(string) so a syntax error becomes a catchable runtime error
        # rather than killing the whole runner.
        parts.append("  try {")
        parts.append(f"    eval({json.dumps(js)});")
        parts.append("  } catch (e) {")
        # Pull the line number within the eval'd slide JS out of the stack
        # (the <anonymous>:LINE:COL frame) so the repair editor gets located
        # feedback, not just a bare message.
        parts.append("    var _ln = null;")
        parts.append("    var _m = String(e.stack || '').match(/<anonymous>:(\\d+):/);")
        parts.append("    if (_m) _ln = parseInt(_m[1], 10);")
        parts.append(
            f"    failures.push({{ slide: {i}, kind: 'exec_error', "
            "message: e.message, line: _ln });"
        )
        parts.append("  }")
        parts.append("})();")
    parts.append(_runner_footer(out_path))
    return "\n".join(parts)


def _collect_slide_js(out_dir: Path) -> list[str | None]:
    """Read output_js/<n>.js files in order. Missing files become None
    (which produces a blank slide + a 'no_code' failure record)."""
    js_dir = out_dir / "output_js"
    if not js_dir.exists():
        raise SystemExit(f"no output_js/ under {out_dir}")

    files = sorted(js_dir.glob("*.js"))
    if not files:
        raise SystemExit(f"no .js files under {js_dir}")

    # Slide numbers are encoded as filenames (e.g., 01.js, 12.js). Honor that
    # ordering and fill gaps with None so slide_n stays right.
    by_num: dict[int, str] = {}
    for f in files:
        try:
            n = int(f.stem)
        except ValueError:
            continue
        by_num[n] = f.read_text()

    if not by_num:
        raise SystemExit(f"no numerically-named .js files under {js_dir}")

    n_max = max(by_num)
    return [by_num.get(i) for i in range(1, n_max + 1)]


def _node_module_dir(start: Path) -> Path:
    """Walk up from `start` until we find a node_modules/ dir, or return
    `start` so node's resolution still gets a chance via NODE_PATH."""
    p = start.resolve()
    for parent in [p, *p.parents]:
        if (parent / "node_modules").exists():
            return parent
    return p


def _isolate_write_failure(slide_js: list[str | None], deck_title: str,
                           palette: dict[str, Any], node: str, cwd: Path,
                           ) -> set[int]:
    """The whole-deck pptx.writeFile() can abort on a single slide's bad JS
    (a non-string `valign`, an unknown shape opt, etc.) and take the entire
    write down with it. The per-slide try/catch in stitch_runner is scoped
    to addText/addShape/etc. at SETUP time; write-time errors fall through
    to a generic WRITE_FAILED and produce no per-slide breadcrumb.

    This salvages that case: render each slide in isolation (one slide per
    Node process, parallel), and return the slide numbers (1-based) that
    failed to write on their own. They are the offenders — the caller
    blanks them in the main runner, records them as render_failures, and
    the existing repair-render-errors loop calls the editor on them.
    """
    bad: set[int] = set()

    def _try_one(idx_1based: int) -> int | None:
        js = slide_js[idx_1based - 1]
        if js is None:
            return None   # already a no_code slide; can't be a write offender
        # NOTE: temp dir MUST live inside cwd (the node_modules dir) — Node's
        # require() resolution walks up from the runner file's location, not
        # the process cwd. A /tmp dir has no pptxgenjs in its parent chain.
        with tempfile.TemporaryDirectory(dir=str(cwd)) as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            tmp_pptx = tmpdir / "isolated.pptx"
            runner_js = stitch_runner([js], deck_title=deck_title,
                                      palette=palette, out_path=tmp_pptx)
            runner_path = tmpdir / "_runner.js"
            runner_path.write_text(runner_js)
            try:
                subprocess.run(
                    [node, str(runner_path)], cwd=str(cwd),
                    capture_output=True, text=True, timeout=60,
                )
            except subprocess.TimeoutExpired:
                return idx_1based
            return None if tmp_pptx.exists() else idx_1based

    with ThreadPoolExecutor(max_workers=8) as ex:
        # copy_context() per submit so worker logs route to the active
        # session log (the contextvar lives in app.py).
        futs = {ex.submit(contextvars.copy_context().run, _try_one, i): i
                for i in range(1, len(slide_js) + 1)}
        for fut in as_completed(futs):
            result = fut.result()
            if result is not None:
                bad.add(result)
    return bad


def render(eval_dir: Path, deck_path: Path | None, out_name: str = "deck.pptx") -> Path:
    eval_dir = eval_dir.resolve()
    deck_path = deck_path or (eval_dir / "deck.json")
    if not deck_path.exists():
        raise SystemExit(
            f"deck.json not found at {deck_path}. Pass --deck for coder-only outputs."
        )

    deck = json.loads(deck_path.read_text())
    palette = deck.get("palette", {})
    deck_title = deck.get("deck_title", "")

    pptx = (eval_dir / out_name).resolve()
    slide_js = _collect_slide_js(eval_dir)
    runner_js = stitch_runner(slide_js, deck_title=deck_title, palette=palette,
                              out_path=pptx)
    runner_path = eval_dir / "_runner.js"
    runner_path.write_text(runner_js)

    node = shutil.which("node")
    if node is None:
        raise SystemExit("`node` not found on PATH. Install with `brew install node`.")

    # Run from the repo root: that dir has both node_modules/ (so
    # require('pptxgenjs') resolves) AND icons/carbon/ (so the slide JS's
    # relative addImage paths resolve). The .pptx is written to an absolute
    # path inside eval_dir, so cwd doesn't affect output location.
    cwd = _node_module_dir(eval_dir)
    if not (cwd / "node_modules" / "pptxgenjs").exists():
        raise SystemExit(
            f"pptxgenjs not installed under {cwd}/node_modules/. Run `npm install` "
            f"in {cwd} first."
        )

    proc = subprocess.run(
        [node, str(runner_path)],
        cwd=str(cwd),
        capture_output=True, text=True, timeout=180,
    )

    failures: list[dict[str, Any]] = []
    for line in (proc.stderr or "").splitlines():
        if line.startswith("FAILURES:"):
            try:
                failures = json.loads(line[len("FAILURES:"):].strip())
            except json.JSONDecodeError:
                pass

    # WRITE_FAILED fallback: a single bad slide can abort pptx.writeFile()
    # for the whole deck. Isolate the offender(s) in parallel, then re-render
    # with them blanked so downstream stages (previews, repair, geometry) all
    # have a deck to work on. The blanked slides go into render_failures.json
    # as 'write_error' — the existing repair loop will call the editor on
    # them and a later re-render will inline the fixed JS.
    if not pptx.exists() and "WRITE_FAILED:" in (proc.stderr or ""):
        wf_msg = ""
        for line in (proc.stderr or "").splitlines():
            if line.startswith("WRITE_FAILED:"):
                wf_msg = line.partition(":")[2].strip()
                break
        log.warning("render: whole-deck write failed (%s) — isolating "
                    "bad slide(s) in parallel", wf_msg[:120])
        bad_slides = _isolate_write_failure(
            slide_js, deck_title, palette, node, cwd)
        if bad_slides:
            log.warning("render: bad slide(s) %s — re-rendering with them "
                        "blanked, repair loop will fix", sorted(bad_slides))
            salvaged_js = [None if (i + 1) in bad_slides else js
                           for i, js in enumerate(slide_js)]
            runner_js2 = stitch_runner(salvaged_js, deck_title=deck_title,
                                       palette=palette, out_path=pptx)
            runner_path.write_text(runner_js2)
            proc2 = subprocess.run(
                [node, str(runner_path)], cwd=str(cwd),
                capture_output=True, text=True, timeout=180,
            )
            failures = []
            for line in (proc2.stderr or "").splitlines():
                if line.startswith("FAILURES:"):
                    try:
                        failures = json.loads(line[len("FAILURES:"):].strip())
                    except json.JSONDecodeError:
                        pass
            # upgrade the no_code records for our blanked slides to
            # write_error, carrying the original error message for the editor
            for f in failures:
                if f.get("slide") in bad_slides and f.get("kind") == "no_code":
                    f["kind"] = "write_error"
                    f["message"] = wf_msg or "pptx.writeFile() failed"

    if not pptx.exists():
        raise SystemExit(
            f"node failed to produce {pptx} (exit={proc.returncode}).\n"
            f"stderr (last 1000 chars):\n{(proc.stderr or '')[-1000:]}"
        )

    # Persist the per-slide failure list so the Stage-3 refine loop can feed
    # render errors (not just the visual symptom) to the editor.
    (eval_dir / "render_failures.json").write_text(json.dumps(failures))

    print(f"\nrendered {len(slide_js)} slides → {pptx}")
    if failures:
        print(f"\n{len(failures)} failure(s):")
        for f in failures:
            slide = f.get("slide", "?")
            kind = f.get("kind", "?")
            method = f.get("method", "")
            msg = f.get("message", "") or f.get("path", "")
            line = f"  slide {slide:>3}  {kind}"
            if method:
                line += f"  ({method})"
            if msg:
                line += f"  — {msg}"
            print(line)
    else:
        print("no failures")

    return pptx


def render_previews(out_dir: Path, pptx: Path | None = None,
                    dpi: int = 130) -> list[Path]:
    """Rasterize a rendered .pptx to per-slide PNGs: slide-NN.png in out_dir.

    Two external tools: `soffice` (.pptx -> .pdf) and `pdftoppm` (.pdf -> PNGs).
    pdftoppm zero-pads the page number to the deck's digit width, so a plain
    sorted() of the result is in slide order. Returns [] (with a warning) if a
    tool is missing or conversion fails — previews are best-effort, never fatal.
    """
    out_dir = Path(out_dir).resolve()
    pptx = (pptx or (out_dir / "deck.pptx")).resolve()
    if not pptx.exists():
        log.warning("render_previews: no pptx at %s", pptx)
        return []

    for stale in out_dir.glob("slide-*.png"):
        stale.unlink()

    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if soffice is None:
        log.warning("render_previews: soffice/libreoffice not on PATH — skipping")
        return []
    # Each call gets its own LibreOffice profile dir — concurrent soffice
    # invocations (a parallel repair-chain run) otherwise deadlock on the
    # shared default-profile lock.
    profile = tempfile.mkdtemp(prefix="lo_profile_")
    try:
        proc = subprocess.run(
            [soffice, f"-env:UserInstallation=file://{profile}",
             "--headless", "--convert-to", "pdf",
             "--outdir", str(out_dir), str(pptx)],
            capture_output=True, text=True, timeout=180,
        )
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    pdf = out_dir / (pptx.stem + ".pdf")
    if not pdf.exists():
        log.warning("render_previews: pdf conversion failed (%s)",
                    (proc.stderr or proc.stdout or "")[-300:])
        return []

    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm is None:
        log.warning("render_previews: pdftoppm not on PATH — skipping")
        return []
    subprocess.run(
        [pdftoppm, "-png", "-r", str(dpi), str(pdf), str(out_dir / "slide")],
        capture_output=True, text=True, timeout=180,
    )
    pngs = sorted(out_dir.glob("slide-*.png"))
    log.info("render_previews: %d slide PNG(s) -> %s", len(pngs), out_dir)
    return pngs


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("eval_dir", type=Path,
                   help="Directory containing output_js/ and (usually) deck.json.")
    p.add_argument("--deck", type=Path, default=None,
                   help="Override deck.json path. Use this for coder-only "
                        "outputs where the input deck lives in data/v2/decks/.")
    p.add_argument("--out", type=str, default="deck.pptx",
                   help="Filename for the rendered pptx (default: deck.pptx).")
    args = p.parse_args()
    render(args.eval_dir, args.deck, args.out)


if __name__ == "__main__":
    main()
