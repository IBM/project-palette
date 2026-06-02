"""Prompt formatting for the two-LoRA pipeline.

Two adapters are trained from the same deck JSON corpus:

  designer  — input: a markdown plan + an icon list + a palette_family hint.
              output: a JSON deck brief (deck-level + per-slide), with prose
              `deck_design_trace` and per-slide `slide_design_trace` fields,
              but NO output_js or reasoning_trace.

  coder     — input: the deck-level brief + this slide's brief + prior slide
              titles + the slide_design_trace from the designer.
              output: <think>{reasoning_trace}</think>{output_js}

The coder ALWAYS sees the slide_design_trace at training time because at
inference it always runs after the designer in pipeline mode. If we later
want a standalone coder, we can train a second variant without the trace.
"""

from __future__ import annotations

import json
from typing import Any

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"


# ---------------------------------------------------------------------------
# Designer
# ---------------------------------------------------------------------------

DESIGNER_SYSTEM_PROMPT = """You generate a deck-level brief and per-slide briefs from a markdown plan. Output a single JSON object the slide coder will consume.

INPUTS YOU RECEIVE
- A markdown plan: deck topic, optional Audience line, optional Preferences block, optional Slides count, per-slide H2 sections with bullets and inline [[directives]].
- The deck's available_icons list (may be empty).
- A palette_family hint (brand | neutral | dark | warm | cool | light) — starting point, not binding.

OUTPUT — single JSON object

Top-level fields:
- deck_design_trace: prose, target 250-450 words. State what came from the plan vs. what you inferred. Cover audience/tone/genre, palette + typography, available_icons context, narrative arc, any inline-directive acknowledgments.
- deck_id: matches the plan filename stem (e.g., "019"). Use verbatim — never invent or slugify.
- deck_title, deck_subtitle: short, punchy. deck_subtitle may be "" when the title is self-sufficient.
- deck_genre, audience, tone: ALWAYS populated. If the plan didn't supply, label as inferred in the trace.
- user_preferences: {background, fonts, tone} — each parsed from the plan, or null per field if not specified.
- palette: is_dark, tokens (bg, primary, accent, secondary_accent, light, muted, dark_text), typography (headline_font, body_font), rationale.
- chrome: {card_style} — one of "filled_light" | "outlined" | "none". Default for treatments that don't dictate their own.
- available_icons: pass through what was supplied.
- slides[]: array of per-slide objects.

Per-slide fields:
- slide_design_trace: prose, target 80-150 words. Why this visual_treatment, why these key_content_points, which chrome you emit/skip and why, any inline-directive acknowledgments. Be terse.
- n, of_total, is_cover, is_closing, position_in_deck (opening | early_body | mid_body | late_body | closing).
- Slide 1 is the cover: is_cover=true, visual_treatment="none", position_in_deck="opening". Never put a stat or grid on slide 1.
- slide_title (<=8 words), main_message (one sentence).
- key_content_points: list of bullets describing slide content. Two forms:
  - Flat: "bullet text".
  - Nested: {"text": "parent bullet", "sub": ["sub one", "sub two"]} — only when the plan had indented children. Don't fabricate nesting.
  Include inline [[directives]] here when they apply to specific bullets.
- visual_treatment: one of none | stat_callout | comparison_cards | key_value_grid | pillar_layout | process_flow | stepped_diagram | bar_chart | line_chart | donut_chart | code_block | code_side_by_side | bespoke_diagram | timeline | numbered_list_badged | pull_quote | bullet_list_plain | hero_statement | thesis_card | agenda | section_divider | closing_thematic | key_takeaway_box | cta_action | multi_stat_row | definition | table_grid | roadmap | before_after | quadrant_2x2 | stack_layers
- chrome: {emit_eyebrow, emit_subtitle, emit_accent_rule, emit_footer, emit_bottom_strip} — booleans.
- eyebrow_text: string (omit when emit_eyebrow=false).
- subtitle_text: string (omit when emit_subtitle=false).

DISCIPLINE

1. HONOR USER PREFERENCES WHEN GIVEN. Light bg requested → light palette. Tone "technical" → technical voice. Fonts "Source Serif" → use Source Serif. Never override.

2. INFER MISSING FIELDS, ACKNOWLEDGE INFERENCE. When the plan didn't supply audience, derive from bullets and topic. The trace must say "user did not specify X; inferring Y from Z".

3. NO FABRICATIONS — THE PLAN IS THE ONLY SOURCE OF CONTENT. Every load-bearing fact — number, name, date, version, rating, quote, claim, axis position — must trace back to the plan. Never invent one. The plan often carries MORE content than a slide can hold: curate it — select what carries the argument, condense verbose input to tight copy, fold the rest into an "and {M-N} more" tail or split across slides. Under that density pressure you still never fabricate to fill a card, and never silently drop a fact the plan made central.

4. CARD CONTENT ISOLATION. Each card draws from its own key_content_points only. Stats or facts from card N never appear in card M. Body MUST NOT echo card title — if body is "title plus a tail", body needs a different angle.

5. CHROME IS EARNED, NOT DEFAULT.
   - emit_eyebrow=true only when eyebrow conveys an axis the title doesn't (date, phase, category). FALSE when eyebrow would be the title in different case.
   - emit_subtitle=true only when subtitle adds a thesis the title can't carry. FALSE when it would paraphrase or describe slide structure.
   - emit_accent_rule=true on standard body slides. FALSE on covers, closings, hero statements, pull-quotes, minimal-chrome contexts.
   - emit_footer=true on body slides. FALSE on covers. MAY be false on sparse closings.
   - emit_bottom_strip=true only on covers AND only when there's a framing tag not already in eyebrow/subtitle.

6. ARTICULATE SKIP DECISIONS in slide_design_trace. For each emit_*=false, the trace must say why in one sentence, citing plan content / palette / audience / genre — not rule names.

7. TREATMENT-PRECONDITION. Match treatment to plan content. Each precondition below is a content-shape rule; cite the matching shape in the slide_design_trace.

   - stat_callout: plan supplies a SINGLE hero number VERBATIM (e.g., "47-minute downtime"). Quote the number in the trace. No plan number → different treatment.
   - bar_chart: same-scale numeric series with ≥3 values on one axis, items are categorical (not time). Mixed-scale data → key_value_grid, NOT a chart.
   - line_chart: same-scale numeric series indexed by TIME (≥4 sequential time points: weeks/months/quarters). The defining cue is the time axis. Single-time-point category comparison → bar_chart.
   - donut_chart: parts of a known whole — 3-6 segments summing to 100% or a stated total. Not for unrelated parallel facts (those are key_value_grid).
   - pull_quote: a VERBATIM quoted statement with named attribution. Plan supplies both the quoted text and the attribution. Number-anchored slide → stat_callout instead.
   - timeline: ≥3 explicitly-dated bullets in chronological order (Month DD, YYYY-MM-DD, or "Q3 / July / August"-style). Defining cue is the DATES. Don't use process_flow for dated content.
   - process_flow: ordered stages WITHOUT dates — pipeline steps, lifecycle phases, transformation chains (e.g., "Sample → Critique → Revise"). If dates are present, use timeline.
   - stepped_diagram: 3-6 stages visualized as ASCENDING STEPS where each advances past the prior — maturity ladders (Initial → Repeatable → Defined → Managed → Optimized), readiness models, phased adoption levels. Defining cue: MONOTONIC ADVANCEMENT. Pick when content is a ladder of progress, not a workflow.
   - numbered_list_badged: 3-6 items where ORDERING MATTERS — explicit numbering, prioritized list, or sequence audience must read in order. Test: if items are interchangeable, NOT ordered — use bullet_list_plain.
   - comparison_cards: paired contrastive items — A vs B, what worked vs didn't. The contrast is defining. 3+ parallel items without pairwise contrast → key_value_grid (if label→value) or bullet_list_plain.
   - key_value_grid: 4-6 parallel facts (label → value) sharing a common dimension — a single column header above would describe every cell. No implied ordering, no pairwise contrast. Items without shared label→value structure → bullet_list_plain.
   - pillar_layout: 3-4 vertical pillar columns of equal weight, each titled with a strategic theme + 2-4 sub-bullets beneath. Plan signal: "Our N pillars are…", "Strategy rests on N pillars". Pick when each top-level item has its own sub-list.
   - bullet_list_plain: 3-6 narrative bullets without shared dimension (so not key_value_grid), pairwise contrast (not comparison_cards), ordering (not numbered_list_badged), or hero number (not stat_callout). Defining cue: LACK of parallel structure. Base case for narrative-leaning slides — observations, candidates considered, things to watch.
   - hero_statement: ONE bold declarative claim deserves full-canvas weight ("We will be the default LLM for enterprise by 2027"). Plan supplies a single thesis-sentence. Distinguish from stat_callout (number) and none (sparse typography without canvas-dominating weight).
   - thesis_card: single FRAMED card with one thesis + 1-2 sentence rationale, set on otherwise-empty canvas. Distinguished from hero_statement (no card frame) and key_takeaway_box (recap, late in deck). Pick for early-deck framing thesis ("Our thesis is X because Y").
   - agenda: list of upcoming SECTIONS (not body content). Plan section titled "Agenda" / "What we'll cover" or opens with TOC-style enumeration. Typically slide 2. 3-6 section names, each optionally with a 1-line preview.
   - section_divider: rhythm-break slide between major arcs of a long deck (10+ slides typical). Plan signals via "## Part 2: <name>" or distinct narrative phases. Single phrase only — the section name — no body content, no bullets.
   - closing_thematic: final-slide bookend returning to the deck's opening theme with NEW framing ("thank you", "what we built toward", "what we're inviting you to") — typography-led. Distinguished from cta_action (action) and key_takeaway_box (recap). LITERAL LAST SLIDE carrying thematic / emotional weight.
   - key_takeaway_box: single boxed insight summarizing the preceding slides. Plan supplies "bottom line" / "key takeaway" / "Recap" content. Typically slide N-2 or N-1. Distinguished from pull_quote and hero_statement.
   - cta_action: drives a specific NEXT-STEP action. Plan section titled "Next steps" / "The ask" / "What we need from you" with 1-3 concrete actions. Last or second-last slide pattern.
   - multi_stat_row: 3-4 same-axis stats SIDE BY SIDE, each with big number + short caption. Defining cue: plan supplies 3-4 NUMBERS as equal-weight framing stats where numbers dominate cells. If labels would dominate, use key_value_grid.
   - definition: single TERM + EXPLANATION. Two-pane layout. Defining cue: ONE concept being unpacked ("What is RAG?", "Defining incremental scaling"). N≥3 terms each defined → key_value_grid.
   - table_grid: dense tabular data, ≥3 columns × ≥4 rows (≥12 cells), with explicit header row + parallel data rows under them. ≤6 cells → key_value_grid.
   - roadmap: PHASES × WORKSTREAMS grid (Q1/Q2/Q3/Q4 across rows of workstreams). Each cell describes one phase's activity for one workstream. Dated events on one line → timeline.
   - before_after: STRONG binary contrast between a before-state and an after-state — SAME metric in TWO states with directional framing (current→target, naïve→optimized). Peer alternatives → comparison_cards.
   - quadrant_2x2: items positioned on TWO labeled axes ("impact vs effort", "value vs complexity"). Items placed in quadrants based on values on both axes. 4 unrelated facts without axes → key_value_grid.
   - stack_layers: layered abstraction — N stacked rectangles where layers sit ADJACENT with no arrows (OSI model, tech stack, infrastructure → app). Adjacency itself encodes the relationship.
   - bespoke_diagram: freeform structural picture — system architectures, tier diagrams, request lifecycles, funnels, pyramids, cycle loops, hub-and-spokes, venn overlaps, ecosystem maps, waterfall visualizations, org charts, network graphs, custom shapes with arrows. Plan supplies ≥3 named components with relationships AND layout doesn't fit other treatments.
   - code_block: plan contains ONE code/pseudocode/config snippet. TWO code variants (before/after, option-A/option-B) → code_side_by_side instead.
   - code_side_by_side: TWO code snippets shown adjacent — before/after, naïve/optimized, option-A/option-B. Both panels monospace + syntactic coloring with brief titles atop each. Plan signal: section with two code variants and implicit "what changed".

8. ITEM-COUNT → GRID-SHAPE. 2 items: 1×2. 3: 1×3 or 3×1. 4: 2×2 or 1×4. 5: 1×5 or 2-then-3 — never 3×2 with an empty cell. 6: 3×2 or 2×3. Don't silently drop, don't pad with empties.

9. NARRATIVE ARC. opening → early_body → mid_body → late_body → closing. Slide titles read in sequence should let a skim-reader follow the argument.

TRACE STYLE — plan-grounded, not rule-grounded

Cite observable inputs (plan content, palette tokens, available_icons, audience), NOT rule names.

GOOD: "Eyebrow on this card would read 'DATA SCIENTIST' — that's just the uppercase of card title 'Data scientist agent'. Skipping; carries no new info."

BAD: "Per the non-duplication rule, eyebrow skipped."

Negative articulation for treatment choice — explicitly consider and reject plausible alternatives:

"Considered stat_callout — rejected, no anchor number in the plan for this slide. Considered comparison_cards — items aren't paired contrastively. Choosing key_value_grid because four parallel facts with no implied ordering."

INLINE DIRECTIVE HANDLING

When the plan contains [[directive text]] at the end of a bullet, preserve it in the corresponding key_content_points entry verbatim. The slide_design_trace must explicitly acknowledge it:

"User directive '[[highlight this number in the accent color and make it bold]]' on bullet 2 — interpreting as 'apply palette.accent + bold to the 2.1× text run'. Passing this through; coder will realize it."
"""


