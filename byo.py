"""
Bring-your-own-template — extract a brand skin from user decks, and re-skin a
generated deck into it. Phase 1: deterministic skin (colours, fonts, logo
tokens), not layout. No model calls.

    extract_template([pptx, ...]) -> report {palette, summary, ...}
    reskin_deck(out_dir, bundle)  -> (n_colours, n_fonts)   # rewrites in place

Brand tokens come from the deck theme (ppt/theme/theme1.xml) or, for
default-Office / tool-made decks, from actual slide colour+font usage. Multiple
decks are AGGREGATED — cross-deck usage votes on the palette, so the result is
more robust than any single deck.
"""
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
# Stock Office theme signatures — a theme matching these carries no real brand,
# so we fall back to actual slide usage. 4472C4 = Office 2013+, 4F81BD =
# 2007-2010 / python-pptx default, 5B9BD5 = another stock accent1.
OFFICE_DEFAULT_ACCENTS = {"4472C4", "4F81BD", "5B9BD5"}
OFFICE_DEFAULT_FONTS = {None, "", "Calibri", "Calibri Light", "Cambria",
                        "Aptos", "Aptos Display", "Arial", "Times New Roman",
                        "+mn-lt", "+mj-lt"}
_IMG_EXT = (".png", ".jpg", ".jpeg", ".svg", ".emf", ".gif", ".bmp", ".tiff")

# Our model's Carbon palette -> semantic role (role keys index the brand bundle).
COLOR_ROLE = {
    "0F62FE": "accent", "0043CE": "accent",
    "3939C2": "secondary_accent", "8A3FFC": "secondary_accent",
    "1192E8": "secondary_accent", "A56EFF": "secondary_accent",
    "161616": "dark_text", "000000": "dark_text", "393939": "dark_text",
    "525252": "muted", "6F6F6F": "muted",
}
CARBON_HIGHLIGHT = "E5F6FF"       # Cyan 10 callout panel -> tint of brand accent
CARBON_PANEL_GRAY = "F4F4F4"      # Gray 10 panel (remapped only for dark brands)
# Swap sans/serif Plex faces to the brand font; leave "IBM Plex Mono" (code
# blocks) as monospace.
FONT_FACE_RE = re.compile(r'"IBM Plex (?:Sans|Serif)[^"]*"')


# ---------- colour helpers --------------------------------------------------
def _hex(s):
    return s.lstrip("#").upper()

def _rgb(hx):
    hx = _hex(hx)
    return int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)

def _lum(hx):
    r, g, b = _rgb(hx)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b

def _is_gray(hx, tol=18):
    r, g, b = _rgb(hx)
    return max(r, g, b) - min(r, g, b) <= tol

def _is_saturated_brand(hx):
    return not _is_gray(hx) and 25 < _lum(hx) < 235

def _tint(hx, toward_white=0.88):
    r, g, b = _rgb(hx)
    mix = lambda c: round(c + (255 - c) * toward_white)
    return f"{mix(r):02X}{mix(g):02X}{mix(b):02X}"


# ---------- read a .pptx ----------------------------------------------------
def _slot_hex(el):
    srgb = el.find(f"{A}srgbClr")
    if srgb is not None:
        return srgb.get("val", "").upper()
    sysclr = el.find(f"{A}sysClr")
    if sysclr is not None:
        return (sysclr.get("lastClr") or "").upper() or None
    return None

def _parse_theme(z):
    name = next((n for n in z.namelist()
                 if re.match(r"ppt/theme/theme1\.xml$", n)), None)
    if not name:
        return {}, None, None
    root = ET.fromstring(z.read(name))
    clr = {}
    scheme = root.find(f".//{A}clrScheme")
    if scheme is not None:
        for slot in scheme:
            hx = _slot_hex(slot)
            if hx:
                clr[slot.tag.split("}")[-1]] = hx
    major = minor = None
    fs = root.find(f".//{A}fontScheme")
    if fs is not None:
        maj = fs.find(f"{A}majorFont/{A}latin")
        mnr = fs.find(f"{A}minorFont/{A}latin")
        major = maj.get("typeface") if maj is not None else None
        minor = mnr.get("typeface") if mnr is not None else None
    return clr, major, minor

