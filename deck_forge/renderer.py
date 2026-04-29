from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger("renderer")


_DASH_NORMALIZE = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-",
})


def _master_defs(deck_title: str) -> str:
    # Master provides only an empty canvas. Background is set per-slide from
    # palette.bg. Footer (deck title + page number + divider line) is rendered
    # per-slide via darkFooter/lightFooter helpers — putting it here too would
    # double up. The deck_title arg is kept for signature compatibility.
    _ = deck_title
    return """
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


_RUNNER_HEADER = """const PptxGenJS = require("pptxgenjs");
const pres = new PptxGenJS();
pres.defineLayout({ name: "WS", width: 13.333, height: 7.5 });
pres.layout = "WS";
const failures = [];
"""


_DEFAULT_PALETTE_FOR_RUNNER = {
    "palette_name": "Default light / paper + navy + violet",
    "is_dark": False,
    "tokens": {
        "bg": "F8F9FB", "primary": "1F2937", "accent": "8B5CF6",
        "secondary_accent": "14B8A6", "light": "FFFFFF",
        "muted": "6B7280", "dark_text": "111827",
    },
    "typography": {"headline_font": "Georgia", "body_font": "Calibri"},
}


def _palette_runner_block(palette: dict, deck_title: str) -> str:
    """Emit JS that pre-binds palette + helper functions for every slide."""
    p = palette or _DEFAULT_PALETTE_FOR_RUNNER
    tokens = p.get("tokens") or _DEFAULT_PALETTE_FOR_RUNNER["tokens"]
    typo = p.get("typography") or _DEFAULT_PALETTE_FOR_RUNNER["typography"]
    is_dark = bool(p.get("is_dark", True))

    palette_js = json.dumps({
        "name": p.get("palette_name", "default"),
        "is_dark": is_dark,
        "bg": tokens.get("bg", "0B1426"),
        "primary": tokens.get("primary", "182447"),
        "accent": tokens.get("accent", "06B6D4"),
        "secondary_accent": tokens.get("secondary_accent", "F59E0B"),
        "light": tokens.get("light", "E5E9F2"),
        "muted": tokens.get("muted", "64748B"),
        "dark_text": tokens.get("dark_text", "0B1426"),
        "typography": {
            "headline_font": typo.get("headline_font", "Georgia"),
            "body_font": typo.get("body_font", "Calibri"),
        },
    })
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

// On a light-bg deck, lightFooter is identical visually — same muted-on-bg readable.
const lightFooter = darkFooter;

function connector(slide, x1, y1, x2, y2, color, opts) {{
  // Draw a line from (x1,y1) to (x2,y2). Defaults to an arrow pointing
  // FROM (x1,y1) TO (x2,y2) — i.e. endArrowType "triangle". Pass opts
  // {{ arrow: "none" | "from" | "to" | "both", width: <number> }} to override.
  // pptxgenjs LINE shapes need non-negative w/h; flip flags carry direction.
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

_RUNNER_FOOTER = """
pres.writeFile({ fileName: "deck.pptx" }).then(() => {
  if (failures.length > 0) {
    console.error("FAILURES:", JSON.stringify(failures));
  }
  process.exit(0);
}).catch(e => {
  console.error("WRITE_FAILED:", e.message);
  process.exit(1);
});
"""


def stitch_runner(slide_js: list[str | None], deck_title: str = "",
                   palette: dict | None = None) -> str:
    parts = [_RUNNER_HEADER, _master_defs(deck_title),
              _palette_runner_block(palette or {}, deck_title)]
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
        parts.append(f"  const of_total = {len(slide_js)};")
        # Wrap each pptxgenjs method so one failed call doesn't abort the slide.
        parts.append("  ['addText','addShape','addTable','addChart','addImage','addNotes'].forEach(m => {")
        parts.append("    const orig = slide[m];")
        parts.append("    if (typeof orig === 'function') {")
        parts.append("      slide[m] = (...args) => {")
        parts.append("        try { return orig.apply(slide, args); }")
        parts.append(f"        catch (e) {{ failures.push({{ slide: {i}, kind: 'shape_error', method: m, message: e.message }}); }}")
        parts.append("      };")
        parts.append("    }")
        parts.append("  });")
        # Eval the slide body as a string so that SYNTAX errors in model output
        # become catchable runtime errors instead of killing the whole runner.
        # JSON.stringify gives us a properly-escaped JS string literal.
        encoded = json.dumps(js)
        parts.append("  try {")
        parts.append(f"    eval({encoded});")
        parts.append("  } catch (e) {")
        parts.append(
            f"    failures.push({{ slide: {i}, kind: 'exec_error', "
            "message: e.message });"
        )
        parts.append("  }")
        parts.append("})();")
    parts.append(_RUNNER_FOOTER)
    return "\n".join(parts)


def build_pptx(slide_js: list[str | None], out_dir: Path,
               deck_title: str = "",
               palette: dict | None = None) -> tuple[Path, list[dict]]:
    """Run the stitched JS through Node + pptxgenjs.

    Returns (pptx_path, failures). `failures` is a list of {slide, kind, ...}
    parsed from the runner's `FAILURES:` line — kinds are 'exec_error' (top-level
    JS threw, slide may be partial), 'shape_error' (one shape failed but rest
    rendered), 'no_code' (model returned nothing).
    """
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    runner = out_dir / "_runner.js"
    runner.write_text(stitch_runner(slide_js, deck_title=deck_title,
                                      palette=palette))

    node = shutil.which("node")
    if not node:
        raise RuntimeError("node not found on PATH")

    proc = subprocess.run(
        [node, str(runner)],
        cwd=str(out_dir), capture_output=True, text=True, timeout=180,
    )
    failures: list[dict] = []
    if proc.stderr:
        for line in proc.stderr.splitlines():
            log.warning("node: %s", line)
            if line.startswith("FAILURES:"):
                try:
                    failures = json.loads(line[len("FAILURES:"):].strip())
                except Exception:
                    pass

    pptx = out_dir / "deck.pptx"
    if not pptx.exists():
        raise RuntimeError(
            f"node failed to produce deck.pptx (exit={proc.returncode})\n"
            f"stderr: {proc.stderr[:500]}"
        )
    return pptx, failures


def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(
            f"`{name}` not on PATH. Install LibreOffice (soffice) and poppler "
            f"(pdftoppm). macOS: `brew install --cask libreoffice && brew install poppler`."
        )
    return path


def render_previews(pptx_path: Path, out_dir: Path, dpi: int = 110) -> list[Path]:
    soffice = _require_tool("soffice")
    pdftoppm = _require_tool("pdftoppm")

    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("slide-*.png"):
        old.unlink()

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        subprocess.run(
            [soffice, "--headless", "--convert-to", "pdf",
             "--outdir", str(tmp), str(pptx_path)],
            check=True, capture_output=True, timeout=60,
        )
        pdf = tmp / (pptx_path.stem + ".pdf")
        if not pdf.exists():
            raise RuntimeError(f"soffice produced no pdf for {pptx_path}")
        subprocess.run(
            [pdftoppm, "-png", "-r", str(dpi),
             str(pdf), str(out_dir / "slide")],
            check=True, capture_output=True, timeout=120,
        )

    return sorted(out_dir.glob("slide-*.png"))