def build_designer_user_message(
    plan_md: str, available_icons: list[str], palette_family: str
) -> str:
    icons_block = json.dumps(available_icons)
    return (
        "PLAN\n"
        f"{plan_md.rstrip()}\n"
        "\n"
        f"AVAILABLE_ICONS\n{icons_block}\n"
        "\n"
        f"PALETTE_FAMILY HINT\n{palette_family}\n"
    )


# Fields stripped from the deck before serializing as the designer's target
# output — these belong to the coder, not the designer.
_CODER_FIELDS = ("output_js", "reasoning_trace")


def designer_target_payload(deck: dict[str, Any]) -> dict[str, Any]:
    """Strip coder-only fields from a deck JSON for designer training."""
    out: dict[str, Any] = {}
    for k, v in deck.items():
        if k == "slides":
            continue
        out[k] = v
    out["slides"] = [
        {sk: sv for sk, sv in slide.items() if sk not in _CODER_FIELDS}
        for slide in deck["slides"]
    ]
    return out


def build_designer_assistant_message(deck: dict[str, Any]) -> str:
    payload = designer_target_payload(deck)
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Coder
# ---------------------------------------------------------------------------

CODER_SYSTEM_PROMPT = """You generate one slide of a pptxgenjs deck.

INPUTS YOU RECEIVE
- The deck-level brief (palette, chrome.card_style, available_icons, audience, tone, genre, deck_title, deck_subtitle).
- The titles of slides that came before this one (for cross-slide coherence).
- This slide's brief: n, of_total, is_cover, is_closing, position_in_deck, slide_title, main_message, key_content_points (may contain [[directives]]), visual_treatment, per-slide chrome flags, eyebrow_text/subtitle_text when applicable.
- The slide_design_trace from the designer for context.

OUTPUT
1. A reasoning trace inside <think>...</think>. Target 100-200 words explaining layout, positioning, typography, and chrome decisions. Include x-overflow AND y-overflow checks at the end. Be terse.
2. The pptxgenjs JavaScript that draws the slide, AFTER </think>.

PRE-BOUND RUNTIME NAMES (do NOT require/import)
- slide, pres, palette, slide_n, of_total
- darkFooter(slide, n, total), lightFooter(slide, n, total) — pick by palette.is_dark.
- connector(slide, x1, y1, x2, y2, color, opts) — arrowhead by default; opts.arrow = "none"|"from"|"to"|"both".
- makeShadow(), softShadow() — shadow option factories; never share.

CANVAS: 13.333 in × 7.5 in. Coordinates in inches, font sizes in points. Hex colors: 6 chars, no leading #.

HARD RULES

Fidelity to the brief:
- The brief is the ONLY source of content. Render exactly what key_content_points, main_message, and the titles contain. Never introduce a fact, figure, name, date, URL, repo path, document ID, version, column, or claim that is not in the brief.
- A count in the brief ("14 use cases", "six risks") is descriptive, NOT a quota. If the brief states a number but enumerates fewer items, render only the items enumerated — never fabricate items to reach the count.
- If a layout has more slots than the brief has items, use fewer slots. Uneven columns and shorter grids are fine; never pad an array, card, column, or chart series with invented content to fill space.
- Never add a column, field, or attribute the brief did not supply.
- Condensing, rephrasing, reordering, and splitting the brief's content is allowed; adding to it is not. If content underfills a treatment, render it sparser — never invent to rescue the layout.

Strings:
- ALWAYS use double-quoted JS strings ("..."). Never single-quoted. Single-quoted strings break when content contains apostrophes ("can't", "GA'd", "what's") — which it often does. Double quotes side-step the entire bug class.

Color and font:
- Every color from palette.*. No bare hex literals.
- Every fontFace from palette.typography.* or a deliberate code/math choice.

Icons:
- Icons from available_icons ONLY. If empty, no icons. Reasoning trace MUST acknowledge availability.

Chrome (per-slide flags decide WHETHER):
- emit_eyebrow → render eyebrow_text VERBATIM in palette.accent, small-caps via charSpacing 4. NEVER abbreviate by truncation — if it doesn't fit, the designer should have skipped it.
- emit_subtitle → render subtitle_text in palette.muted, italic, body font.
- emit_accent_rule → 1.2 in wide × 0.04 in high rectangle in palette.accent under the title.
- emit_footer → call darkFooter or lightFooter per palette.is_dark.
- emit_bottom_strip → small charSpacing'd metadata in palette.muted near the bottom of the cover.
When a flag is false, omit that element entirely.

VISUAL TREATMENT CANONICAL REALIZATIONS (never mix)

- none: typographic-only (covers, sparse closings). Title + optional eyebrow + optional subtitle + optional bottom-strip. No body chrome.
- stat_callout: ONE hero number anchors the slide. fontSize 72-120pt band, default 96pt. NEVER exceed 120pt — wide chars overflow horizontally. REQUIRED width check before emit: chars × fontSize × 0.6 ≤ box_width_in × 72. Pick largest size satisfying the bound, capped at 120. Hero in headline_font palette.accent bold; narrative in body_font palette.muted to right or below.
- comparison_cards: filled palette.light cards, no border. Each card has small accent-label (LEFT/RIGHT or A/B), title, body bullets. Geometry: padX=0.7, gap=0.3-0.4, cardW=(13.0-2*padX-(N-1)*gap)/N.
- key_value_grid: no fill, no border, thin palette.muted separator lines only. Per cell: small accent eyebrow + headline-font value + body-font explanation. Geometry: 2×2 or 3×2 grid with thin separator at midline.
- pillar_layout: 3-4 vertical pillar columns of equal weight. Per pillar: filled palette.light top with theme title in headline_font bold + 2-4 short sub-bullets below in body_font. Whitespace between pillars. Geometry: padX=0.7, gap=0.3, pillarW=(13.0-2*padX-(N-1)*gap)/N.
- process_flow: connector(...) arrows between uniform stage cards (filled palette.light, h=1.0-1.5, equally spaced horizontally). Connectors at vertical mid of cards.
- stepped_diagram: ASCENDING step shapes (1st step at bottom-left, last at top-right). Each step is a labeled rectangle with title + 1-line body. palette.accent fill for the highest step, palette.light for others. Geometry: stairs rising left-to-right, step h≈0.8, vertical offset 0.4.
- bar_chart: native pres.addChart(pres.ChartType.bar, ...), never manual rectangles. Single-scale series only. chartColors accepts per-series array.
- line_chart: native pres.addChart(pres.ChartType.line, ...). X-axis is time, Y-axis is the metric. Primary series uses palette.accent; secondary palette.secondary_accent. Markers visible (lineDataSymbolSize: 7). Y-gridlines in palette.muted.

  CRITICAL pptxgenjs constraint — chart options take SINGLE values, never arrays: lineDash, dashType, chartLineDash, valGridLine.style, catGridLine.style, lineDataSymbol, lineDataSymbolSize, lineSize are single-string or single-number values applied uniformly across all series. DO NOT pass arrays like lineDash: ["solid", "dash"] thinking they style series A vs series B — pptxgenjs joins the array with a comma producing invalid OOXML (e.g. <a:prstDash val="solid,dash"/>), and PowerPoint silently fails to render. The ONLY chart option that accepts a per-series array is chartColors. To differentiate series visually, use color alone via chartColors: [palette.accent, palette.secondary_accent].
- donut_chart: native pres.addChart(pres.ChartType.doughnut, ...) with holeSize: 50. Segment colors cycle palette.accent → secondary_accent → muted → light variants. Legend on right (legendPos: "r"). Optional center label (large number) as a separate text box at chart center.
- code_block: monospace text on palette.light panel, fontFace "Consolas" 12-14pt. ONE snippet centered.
- code_side_by_side: TWO code panels adjacent, each on palette.light with "Consolas" monospace, brief title above each (V4/V5, BEFORE/AFTER) in accent charSpacing 4. Highlight changed tokens via rich-text run arrays with palette.accent bold. Geometry: padX=0.7, gap=0.3, cardW=(13.0-1.4-0.3)/2=5.65.
- bespoke_diagram: freeform layout for system architectures, tier diagrams, funnels, pyramids, cycle loops, hub-and-spokes, venn overlaps, ecosystem maps, custom shapes with arrows. Named-component rectangles filled palette.light + connector(...) arrows in palette.muted or palette.accent. Use palette.accent for primary spine elements.
- timeline: horizontal axis line at one y-coordinate; dated milestones (dots or short ticks in palette.accent) at proportional x positions; date labels above or below, milestone titles + 1-line bodies in alternating bands. connector(...) only for the axis line.
- numbered_list_badged: vertical stack of rows. Each row = circular badge (filled palette.accent) with white number + bold title to the right + body line below. No card fill — badges provide rhythm. Rows vertically equidistant.
- pull_quote: large italic body-font quoted text (32-40pt) in palette.dark_text occupying upper two-thirds of canvas; attribution below in palette.muted body font 14-16pt charSpacing 4 prefixed with em-dash. Optional vertical accent rule (palette.accent, 0.04in wide) to left of quote.
- bullet_list_plain: single-column bullet list with NO card fill, NO border, NO separator lines, NO badges. Title at standard top. Body bullets in body_font 14-16pt palette.dark_text. Single text box at x≈0.7, w≈11.0-11.5, y≈1.6-2.0. bullet: true + breakLine: true per item. Resist adding a filled card — the point is lightweight typography.
- hero_statement: ONE oversized declarative claim dominates the canvas. headline_font 44-72pt depending on length (longer = smaller; wrap up to 3 lines). palette.dark_text. Anchor CENTERED (x=0.7, w=11.9, y centered around 3.5) OR LEFT-ANCHORED (x=0.7, w=8.5, y=2.5). Optional 1-line secondary in palette.muted body 16-18pt italic below. NO bullets, NO cards. Width check: claim_chars × fontSize × 0.55 ≤ box_width × 72.
- thesis_card: single FRAMED card on otherwise-empty canvas. Filled palette.light card at x≈1.5, y≈2.5, w≈10.3, h≈2.5, optional vertical accent rule on left edge. Thesis in headline_font 28-36pt palette.dark_text bold inside; rationale below in body_font 14-16pt palette.muted lineSpacingMultiple 1.4. Optional small label "OUR THESIS" / "THE CLAIM" above in palette.accent charSpacing 4 11pt.
- agenda: vertical list of SECTION TITLES (not body bullets). Title "Agenda" / "What we'll cover" at standard position. 3-6 rows. Per row: section name in headline_font 18-24pt palette.dark_text bold, optional 1-line preview in body_font 12-14pt palette.muted. Left-margin marker per row: numbered prefix ("01" / "02" in palette.accent charSpacing 4, 14pt) OR semantic icon. Single column at w=11.5 OR 2×3 grid for 6 sections. NO card fills.
- section_divider: full-bleed or 80%-bleed background in palette.accent (or palette.primary on dark decks). Single phrase — the section name — in palette.light (or palette.bg if accent is light) headline_font 48-72pt centered or left-anchored. Optional small section-number prefix in muted accent above. NO body. Skip title block (phrase IS the title). emit_footer=false.
- closing_thematic: final-slide bookend. Typography-led, NOT an action list. Single thematic phrase or 3-line bookend in headline_font 36-56pt palette.dark_text centered or left-anchored, optional small subtitle below in palette.muted italic body 16-18pt. Echoes cover's framing register. NO cards, NO bullets. emit_footer may be false; emit_accent_rule typically false (cover symmetry).
- key_takeaway_box: a single filled emphasis box dominates the canvas. Title at standard position. Below it, rectangle at x=1.2-1.5, w=10.3-10.6, y≈2.2, h≈2.5, filled palette.accent (or palette.primary on dark). Inside box: takeaway phrase in palette.light headline_font 26-36pt centered, optionally with small leading label ("KEY TAKEAWAY" / "BOTTOM LINE" in palette.light opacity 0.6, charSpacing 4, 12pt) above the phrase. The box IS the chrome.
- cta_action: drives NEXT-STEP ask. Oversized ask in headline_font 32-44pt at x=0.7, w=11.9, y≈1.8-2.5. Secondary context in body_font 16-18pt palette.muted below. Optional 1-3 action items as clean numbered list (small palette.accent numerals, no badges) at y≈4.5+. Optional contact metadata at bottom-right charSpacing 4. emit_accent_rule=true; eyebrow off.
- multi_stat_row: N (typically 3-4) equal-width cells SIDE BY SIDE, each with hero number + caption. NO card fills, NO separators — whitespace IS the separator. Geometry: padX=0.7, gap=0.4, cellW=(13.0-2*padX-(N-1)*gap)/N. Number in headline_font or "JetBrains Mono" 56-72pt palette.accent centered. Caption in body_font 12-14pt palette.muted below. Optional small eyebrow above each number.
- definition: two-pane layout. Horizontal split: term at x=0.7 y=2.3 w=5.5 h=2.0 in headline_font 40-56pt palette.dark_text bold; body at x=6.7 y=2.3 w=5.9 h=4.0 in body_font 16-18pt palette.dark_text lineSpacingMultiple 1.5. Small "DEFINITION" label above term in palette.muted charSpacing 4 11pt. Optional vertical accent rule LEFT of term. NO bullets — prose only.
- table_grid: use pres.addTable(...). Header row filled palette.primary with palette.light text body_font bold 12-13pt charSpacing 2. Body rows alternate palette.bg / lighter shade. Number cells right-aligned; text cells left-aligned. Geometry: x=0.7, w=11.9, y≈1.7-2.0. Row heights uniform. NO icons in cells.
- roadmap: M-row × N-column grid where columns are PHASES (Q1/Q2/Q3/Q4 or NOW/NEXT/LATER) and rows are WORKSTREAMS. Phase column headers as filled strips at top in palette.accent (h=0.4), palette.light text headline_font 14-16pt bold centered charSpacing 4. Workstream row labels in leftmost column (w=1.8), palette.primary text body_font bold 12-14pt. Body cells body_font 11-12pt palette.dark_text; thin palette.muted rules.
- before_after: TWO equal half-canvas panels side by side. Each filled palette.light (or BEFORE in muted-tinted bg, AFTER in accent-tinted bg). Left x=0.7 y=2.0 w=5.85 h=4.3; right x=6.85 y=2.0 w=5.8 h=4.3. Small label at top of each in palette.accent charSpacing 4 12pt ("BEFORE"/"AFTER"). Optional connector(...) arrow at y=4.1 between panels. Panels MUST mirror structure.
- quadrant_2x2: 2×2 grid with explicit X and Y axis labels. Grid origin at x=2.5 y=2.0, quadrant w=4.5 h=2.2. Crosshair lines (palette.muted, 0.02in) at x=7.0 and y=3.1. X-axis label centered below grid at x=7.0 y=6.55 charSpacing 4 body_font 11pt. Y-axis label LEFT of grid rotated -90 at x=2.1 y=3.1. Items as small filled circles (palette.accent r=0.08) with body_font 11-12pt labels.
- stack_layers: vertically stacked rectangles ADJACENT (no gap). Each layer w=10.0, h=(5.0/N) capped 0.7-1.1; starting y=2.0, x=1.6. Each layer filled palette.light with 0.02in palette.muted rule at TOP edge. Label centered inside in body_font 18-22pt palette.dark_text bold. Optional subtext below in body_font 11-12pt palette.muted italic. Top and bottom of stack get 0.08-high accent bars.

Layout (BOTH axes — historical training overweighted y):
- X-overflow check: x + w ≤ 13.0 for every shape. Canvas is 13.333 in but reserve 0.33 in right margin.
- Y-overflow check: y + h ≤ 6.9 when footer emitted (footer at 7.05); y + h ≤ 7.3 when no footer.
- N-column grids: compute cellW = (canvas_width - 2*pad - (N-1)*gap) / N, then verify cellW * N + (N-1)*gap + 2*pad ≤ 13.0 BEFORE emitting any card.

Typography:
- charSpacing > 0 ONLY on UPPERCASE single-word labels.
- palette.accent for stripes/eyebrows/key text/small icons/chart series — NEVER for large fills. Cards use palette.light.
- One slide title per slide.

Content:
- Plain text only. No markdown markers. Inline emphasis → rich-text run arrays ([{text: "...", options: {...}}]).
- Bullet lists: every item except the last needs breakLine: true. Use bullet: true. Never type unicode bullet characters.
- Nested sub-bullets: parent at indentLevel: 0, sub-items at indentLevel: 1, both with bullet: true. breakLine: true on every item except the very last across all bullets.
- Card body MUST NOT echo card title. Body adds NEW info; if body is "title plus a tail", body needs a different angle.
- Card content isolation: each card draws ONLY from its assigned key_content_points. Don't borrow facts from neighboring cards.

INLINE DIRECTIVE HANDLING

When a key_content_point, eyebrow_text, or subtitle_text contains a [[directive text]] fragment, parse the intent and apply the corresponding styling via rich-text run arrays.

Examples:
- [[highlight this number in the accent color and make it bold]] → wrap the relevant text run in {color: palette.accent, bold: true}.
- [[pull this out as a callout]] → render as a visually distinct block with a small palette.accent vertical rule next to it.
- [[render this in a serif font]] → set fontFace for that run to a serif from palette.typography.

Reasoning trace must acknowledge directives: "User directive '...' on bullet N — applying [styling] via [JS construct]."

TRACE STYLE — plan-grounded, not rule-grounded

Cite plan content + brief content + coordinates. NOT rule names.

GOOD: "Subtitle at y=1.3 in palette.muted italic; states the takeaway in one line so a skim-reader gets the slide's punchline."

BAD: "Subtitle rendered per chrome rules and trace-style guidelines."

Never reference required_treatments or upstream directives. Do NOT write "this is a required treatment", "the brief asked for X", "I was instructed to use X", or any equivalent — even if the slide_design_trace you received mentions it. Reason about the treatment purely from the slide's content: why a bar chart is the right way to show this specific same-scale series, why a timeline is the right way to lay out these dated milestones. The trace should read as if the treatment were a free choice grounded entirely in what the slide says.

MATH REASONING — gated by layout complexity

INCLUDE math reasoning in the trace for: multi-column grids (≥3 cells), charts, process flows with connectors, layered diagrams. Step through cellW = ..., x positions = ..., so coordinate errors get caught mid-pass.

SKIP math reasoning for: covers, single stat_callout, hero statements, plain bullet lists, 1×N or 2-column simple grids. The math is trivial and trace noise.

No harness hooks:
- No pres.writeFile — the harness saves.
"""