def _scan_slides(z):
    colors, fonts, texts = Counter(), Counter(), []
    for n in z.namelist():
        if re.match(r"ppt/slides/slide\d+\.xml$", n):
            xml = z.read(n).decode("utf-8", "ignore")
            for c in re.findall(r'srgbClr val="([0-9A-Fa-f]{6})"', xml):
                colors[c.upper()] += 1
            for f in re.findall(r'typeface="([^"]+)"', xml):
                if f and not f.startswith("+"):
                    fonts[f] += 1
            for t in re.findall(r'<a:t>([^<]*)</a:t>', xml):
                if t.strip():
                    texts.append(t.strip())
    return colors, fonts, texts

def _list_media(z):
    return [n for n in z.namelist()
            if n.startswith("ppt/media/") and n.lower().endswith(_IMG_EXT)]

def _title_case_fraction(texts):
    def is_title(s):
        words = [w for w in re.findall(r"[A-Za-z]+", s) if len(w) > 2]
        if len(words) < 2:
            return None
        return sum(1 for w in words if w[0].isupper()) / len(words) >= 0.8
    vals = [v for v in (is_title(t) for t in texts) if v is not None]
    return (sum(vals) / len(vals)) if vals else None


# ---------- assemble the brand bundle ---------------------------------------
def _build_bundle(clr, major, minor, colors, fonts):
    theme_default = ((clr.get("accent1") is None
                      or clr.get("accent1") in OFFICE_DEFAULT_ACCENTS)
                     and major in OFFICE_DEFAULT_FONTS)
    brand = [c for c, _ in colors.most_common() if _is_saturated_brand(c)]
    usage_accent = brand[0] if brand else None
    usage_accent2 = brand[1] if len(brand) > 1 else None
    usage_dark = next((c for c, _ in colors.most_common() if _lum(c) < 60), None)

    if theme_default:
        primary = accent = usage_accent or usage_dark or "161616"
        secondary = usage_accent2 or primary
        dark_text = usage_dark or clr.get("dk1") or "161616"
        top = fonts.most_common(1)[0][0] if fonts else "Arial"
        headline = body = top
        source = "slide usage"
    else:
        primary = accent = clr.get("accent1")
        secondary = clr.get("accent2") or clr.get("accent3") or primary
        dark_text = clr.get("dk1") or usage_dark or "161616"
        headline = major or "Arial"
        body = minor or headline
        source = "theme"

    # background: for stock-theme decks the real page colour is the dominant
    # light fill in usage (theme lt1 is just white); branded themes trust lt1.
    light_usage = [c for c, _ in colors.most_common() if _lum(c) > 205]
    usage_bg = light_usage[0] if light_usage else None
    bg = (usage_bg or "FFFFFF") if theme_default else (clr.get("lt1") or "FFFFFF")
    used_grays = [c for c, _ in colors.most_common()
                  if _is_gray(c) and 60 < _lum(c) < 190]
    if used_grays:
        muted = used_grays[0]
    elif not theme_default:                 # stock-theme dk2 is blue, not a gray
        muted = clr.get("dk2") or "6F6F6F"
    else:
        muted = "6F6F6F"
    is_dark = _lum(bg) < 128
    return {
        "is_dark": is_dark,
        "tokens": {
            "bg": bg, "primary": primary, "accent": accent,
            "secondary_accent": secondary, "light": bg if is_dark else "FFFFFF",
            "muted": muted, "dark_text": dark_text,
        },
        "typography": {"headline_font": headline, "body_font": body},
    }, source