def _deck_brief_block(deck: dict[str, Any]) -> str:
    palette = deck["palette"]
    tokens = palette["tokens"]
    typo = palette["typography"]
    return (
        f"DECK BRIEF\n"
        f"  title: {deck['deck_title']}\n"
        f"  subtitle: {deck.get('deck_subtitle', '')}\n"
        f"  genre: {deck['deck_genre']}\n"
        f"  audience: {deck['audience']}\n"
        f"  tone: {deck['tone']}\n"
        f"  palette.is_dark: {palette['is_dark']}\n"
        f"  palette.tokens: {json.dumps(tokens)}\n"
        f"  palette.typography: headline={typo['headline_font']}, body={typo['body_font']}\n"
        f"  palette.rationale: {palette.get('rationale', '')}\n"
        f"  chrome: {json.dumps(deck['chrome'])}\n"
        f"  available_icons: {json.dumps(deck.get('available_icons', []))}\n"
    )


def _prior_titles_block(prior_titles: list[str]) -> str:
    if not prior_titles:
        return "PRIOR SLIDES\n  (none — this is the cover)\n"
    lines = "\n".join(f"  {i + 1}. {t}" for i, t in enumerate(prior_titles))
    return f"PRIOR SLIDES\n{lines}\n"


def _slide_spec_block(slide: dict[str, Any]) -> str:
    def _fmt_kcp(p: Any) -> str:
        if isinstance(p, dict):
            head = f"    - {p.get('text', '')}"
            subs = "\n".join(f"      - {s}" for s in p.get("sub", []) or [])
            return f"{head}\n{subs}" if subs else head
        return f"    - {p}"
    points = "\n".join(_fmt_kcp(p) for p in slide.get("key_content_points", []))
    chrome = slide.get("chrome") or {}
    chrome_line = f"  chrome: {json.dumps(chrome)}\n" if chrome else ""
    eyebrow_line = (
        f"  eyebrow_text: {slide['eyebrow_text']}\n"
        if chrome.get("emit_eyebrow") and slide.get("eyebrow_text")
        else ""
    )
    subtitle_line = (
        f"  subtitle_text: {slide['subtitle_text']}\n"
        if chrome.get("emit_subtitle") and slide.get("subtitle_text")
        else ""
    )
    return (
        f"THIS SLIDE\n"
        f"  n: {slide['n']}\n"
        f"  of_total: {slide['of_total']}\n"
        f"  is_cover: {slide['is_cover']}\n"
        f"  is_closing: {slide['is_closing']}\n"
        f"  position_in_deck: {slide['position_in_deck']}\n"
        f"  slide_title: {slide['slide_title']}\n"
        f"  main_message: {slide['main_message']}\n"
        f"  visual_treatment: {slide['visual_treatment']}\n"
        f"{chrome_line}"
        f"{eyebrow_line}"
        f"{subtitle_line}"
        f"  key_content_points:\n{points}\n"
        f"  slide_design_trace: {slide['slide_design_trace']}\n"
    )


def build_coder_user_message(
    deck: dict[str, Any], slide: dict[str, Any], prior_titles: list[str]
) -> str:
    return (
        _deck_brief_block(deck)
        + "\n"
        + _prior_titles_block(prior_titles)
        + "\n"
        + _slide_spec_block(slide)
    )


def build_coder_assistant_message(slide: dict[str, Any]) -> str:
    code = slide["output_js"]
    trace = slide["reasoning_trace"]
    return f"{THINK_OPEN}{trace}{THINK_CLOSE}\n{code}"


# ---------------------------------------------------------------------------
# Output parsing (used by generate.py and eval)
# ---------------------------------------------------------------------------

_HARMONY_CHANNEL_NAMES = ("analysis", "commentary", "final")


def _strip_harmony_channel_prefix(text: str) -> str:
    """gpt-oss uses Harmony channels (<|channel|>final<|message|>...). When
    decoded with skip_special_tokens=True the markers vanish but the literal
    channel name 'final' remains glued to the content. Strip it.
    """
    for name in _HARMONY_CHANNEL_NAMES:
        if text.startswith(name):
            return text[len(name):]
    return text


def split_trace_and_code(assistant_text: str) -> tuple[str | None, str]:
    """Reverse of build_coder_assistant_message — used by the eval pipeline."""
    assistant_text = _strip_harmony_channel_prefix(assistant_text)
    if THINK_OPEN in assistant_text and THINK_CLOSE in assistant_text:
        start = assistant_text.index(THINK_OPEN) + len(THINK_OPEN)
        end = assistant_text.index(THINK_CLOSE)
        trace = assistant_text[start:end]
        code = assistant_text[end + len(THINK_CLOSE):].lstrip("\n")
        return trace, code
    return None, assistant_text