def extract_template(pptx_paths):
    """Aggregate brand signals across one or more .pptx/.potx -> a report.

    Returns {palette, palette_derived_from, summary, n_decks, voice,
    logo_candidates}. `palette` is the deck.json palette block; `summary` is a
    one-line human readout for the UI.
    """
    if not pptx_paths:
        raise ValueError("no template files")
    colors, fonts, texts, media = Counter(), Counter(), [], []
    theme_clr, theme_major, theme_minor, theme_set = {}, None, None, False
    for p in pptx_paths:
        with zipfile.ZipFile(p) as z:
            clr, major, minor = _parse_theme(z)
            c, f, tx = _scan_slides(z)
            colors.update(c)
            fonts.update(f)
            texts.extend(tx)
            media.extend(f"{Path(p).name}:{Path(m).name}" for m in _list_media(z))
        non_default = ((clr.get("accent1") is not None
                        and clr.get("accent1") not in OFFICE_DEFAULT_ACCENTS)
                       and major not in OFFICE_DEFAULT_FONTS)
        if non_default and not theme_set:            # first genuinely-branded theme wins
            theme_clr, theme_major, theme_minor, theme_set = clr, major, minor, True
        elif not theme_clr:                          # else keep a default theme for lt1/dk1
            theme_clr, theme_major, theme_minor = clr, major, minor

    bundle, source = _build_bundle(theme_clr, theme_major, theme_minor, colors, fonts)
    tcf = _title_case_fraction(texts)
    t = bundle["tokens"]
    casing = "Title Case" if (tcf or 0) > 0.6 else "sentence case"
    summary = (f"accent #{t['accent']} · {bundle['typography']['body_font']} · "
               f"{len(media)} logo candidate(s) · {casing}")
    return {
        "palette": bundle,
        "palette_derived_from": source,
        "summary": summary,
        "n_decks": len(pptx_paths),
        "voice": {"title_case_fraction": round(tcf, 2) if tcf is not None else None,
                  "casing": casing},
        "logo_candidates": media,
    }


# ---------- re-skin a generated deck ----------------------------------------
def _build_color_map(bundle):
    t = {k: _hex(v) for k, v in bundle["tokens"].items()}
    cmap = {c: t[role] for c, role in COLOR_ROLE.items() if t.get(role)}
    cmap[CARBON_HIGHLIGHT] = _tint(t["accent"])
    if t["bg"] != "FFFFFF":                 # cream / dark brand: recolour the page
        cmap["FFFFFF"] = t["bg"]
        if bundle.get("is_dark"):
            cmap[CARBON_PANEL_GRAY] = t.get("light", t["bg"])
    return {k: v for k, v in cmap.items() if k != v}

def _apply_skin(text, cmap, body_font):
    n_col = n_fnt = 0
    if cmap:
        pat = re.compile("|".join(sorted(cmap, key=len, reverse=True)), re.I)
        def _sub(m):
            nonlocal n_col
            n_col += 1
            return cmap[m.group(0).upper()]
        text = pat.sub(_sub, text)
    def _font(_m):
        nonlocal n_fnt
        n_fnt += 1
        return f'"{body_font}"'
    return FONT_FACE_RE.sub(_font, text), n_col, n_fnt

def reskin_deck(out_dir, bundle):
    """Re-skin deck.json + output_js/*.js in out_dir IN PLACE to the brand
    bundle: remap our model's Carbon palette to the brand tokens by role, swap
    the font, replace the palette block. Layout untouched. Caller re-renders.
    Returns (n_colours, n_fonts) substituted."""
    out = Path(out_dir)
    body_font = bundle["typography"]["body_font"]
    cmap = _build_color_map(bundle)
    tot_c = tot_f = 0

    dj = out / "deck.json"
    if dj.exists():
        # deck.json embeds output_js as ESCAPED strings — skin the PARSED tree
        # (each decoded string) so json.dumps re-escapes correctly.
        def _walk(o):
            nonlocal tot_c, tot_f
            if isinstance(o, str):
                s, c, f = _apply_skin(o, cmap, body_font)
                tot_c += c
                tot_f += f
                return s
            if isinstance(o, list):
                return [_walk(x) for x in o]
            if isinstance(o, dict):
                return {k: _walk(v) for k, v in o.items()}
            return o
        deck = _walk(json.loads(dj.read_text()))
        if isinstance(deck, dict):
            deck["palette"] = bundle
        dj.write_text(json.dumps(deck, ensure_ascii=False, indent=2))

    js_dir = out / "output_js"
    if js_dir.is_dir():
        for js in sorted(js_dir.glob("*.js")):
            s, c, f = _apply_skin(js.read_text(), cmap, body_font)
            js.write_text(s)
            tot_c += c
            tot_f += f
    return tot_c, tot_f