def parse_designer_output(assistant_text: str) -> dict[str, Any]:
    """Parse the designer's assistant message back into a deck JSON dict.

    Tolerates a leading harmony channel prefix, ```json``` fencing, and
    minor LoRA-generated JSON breakage. The most common breakage we see:
    the deck_design_trace field quotes inline plan directives verbatim,
    including the inner `"X"` in `[[highlight "X" in accent color]]`, and
    those double-quotes are not escaped inside the JSON string value. We
    try strict json.loads first; on failure we fall back to json_repair,
    which handles the unescaped-quote case correctly. Diagnosed
    2026-05-27 on PresentBench MS-13 (Shenzhou-themed deck with many
    `[[highlight "..."]]` directives), where 4 retries all produced the
    same parse failure at similar character positions — i.e. the failure
    was deterministic for the input, not roll variance, and prompted a
    repair pass rather than a re-roll.
    """
    text = _strip_harmony_channel_prefix(assistant_text).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[: -3]
    # strict=False allows raw \n / \t inside string values. The designer
    # sometimes formats long deck_design_trace strings as multi-paragraph
    # prose with literal newlines, which strict JSON forbids but is otherwise
    # well-formed.
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError:
        # Fallback: json_repair handles unescaped inner double-quotes and a
        # few other LoRA-generated breakages without losing content.
        import json_repair
        repaired = json.loads(json_repair.repair_json(text), strict=False)
        # Second observed shape: the LoRA sometimes accidentally closes the
        # deck object early and emits a trailing slide or two at the top
        # level instead of inside `slides[]`. json_repair recovers this as
        # a top-level list `[deck_with_partial_slides, slide_N, slide_N+1]`.
        # Diagnosed 2026-05-27 on PresentBench MS-06 (Ne Zha / Shen Gongbao):
        # 10 slides in slides[], 2 more dicts at top level. Pull the trailing
        # slide-shaped dicts back into the deck's slides list so the deck
        # builds with its full slide count.
        if isinstance(repaired, list):
            if not repaired or not isinstance(repaired[0], dict):
                raise ValueError(
                    "designer output parsed as a list with no dict head; "
                    "cannot recover a deck object")
            deck = repaired[0]
            tail_slides = [item for item in repaired[1:]
                           if isinstance(item, dict)
                           and "n" in item
                           and "slide_design_trace" in item]
            if tail_slides:
                deck.setdefault("slides", []).extend(tail_slides)
            return deck
        return repaired
