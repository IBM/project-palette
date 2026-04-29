from __future__ import annotations

import json
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx

import base64

from agent_core import chat_complete, rits_chat_complete, vision_chat_complete
from renderer import build_pptx, render_previews

log = logging.getLogger("tool")


_REFLECTION_PROMPT = """You are a presentation strategist. A user has asked you to build a deck. BEFORE picking layouts or writing slides, you reflect on what kind of document they're asking for and what good instances of that document type contain.

Write a short prose reflection (4–10 sentences) covering:

1. **What kind of document is this?** Explainer, strategy doc, status update, pitch, research summary, retrospective, technical deep-dive, investor update, customer pitch, internal review — name the genre in your own words. Don't pick from a fixed list.

2. **Who is the audience and what do they walk away with?** What background do they have? What should they be able to do, decide, or believe afterward?

3. **What does this kind of document, on this specific topic, need to contain to be useful?** This is the most important part. Reason from first principles about the topic itself. What are the natural sub-pieces? What ordering makes sense — pedagogical, analytical, narrative, problem-solution? What concrete examples or evidence anchor the content? What questions would the audience have that the deck must answer? Don't sketch slides yet; reason about content needs.

4. **Is the request clear enough to plan a good deck, or is it so ambiguous that no sensible default exists?** This bar is HIGH. Most requests have a sensible default interpretation that the user will be happy with — even if the user didn't specify everything. Only ask if the topic itself is so broad that you genuinely don't know what document the user wants.

End your reflection with EXACTLY ONE of these signals on its own line:

  READY_TO_PLAN
or
  NEEDS_CLARIFICATION: <question 1> | <question 2>

Default strongly to READY_TO_PLAN. Make a sensible choice and proceed. The user can always iterate after seeing the blueprint.

Sensible defaults you should make WITHOUT asking:
- Audience expertise level — for a technical topic, default to "practitioners / engineers / researchers in the field." For a business topic, default to "decision-makers familiar with the business."
- Tone — default to professional but accessible.
- Depth — default to substantive but not exhaustive (~10–14 slides for a complex topic, ~6–8 for a focused one).
- Scope — interpret the topic as written.

Examples of when to use READY_TO_PLAN (the common case):
- "Build a deck explaining transformer architecture for decoder only models" → clear topic, default to engineers/researchers, proceed.
- "Q3 2025 earnings deck for IBM" → clear genre + topic. Proceed.
- "Pitch deck for our AI security startup" → clear genre + topic. Proceed; default to standard VC pitch arc.

Examples of when to ASK (rare — only when the topic itself is the ambiguity):
- "AI safety" — could be a technical-research summary, a policy argument, a non-technical intro, or a regulatory pitch. Ask 1 sharp question about angle.
- "Make a deck about IBM" — could be company history, financial analysis, investor pitch, or product overview. Ask.

When in doubt, ALWAYS prefer READY_TO_PLAN.
"""


_BLUEPRINT_PROMPT = """You are a presentation outliner. You have just reflected (in prose) on what kind of deck the user wants and what content it needs. Now produce a HIGH-LEVEL STRUCTURED BLUEPRINT — one entry per slide.

The blueprint commits to: how many slides, what each slide is FOR (its role in the deck narrative), what high-level points it covers, and what evidence each slide depends on. It does NOT pick the specific examples, numbers, quotations, or content that will appear on the slide — those are decided at later stages with full evidence in hand. It does NOT pick layouts.

Return ONLY a JSON object, no prose, no code fences:

{
  "deck_title": "<short, punchy deck title — this is what goes on the cover slide>",
  "deck_subtitle": "<optional, one descriptive line for the cover>",
  "total_slides": <integer, including cover>,
  "slides": [
    {
      "n": 1,
      "title": "<this slide's title — short, declarative>",
      "purpose": "<what this slide DOES in the deck — e.g. 'introduce the deck', 'set up the problem', 'define the core mechanism', 'compare alternatives', 'land the takeaway'>",
      "key_points": [
        "<a high-level qualitative claim or sub-topic this slide covers>",
        "<another claim or sub-topic — 2 to 4 items total>"
      ],
      "evidence_needed": "<optional, what facts/data/research this slide depends on, phrased as a search query. Empty if conceptual / general knowledge>"
    },
    ...
  ]
}

## Critical rules

- The first slide is ALWAYS a cover. Its `title` should be the deck title verbatim (the same string you wrote in `deck_title`), NOT the literal word "Cover" or similar. Its `key_points` should describe the deck's overall focus at 1-2 high-level claims. Do NOT mention "presenter", "date", "[Your Name]", or any placeholder labels — the cover slide visually shows just the title and subtitle.

- **NEVER add an Agenda / Outline / Roadmap / Table of Contents slide for decks of 15 slides or fewer.** Decks of this size don't need a roadmap — the audience reads slide titles as they advance, and a redundant agenda slide just reads as filler. Specifically: do not generate slides titled "Agenda", "Outline", "Roadmap", "What we'll cover", "In this deck", "Topics", or any equivalent "this deck has these sections" slide. If the planner is tempted to add one, drop it — the cover slide + the deck's titles already preview the structure. Agenda slides are appropriate ONLY for 16+ slide decks where a true table of contents helps the audience track progress through long material.

- The last slide is ALWAYS some form of closing — recap, key takeaways, next steps, call to action.

- **Number of slides emerges from the content, not from a target.** A topic with 5 natural seams gets 5 content slides + cover + closing = 7 total. Don't pad. Don't compress.

- **key_points are HIGH-LEVEL.** They should be qualitative claims or sub-topics, NOT specific examples, numbers, named cases, or quotations. Bad: "GPT-3 has 175B parameters and was trained on 300B tokens." Good: "scale of leading decoder-only models." The downstream stage decides which specific instance to use, with evidence in hand.

- **purpose is what the slide ACCOMPLISHES, not what it shows.** "Set up the problem" is a purpose. "List 5 techniques" is content. Keep purpose at the role-in-narrative level.

- **title makes the slide's POINT, not just names the TOPIC.** A title that says "Strategic Pillars" leaves the actual pillars unnamed — the audience has to read the body to learn what they are. A title that says "Strategic priorities (the 4 pillars)" or "Strategic priorities — Reframe the narrative, Promote AgentOps, Activate Domain Agents, Convert Wins" commits to the specifics in the title itself. Fold the slide's specific commitment into the title via a parenthetical, an em-dash subtitle, or compound phrasing. Examples (paired — topic-only vs committed):

  topic-only:  "Market Context"
  committed:   "Market context (the $7.84B → $52.62B opportunity)"

  topic-only:  "Differentiation"
  committed:   "Our differentiation (open, integrated, trusted, hybrid)"

  topic-only:  "Causal Masking"
  committed:   "Step 4 — Causal masking blocks future tokens"

  topic-only:  "Campaign Calendar"
  committed:   "Q1 campaign calendar (Jan/Feb/Mar)"

  topic-only:  "Tokens"
  committed:   "Step 1 — Tokens become vectors"

The committed version names what the slide is actually arguing or showing. If the slide is part of a STEPS-shaped deck, prefix with "Step N —". If it commits to a specific number or named set, fold that into the title. The title should let a reader skim the deck's titles and understand the deck's arc without reading bodies.

Limit titles to ≤10 words. If the commitment doesn't fit, abbreviate or use a colon.

**ONLY commit specifics that are grounded.** A specific number ("$1.2M ARR"), named entity ("Acme Bank"), or specific percentage ("95% renewal") in a title must come from one of: (a) the user's original request, (b) gathered evidence in the slide payload, or (c) genuinely canonical / well-known sector figures (e.g. "GPT-3 has 175B parameters" — widely documented). For everything else — pitch metrics, market sizes, customer names, ROI projections, named pilot accounts — DO NOT invent specifics in the title. Either keep the title at the structural level ("Strategic priorities (4 pillars)" without naming them if you don't know the actual pillars) OR append `(illustrative)` to flag that the specifics are placeholders for the user to verify (e.g. *"Traction — 3 pilots, $1.2M ARR (illustrative)"*). The `(illustrative)` tag is your safety valve for committing without lying. Use it whenever you're not sure.

- **Sequence matters.** For an explainer, slides must build on each other — concept B on slide N requires that concept A was introduced on slide N-1. For a strategy doc, the argument arc has to land.

- **evidence_needed flags research the downstream stage will need.** Phrase as a search query. The downstream stage will use the gathered evidence to pick what specifically appears on the slide. If a slide is general/conceptual and needs no specific facts, leave `evidence_needed` empty.

- DO NOT invent specific numbers, names, examples, or quotations at this stage. The blueprint articulates the SHAPE of what each slide does. Specifics belong to the next stage.

- **DO NOT invent names, people, presenters, company names, taglines, or branding** the user didn't supply. For presenters/dates/authorship, use bracketed placeholders like "[presenter name]". Never write "Prepared by AI."
"""


_EVIDENCE_DISTILL_PROMPT = """You are a research distiller. Given a slide's research need and raw web-search results, decide whether the results contain authoritative, specific facts the slide writer can quote.

Return ONLY a JSON object — no prose, no code fences:

{
  "confidence": "high" | "low" | "none",
  "evidence": "<see rubric below>",
  "sources": ["<url 1>", "<url 2>"]
}

Confidence rubric:
- "high": at least one reputable source (analyst firm, regulator, government, vendor primary doc, peer-reviewed paper, well-known publication) gave a direct, quote-worthy fact. `evidence` should be 3-5 short bullet lines distilling these facts, each with the source name in parens — e.g. "AI workflow automation TAM expected to grow 28% CAGR through 2028 (Gartner)".
- "low": directional info only — multiple weak sources, blogs, or vague consensus, but no authoritative specific numbers. `evidence` should be a one-line qualitative summary.
- "none": search returned nothing useful, or results were off-topic. `evidence` should be empty string.

Be honest. "low" and "none" tell the writer to write qualitatively and mark unknowns as "(illustrative — pending review)" rather than fabricate plausible numbers. Inflating confidence is much worse than admitting weak research.
"""


_PALETTE_PROMPT = """You are the PALETTE stage of a slide-generation pipeline. Your one job: pick a coherent color palette and typography pair for this deck, committing to specific hex values and font names that all slides will share.

You receive: the deck title and subtitle, the reflection prose (which describes audience, tone, and topic), and a list of the deck's slide titles.

Why this exists: without a committed palette, slides freelance their own colors and produce a visually inconsistent, bland deck (white background + one blue accent). Your job is to commit to a coherent set of colors that propagate through every slide and through the slide chrome.

You MUST reason in these sections, in order. Do not commit JSON until each section is complete.

## 1. Read the deck

Restate in your own words: what is this deck (genre — pitch / explainer / strategy / post-mortem / market landscape / etc.), who is the audience, and what is the tone (formal / casual / technical / narrative / data-heavy)?

## 2. Pick a palette identity

Decide light vs dark BY GENRE, not by default. Both are first-class options — dark is not the safe pick.

LIGHT background tends to fit:
- Strategy / marketing / business / GTM decks
- Vendor landscapes, executive summaries, analyst-style content
- Academic, document-style, and analytical decks
- Anything readers will skim on a screen for substance over drama

DARK background tends to fit:
- Editorial / dramatic / narrative-arc decks
- Technical deep-dives where code and diagrams pop on dark surfaces
- Pitches that want presence and gravitas
- Decks meant to feel signature and curated, not workmanlike

Many decks could go either way. When in doubt, prefer LIGHT for content-heavy strategy/business decks and DARK for editorial/technical narratives. Cover slides are sometimes dark even when the body of the deck is light — that's a deliberate framing choice the coder can make.

Pick light or dark. State why in one sentence — tie it to the deck's genre.

Then pick an ACCENT identity — the primary color that highlights, fills cards, and creates visual interest. The accent doesn't need a deep thematic justification; it needs to be a coherent color that works against the chosen background. Common patterns that work: navy + electric mint; navy + violet; navy + coral; cream + deep teal; off-white + burgundy; warm white + IBM-style blue/orange; off-white + forest green. Avoid generic primary blue (e.g. #0F62FE) — that's the bland default we're trying to escape.

Optionally pick a SECONDARY accent for emphasis variation (problem-coded red / warning amber / success green / soft cyan / etc.). Used sparingly.

## 3. Commit tokens

Output 7 named-role hex tokens (no `#` prefix, 6-character lowercase or uppercase). Roles:

- `bg` — slide background (the dominant surface across most slides)
- `primary` — the dark fill color for cards/containers on a light background, OR the deck's primary deep tone
- `accent` — the highlight color (used for eyebrow tags, dividers, accent stripes, key callouts, chart series)
- `secondary_accent` — used sparingly for emphasis variation (alert / warning / success — your choice, optional but useful)
- `light` — the light text color for use on dark backgrounds, OR the light surface color for cards on a dark bg
- `muted` — for footers, secondary text, dividers, gridlines
- `dark_text` — the body text color when used on a light background

These 7 tokens must work together. Test mentally: if you draw a card filled with `primary`, with text in `light`, an accent stripe in `accent`, a footer in `muted`, does it look composed? If not, adjust.

## 4. Pick typography

Choose two font faces:
- `headline_font` — used for slide titles, big stats, emphatic headers. Typical: Georgia (serif, editorial), Arial Black (sans, bold), Cambria (serif).
- `body_font` — used for body prose, captions, labels. Typical: Calibri (sans, friendly), Trebuchet MS, or another clean sans-serif.

Stick to widely-available system fonts: Georgia, Cambria, Calibri, Arial, Arial Black, Trebuchet MS, Palatino, Garamond, Consolas (for monospace/code).

A serif headline + sans body pairing is the strongest editorial convention and a safe default. State your rationale in one sentence.

## Output

After the four reasoning sections, output a single JSON object:

{
  "palette_name": "short label, e.g. 'Cybersecurity / mint-on-navy' or 'Editorial / cream-and-burgundy'",
  "rationale": "one to two sentences on why this palette fits the deck",
  "is_dark": true,
  "tokens": {
    "bg": "0B1426",
    "primary": "182447",
    "accent": "8B5CF6",
    "secondary_accent": "06B6D4",
    "light": "E5E9F2",
    "muted": "64748B",
    "dark_text": "1A1A2E"
  },
  "typography": {
    "headline_font": "Georgia",
    "body_font": "Calibri",
    "rationale": "Editorial serif headline + clean sans body."
  }
}

The JSON is the only thing parsed. Reasoning sections above are for you to think — they are not consumed downstream.

## Reference palettes (use as inspiration; invent your own — don't copy)

LIGHT — Strategy / marketing palette (from a watsonx Q1 marketing strategy deck):
  is_dark: false
  bg: F5F2EB (warm off-white / paper), primary: 1A2B5C (deep navy for cards / dark accents),
  accent: E2733B (signature orange — used for stripes, eyebrow tags, key callouts),
  secondary_accent: 5B9BD5 (cool blue, used sparingly for data),
  light: FFFFFF (pure white surface for cards), muted: 8B95A8 (warm gray for footers),
  dark_text: 0F1B3D (near-black navy for body text on the warm bg)
  Typography: Georgia headline + Calibri body.

LIGHT — Vendor landscape palette (from a vector-DB explainer's comparison slide):
  is_dark: false
  bg: F8F9FB (cool white), primary: 1F2937 (charcoal for headlines),
  accent: 8B5CF6 (violet — primary brand accent),
  secondary_accent: 14B8A6 (teal — used for code / second category),
  light: FFFFFF (pure card surface), muted: 6B7280, dark_text: 111827
  Typography: Georgia headline + Calibri body.

DARK — Cybersecurity pitch palette (from a Series A AI-SOC pitch deck):
  is_dark: true
  bg: 0A0E27 (near-black navy), primary: 141B3D (card surface on dark),
  accent: 00E5A0 (electric mint), secondary_accent: FF4757 (alert red, sparingly),
  light: E8ECF7, muted: 7A8AB5, dark_text: 0A0E27
  Typography: Georgia headline + Calibri body.

DARK — Technical-explainer palette (from a transformers / vector-DB technical deep-dive):
  is_dark: true
  bg: 0B1426 (deep navy), primary: 182447 (card surface), accent: 8B5CF6 (violet — math/vector feel),
  secondary_accent: 06B6D4 (cyan — code / tooling),
  light: E5E9F2, muted: 64748B, dark_text: 0B1426
  Typography: Georgia headline + Calibri body.

Notice the structural pattern is the same in both light and dark: ONE signature accent + a secondary for variation + a card surface that contrasts with the bg + a muted for chrome. Don't blindly imitate any of these — pick a palette that makes sense for THIS deck's genre and audience.
"""


_BRIEF_PROMPT = """You are the SLIDE BRIEF stage of a slide-generation pipeline. Your job is to decide WHAT this slide says and WHAT KIND of visual treatment it gets — nothing about layout, nothing about code. A separate downstream stage handles spatial layout, and a stage after that writes the pptxgenjs code.

You receive: the slide's title, purpose, key_points (high-level, qualitative), any gathered evidence, plus deck context (deck_title, deck_subtitle, slide_n, of_total).

Content sources you draw from, in this order:
1. The slide's purpose and key_points (set by the planner)
2. Any gathered evidence in the payload (from research, may be empty)
3. Your own knowledge of the topic — for most subject areas this is where most specifics come from. Use it.

You MUST reason through these sections, in order. Do not skip any. Do not commit to the JSON output until you have completed every section. There is no length limit on reasoning — reason as long as you need.

## 1. Restate the purpose

The planner's `purpose` field tells you what the slide DOES in the deck. Restate it in your own words, sharpening if vague. If the purpose conflates two things, flag it — emit `{"flag": "muddy_purpose", "reason": "..."}` as the JSON output instead of the normal output, and stop. The downstream stages can't recover from a slide that's trying to do two things at once.

## 2. Pick the main message

What is the ONE sentence the reader walks away with after seeing this slide? Pick a sentence that COMMITS to a specific claim — a number, a named set of items, a strategic intent, a definition. The committed version may or may not contain numbers; what matters is that it picks a stance, not a topic-adjacent restatement.

Examples (paired — uncommitted vs committed):

  uncommitted: "Several customer segments behave differently."
  committed:    "Enterprise accounts churn 3x less than SMB after the first renewal."

  uncommitted: "We're working on several research areas."
  committed:    "We're exploring three directions: causal inference under interference, identification with graph data, and policy evaluation in adaptive experiments."

  uncommitted: "Better customer experience matters."
  committed:    "Our 2026 strategy is to compress signup-to-first-value to under 5 minutes."

  uncommitted: "Drug X had effects in the trial."
  committed:    "Drug X reduced sleep-onset latency by 18 minutes vs. placebo (p<0.01)."

  uncommitted: "Causal inference is a useful framework."
  committed:    "Causal inference estimates what would have happened under a different treatment, given observational data."

State your main message; state why it serves the slide's purpose and the deck's narrative arc.

## 3. Decide content_blocks

Now decide the SPECIFIC content that appears on the slide to support the main message. Each block has a role:
- `headline` — the slide's primary statement (often a sharpened version of the title or main message)
- `body` — supporting prose / bullets that elaborate
- `evidence_callout` — a specific number, quotation, or named instance that anchors the claim
- `footnote` — secondary / supporting content (caveats only — citations / source URLs go in speaker_notes, not on the slide)

Pick which specifics elevate to slide content, which become footnotes, which are dropped. Do NOT cram everything in. A slide that says one thing well beats one that gestures at five.

If you commit to a specific number / named example / quotation that you cannot verify against gathered evidence, mark `"illustrative": true` on the block. Prefer canonical / well-known specifics over obscure ones.

FLAG MATH AND CODE CONTENT EXPLICITLY. If a content_block contains a mathematical expression (uses Σ, √, integrals, fractions, subscripts via `_{...}`, superscripts via `^{...}`, Greek letters, or LaTeX-style notation), add `"contains_math": true` to that block. If it contains code (function calls, operators like `→` `=>` `==`, `import` keywords, syntax constructs), add `"contains_code": true`. The downstream coder uses these flags to pick the right typography (Cambria Math for math, Consolas for code). Without these flags, the coder defaults to body_font (Calibri) which renders LaTeX subscripts and code symbols poorly.

USE BRACKETED PLACEHOLDERS for unsupplied user-specific specifics. Things like the user's company name, founder name, customer logos, presenter name, exact dates, ARR numbers, pilot accounts, market sizes for the user's specific niche — if the user did NOT supply them and they are NOT in canonical knowledge, use `[Company name]`, `[Founder name]`, `[Pilot account]`, `[Date]`, `[$X ARR]`, etc. The downstream coder will render them verbatim. The user fills these in later. Do NOT invent a plausible-looking name; bracketed is more honest and easier for the user to find-and-replace.

CITATIONS, SOURCE URLS, AND ATTRIBUTIONS GO IN SPEAKER NOTES, NOT ON THE SLIDE — NO EXCEPTIONS. This rule is violated whenever any of these patterns appear in any visible content_block (headline, body, evidence_callout, footnote):

- A URL ("https://...", "github.com/...", "milvus.io/...", "pangeanic.com")
- A paper citation in author-year form ("Vaswani et al., 2017", "Radford et al., 2018", "Brown et al., 2020")
- A "Sources:" / "Source:" / "Reference:" prefix
- A blog name ("Hugging Face blog", "Princeton blog", "The Moonlight, 2024")
- A "(GitHub issue #...)" reference, "(arXiv ...)", "(see ...)"
- Anything that reads as "where the fact came from" rather than the fact itself

ALL of these belong in the top-level `speaker_notes` field. ON-SLIDE content has the FACT or CLAIM, not the source. The `footnote` content_block role is for QUALIFYING CAVEATS only — "estimates as of Q1 2026", "excludes enterprise tier", "values illustrative" — never for citations.

If you're tempted to put "(pangeanic.com)" or "Hugging Face blog" or "GitHub issue #121193" on a slide, STOP. Move it to speaker_notes. The reader does not need to see citations on every slide; the presenter cites verbally if asked.

NO REDUNDANT BLOCKS. Before finalizing content_blocks AND visual_treatment, read them back and check: do any two of them say the same thing in different words? If so, drop the weaker one. The body bullets, the evidence_callout, and the visual_treatment must each ADD different information.

Specifically forbidden patterns:
- Body bullets that list the same items the visual_treatment is showing. If the visual is `comparison_cards` of 3 customer segments, do NOT also write 3 body bullets describing those segments. The cards ARE the content; body should be empty or carry different supporting prose.
- A `key_value_grid` whose items duplicate body bullets, or vice versa. Pick one carrier — the grid OR the bullets, not both.
- An `evidence_callout` that paraphrases a body bullet in different words.
- A subhead that is a longer-winded version of the headline (the subhead should add a thesis or tension, not restate the title).

If the visual_treatment carries the substance, body should typically be empty or contain a single short paragraph of context — NOT a parallel list. If body carries the substance, the visual is illustrative or absent.

## 4. Decide visual_treatment

Does this slide need a visual? If yes, what kind?

Visual types you can pick:
- `bar_chart` — comparing 2-6 quantities along one axis
- `stat_callout` — one big number with caption
- `comparison_cards` — 2-4 parallel options side-by-side, each with parallel structure
- `process_flow` — sequence of steps with arrows
- `matrix` — grid relationship (e.g., 2x2 quadrants, mapping table)
- `key_value_grid` — N items each with label + short description
- `none` — pure text slide

Match the visual to the main message. Examples:

  main message: "Three pricing tiers serve different customer segments."
  → comparison_cards (3 cards, parallel structure).

  main message: "We're exploring three research directions: causal inference under interference, identification with graph data, policy evaluation in adaptive experiments."
  → key_value_grid (3 items, label + one-line description). NOT a bar chart.

  main message: "Annual customer LTV is $480."
  → stat_callout (one big number with caption).

  main message: "Causal inference estimates what would have happened under a different treatment."
  → none (typographic emphasis on the definition itself).

If the main message names ONE specific outcome, prefer `stat_callout`. If it lists PARALLEL items (techniques, options, segments, phases, directions), prefer `comparison_cards` or `key_value_grid`. If it's a quantitative COMPARISON, `bar_chart`. If it's a SEQUENCE, `process_flow`. If it's a definition, narrative bridge, concept setup, executive summary, or a slide whose substance is best carried by clean prose, `none` is the right answer.

`visual_treatment.type = "none"` IS A FIRST-CLASS OUTPUT. A clean text slide with strong typography is not a fallback or a failure mode — it's a different shape of slide that fits a real category of content. A 14-slide deck might have 3-4 slides with `type: "none"` (cover-style, definition, narrative bridge, closing thought). The downstream coder will write a clean text composition with no cards, no eyebrow, no chrome scaffold — just title + subhead + body. Don't reach for a chart or cards just to "have something visual" — that produces busy, padded slides.

PARALLEL STRUCTURE IS NON-NEGOTIABLE for `comparison_cards`, `key_value_grid`, `process_flow`. Every item in the array MUST share the same shape: same set of fields, same expected length, same level of specificity. The downstream coder renders these via a `forEach` loop where the same drawing code runs N times — so heterogeneous items break visual consistency. Concretely:

  GOOD `comparison_cards` items: each has `{title: "...", body: "..."}` with title 1-3 words and body 12-25 words. All 3 cards land at the same visual weight.
  BAD: card 1 has `{title, body}`, card 2 has `{title, body, footnote, badge}`, card 3 has `{title}`. The forEach can't render this consistently.

  GOOD `key_value_grid` items: each has `{label: "...", value: "..."}` where labels are short (1-4 words) and values are short (4-12 words).
  BAD: one item's value is "12%" and another's is a 40-word paragraph.

  GOOD `process_flow` steps: each has `{label: "...", desc: "..."}` with labels that are nouns or verb phrases of similar length.
  BAD: step 1 is "Tokenize" and step 5 is "After embedding the tokens, the model applies layer normalization across each subspace before the attention block".

Cap N at 4 for cards and key-value grids (more is too dense). Cap process_flow at 6 steps (more is unreadable on 16:9). If your data wants 7+ items, REGROUP into fewer composite items, not more thin ones.

For the chosen visual, specify what each part shows. For a bar chart: labels and values. For comparison_cards: the categories and what each card contains. For a process_flow: the steps in order.

## Output — emit ONE JSON object whose `content_blocks` array contains ONLY the blocks this slide actually needs

Most slides need 1-3 blocks. Only the densest slides need all 4. Emitting all 4 blocks every time produces redundant, padded slides — that is THE failure mode this stage must avoid.

The output schema:

```
{
  "slide_title": "≤8 words, catchy, title case — what reads at the top of the slide",
  "main_message": "the one sentence the reader walks away with — the slide's thesis",
  "rationale": "why this content shape serves the slide's purpose",
  "content_blocks": [ ... 1-4 blocks, see shapes below ... ],
  "visual_treatment": { "type": "...", "what_it_shows": "...", ... type-specific fields ... },
  "speaker_notes": "citations / context that does not belong on the slide. Empty string if none."
}
```

`slide_title` is what the audience reads at the top. It is short, catchy, and committed — but distinct from `main_message`. The main_message is your THESIS sentence (used internally to drive content_blocks); the slide_title is the HEADING the reader sees. They are NOT the same string.

Examples (paired — main_message vs slide_title):

  main_message: "A causal mask is a lower-triangular pattern that lets each position attend to itself and earlier positions only."
  slide_title:  "Causal mask: lower-triangular attention"

  main_message: "Rule-based SOC tools generate >70% false positives and take >30 minutes to triage — they cannot scale to today's alert volume."
  slide_title:  "Why current SOC tools fall short"

  main_message: "Causal mask creation is a one-line operation with minimal runtime cost."
  slide_title:  "One-line mask, negligible cost"

  main_message: "Three inference tricks unlock 2.5x throughput and 4x faster first-token."
  slide_title:  "Three inference tricks"

`slide_title` ≤ 8 words. NEVER copy `main_message` into `slide_title` — that produces 11-15 word titles that wrap to 2 lines and read as paragraphs, not headings. The planner's blueprint title is a starting point; you may adapt it to make it punchier, but keep the same anchor (e.g. don't rename "Causal masking" to "Sequence regularization").

Every block has `{"role": "...", "text": "..."}` with optional `"illustrative": true` for unverifiable specifics.

### Five contrasting shapes — match yours to one of these

The shape of `content_blocks` should match the slide's role in the deck. Below are five contrasting shapes drawn from real decks. Pick the one closest to your slide; do NOT default to the maximalist 4-block shape.

**Shape 1 — Definition / concept setup / narrative bridge (1 block, no visual)**

For: "What is X?", "Why does Y matter?", concept introductions, transitions between sections.

```
{
  "slide_title": "What is causal inference?",
  "main_message": "Causal inference estimates what would have happened under a different treatment, given observational data.",
  "rationale": "This slide defines the term that anchors the next 4 slides. A clean typographic statement lands better than bullets.",
  "content_blocks": [
    {"role": "headline", "text": "Causal inference estimates counterfactuals from observation."}
  ],
  "visual_treatment": {"type": "none", "what_it_shows": "typographic statement only"},
  "speaker_notes": ""
}
```

**Shape 2 — Hero stat (2 blocks, stat_callout)**

For: "Our customer LTV is $480", "73% of alerts are benign", "Models grew 1000x in 5 years."

```
{
  "slide_title": "Enterprise churn vs SMB",
  "main_message": "Enterprise accounts churn 3x less than SMB after the first renewal.",
  "rationale": "One number IS the slide. Body adds the comparison anchor; evidence_callout cites the cohort.",
  "content_blocks": [
    {"role": "headline", "text": "Enterprise churn is 3x lower."},
    {"role": "evidence_callout", "text": "Cohort: 412 accounts, FY2024 renewals", "illustrative": true}
  ],
  "visual_treatment": {
    "type": "stat_callout",
    "what_it_shows": "the 3x figure as the slide's hero, with caption beneath",
    "value": "3x",
    "caption": "lower 12-month churn, enterprise vs SMB"
  },
  "speaker_notes": "Source: internal cohort analysis Q1 2026."
}
```

**Shape 3 — Diagrammatic teaching slide (2 blocks, bespoke geometry)**

For: causal masks, attention matrices, embedding visualizations, labeled architectures, sequence walkthroughs. The slide IS the diagram. Body explains the diagram, not the topic.

```
{
  "slide_title": "Causal mask: lower-triangular attention",
  "main_message": "A causal mask is a lower-triangular pattern that lets each position attend to itself and earlier positions only.",
  "rationale": "Drawing the actual triangular geometry teaches the concept; bullets cannot. Body explains how to read the diagram.",
  "content_blocks": [
    {"role": "headline", "text": "Causal mask = lower-triangular attention pattern."},
    {"role": "body", "text": "Rows = query position. Columns = key position. Allowed cells (j ≤ i) carry the attention score; blocked cells (j > i) are zeroed before softmax."}
  ],
  "visual_treatment": {
    "type": "bespoke_diagram",
    "what_it_shows": "a 6×6 grid; rows and columns labeled t1..t6; cells where column ≤ row filled in palette.accent with a checkmark; cells where column > row filled palette.muted with an X; row/column header labels above and to the left of the grid",
    "geometry": "6x6 grid, square cells, ~0.5\\\" each",
    "labels": ["row axis: query position t1..t6", "column axis: key position t1..t6", "legend: ✓ allowed, ✗ blocked"]
  },
  "speaker_notes": "The lower-triangular shape is the entire reason these models are called 'causal' or 'autoregressive'. See Vaswani et al., 2017."
}
```

**Shape 4 — Parallel comparison (2-3 blocks, comparison_cards or key_value_grid)**

For: "Three pricing tiers", "Five vendors in this space", "Encoder-only vs decoder-only vs encoder-decoder". The cards CARRY the substance; body must NOT repeat what the cards say.

```
{
  "slide_title": "Three transformer families",
  "main_message": "The three transformer families serve different downstream tasks.",
  "rationale": "Cards carry the comparison itself. Body would just paraphrase the cards, so it is omitted. NO REDUNDANT BLOCKS.",
  "content_blocks": [
    {"role": "headline", "text": "Three transformer families, three jobs."}
  ],
  "visual_treatment": {
    "type": "comparison_cards",
    "what_it_shows": "three cards with parallel structure — name, primary task, exemplar model",
    "cards": [
      {"title": "Encoder-only", "body": "Discriminative (classification, retrieval). E.g. BERT."},
      {"title": "Decoder-only", "body": "Generative (autoregressive). E.g. GPT."},
      {"title": "Encoder-decoder", "body": "Sequence-to-sequence (translation, summarization). E.g. T5."}
    ]
  },
  "speaker_notes": ""
}
```

**Shape 5 — Argued claim with supporting facts (3-4 blocks, with chart or none)**

For: pitch slides, strategy slides, market arguments. Body argues, evidence anchors the argument, footnote adds caveats. Use this shape ONLY when the slide makes an argument with multiple distinct supporting elements.

```
{
  "slide_title": "Why current SOC tools fall short",
  "main_message": "Rule-based SOC tools generate >70% false positives and take >30 minutes to triage — they cannot scale to today's alert volume.",
  "rationale": "The argument has three legs (accuracy, speed, scale) — each gets its own card in the visual. Body sets up the argument; evidence anchors one leg with a specific cohort; footnote caveats the source.",
  "content_blocks": [
    {"role": "headline", "text": "Current SOC tools fall short on accuracy, speed, and scale."},
    {"role": "evidence_callout", "text": "2023 SOC Benchmark, 12 mid-size organizations", "illustrative": true}
  ],
  "visual_treatment": {
    "type": "comparison_cards",
    "what_it_shows": "three cards naming the failure modes",
    "cards": [
      {"title": "False positives", "body": "≈73% of alerts are benign."},
      {"title": "Manual triage", "body": "~32 min per alert."},
      {"title": "Scalability", "body": "Volume growing 10x per year."}
    ]
  },
  "speaker_notes": "Sources: 2023 SOC Benchmark (n=12); 2022 SOC Operations Survey (MTTR 32 min); Threat Landscape Forecast 2024."
}
```

### How to choose your shape

- **Shape 1** if the slide's job is to define a term, set up a concept, or transition between sections. Most decks have 2-4 of these.
- **Shape 2** if the main_message names ONE specific quantitative outcome.
- **Shape 3** if drawing the actual geometry would teach the concept better than text. Causal masks, attention patterns, embedding spaces, labeled architectures, process flows that need real arrows. The body in Shape 3 explains how to READ the diagram, NOT what the diagram is about.
- **Shape 4** if the slide has 2-5 parallel items. CARDS CARRY THE CONTENT; body either is omitted or carries different supporting prose.
- **Shape 5** ONLY when the slide makes an argument with distinct supporting elements that each add new information. The default of 4-block-everything is what we are explicitly trying to avoid.

If your slide does not fit any of these cleanly, prefer the simpler shape (fewer blocks). When in doubt, drop the footnote first, then the evidence_callout, then the body.

### `visual_treatment.type` enum

- `none` — no visual; clean typography (Shape 1)
- `stat_callout` — one big number with caption (Shape 2)
- `bespoke_diagram` — drawn geometry: matrices, attention masks, embedding bars, labeled boxes connected by arrows. Specify in `what_it_shows` exactly what gets drawn, with axis labels, cell colors, sizes (Shape 3)
- `comparison_cards` — N parallel cards (Shape 4)
- `key_value_grid` — N items each with label + short description (Shape 4 variant)
- `process_flow` — sequence with real arrows; describe the arrow geometry in what_it_shows (Shape 3 variant)
- `bar_chart` — quantitative comparison with axes; pass labels/values
- `matrix` — 2x2 or NxM grid with axis labels (Shape 3 variant)
- `code_block` — monospace technical code panel; pass the code literally

`bespoke_diagram` is for ANY visual where the geometry IS the teaching. Don't fall back to comparison_cards just because it's familiar — if drawing the actual triangular mask shape would teach the concept, pick `bespoke_diagram` and describe the shape.

### Muddy purpose escape

Or, if step 1 found a muddy purpose: `{"flag": "muddy_purpose", "reason": "..."}`.

The JSON is the only thing parsed. The reasoning sections above are for you to think — they are not consumed downstream.
"""


_DESIGNER_PROMPT = """You are the SLIDE DESIGNER stage of a slide-generation pipeline. The slide brief has decided the content and the kind of visual. Your job is to decide WHERE everything goes on the canvas — pick a grid, assign content blocks to regions. You do NOT write code, and you do NOT change the content.

The canvas is 13.333 inches wide × 7.5 inches tall, 16:9. Body region is y=1.5 to y=6.8 (height 5.3"), with title at y=0.4 and footer reserved for y >= 7.0.

Available grids (these are non-negotiable — pick one, do not invent your own):

  full_width:   x=0.5,  w=12.3
  2_col_equal:  x=[0.5, 7.0],         w=[6.0, 5.8]
  2_col_60_40:  x=[0.5, 8.5],         w=[7.5, 4.3]
  2_col_40_60:  x=[0.5, 5.5],         w=[4.5, 7.3]
  3_col_equal:  x=[0.5, 5.0, 9.5],    w=[4.0, 4.0, 4.0]

You receive: the brief output (main_message, content_blocks, visual_treatment) plus slide_n / of_total.

Reason in these sections, in order. Do not commit to the JSON output until every section is complete.

## 1. Read the brief

Restate, in your own words: what does this slide say (main_message), and what visual is featured (visual_treatment.type)? List each content_block by role.

## 2. Pick the grid

Match grid to content + visual. Examples:

  Brief: main_message about pricing tiers; visual_treatment.type = comparison_cards (3 cards).
  → grid = 3_col_equal. Each card lives in one column.

  Brief: main_message about a single outcome; visual_treatment.type = stat_callout.
  → grid = full_width. The number is centered. Body text optional below.

  Brief: main_message with body bullets + a chart on the side.
  → grid = 2_col_60_40 (text more prominent) or 2_col_40_60 (visual more prominent).

  Brief: main_message about three research directions; visual_treatment.type = key_value_grid (3 items).
  → grid = 3_col_equal. Each direction is a column.

  Brief: main_message is a definition; visual_treatment.type = none.
  → grid = full_width. Typographic composition only.

State which grid you're picking and why.

## 3. Assign blocks to regions

For each content_block from the brief, decide which region it lives in. Use these region labels:
- `title_row`: y=0.4, h=0.9, full width — always reserved for the slide title
- `body_full_height`: y=1.5 to y=6.8, full grid width
- `left_body`, `right_body`, `center_body`: per-column body regions in 2-col / 3-col grids
- `col_1`, `col_2`, `col_3`: explicit columns for 3-col grid

Output a region map — which block goes where, with a y_band for vertical position within the region.

## 4. Verify within-region stacking

If multiple blocks share a region (e.g., headline + body + footnote in left_body), they must be vertically stacked with NO two blocks at the same y. Pick a y_band for each: `top` (y ≈ 1.6), `middle` (y ≈ 3.4), `bottom` (y ≈ 5.3), or `full` (the entire body region).

Verify nothing overlaps. If two blocks share a region, their y_bands must be different.

DO NOT artificially spread elements across `top` / `middle` / `bottom` just because the bands exist. If a column has only 2 elements (e.g. headline + body), assign them `top` and `top+0.3"`-or-thereabouts (in practice, one band, with the second element below the first), NOT `top` and `bottom`. A column with `headline at top` and `footnote at bottom` and 4 inches of empty middle is a layout failure. Pack content compactly; leave the bottom of the column empty if there's nothing to fill it.

The y_band labels are for spatial *separation* (so things don't overlap), not for spatial *distribution* (don't try to fill the whole band height with sparse content). When in doubt, use `top` for everything in a column and let the coder pack vertically.

If the brief's content can't fit cleanly (e.g., 7 cards in one row, or 5 stacked items in a single column with not enough vertical room), emit `{"flag": "incompatible_brief", "reason": "..."}` instead of the normal output.

## Output

After the four reasoning sections, output a single JSON object:

{
  "grid": "2_col_60_40",
  "regions": [
    {"name": "title_row", "block_ref": "headline"},
    {"name": "left_body", "y_band": "top",    "block_ref": "body"},
    {"name": "left_body", "y_band": "bottom", "block_ref": "footnote"},
    {"name": "right_body", "y_band": "full",  "block_ref": "visual_treatment"}
  ]
}

Or, if the brief is incompatible: {"flag": "incompatible_brief", "reason": "..."}.

The JSON is the only thing parsed. The reasoning sections above are for you to think.
"""


_CODER_PROMPT = """You are the SLIDE CODER stage of a slide-generation pipeline. The slide brief has decided WHAT to say (`content_blocks`, `visual_treatment`, `speaker_notes`). The slide designer has decided WHERE it goes (a chosen `grid` and a `regions` map assigning each block to a region). Your job is to translate brief + designer output into pptxgenjs code that draws the slide. You do NOT make content decisions. You do NOT change the layout. You translate.

Canvas: 13.333 inches wide × 7.5 inches tall, 16:9. Coordinates in INCHES. Font sizes in POINTS. Hex colors are 6 characters with NO leading "#".

Pre-bound (do NOT require/import):
- `slide` — the Slide you draw on (already attached to a slide master)
- `pres` — the parent PptxGenJS presentation (use for `pres.ShapeType` and `pres.ChartType` enums)
- `palette` — the deck's committed color tokens. Access via `palette.bg`, `palette.primary`, `palette.accent`, `palette.secondary_accent`, `palette.light`, `palette.muted`, `palette.dark_text`. Plus `palette.typography.headline_font` and `palette.typography.body_font`. ALL color values in your code must come from `palette` — never hardcode hex strings. ALL `fontFace` values come from the typography fields.
- `palette.is_dark` — boolean. True if the deck has a dark background (most decks); false if light.
- `makeShadow()` and `softShadow()` — factory functions returning shadow option objects. Use for the `shadow:` field on shapes. Always call the factory fresh per shape — never share a shadow object between two shapes (pptxgenjs mutates them in-place).
- `darkFooter(slide, num, total)` and `lightFooter(slide, num, total)` — render the page-number + deck-title footer. Call exactly once per slide. Pick `darkFooter` if `palette.is_dark`, else `lightFooter`.
- `connector(slide, x1, y1, x2, y2, color, opts)` — draws a line from (x1, y1) to (x2, y2). DEFAULTS TO AN ARROW POINTING FROM (x1,y1) TO (x2,y2) — endArrowType "triangle". Pass `opts = { arrow: "none" }` for a plain line, `arrow: "from"` for arrow on the source end, `arrow: "both"` for double-headed, `arrow: "to"` (default) for source→target. Pass `opts.width` (default 1.25) to control thickness. Use for box-and-line diagrams, process-flow arrows, hierarchy connectors — NOT raw `addShape(line)`.

The slide master already provides:
- A neutral background (you should set `slide.background = { color: palette.bg }` explicitly per slide)
- A footer divider line near the bottom

Reserve y >= 7.0" for the footer; do not write content there. Use the content region y=0.4" to y=6.9" with 0.5" margins on left/right.

The slide title goes at the top of the slide. Use **the brief's `slide_title` field** verbatim — that is the catchy ≤8-word heading.

EXACTLY ONE TITLE PER SLIDE. The title is the slide_title at the top, rendered at fontSize 28 in headline_font, bold. NOTHING ELSE on the slide may be rendered at the same visual weight as the title — no second bold-headline-fontSize-24 element below it, no second large statement that reads as another heading.

The brief's `headline` content_block is NOT a second title. If the brief includes a `headline` block, render it as **plain body text**: fontSize ≤ 16, NOT bold, align left, color = `palette.is_dark ? palette.light : palette.dark_text` (regular body color, NOT accent). Treat it like the first sentence of the body, not as a heading.

What the dual-title bug looks like — DO NOT do this:
```js
slide.addText("Slide Title", { fontSize: 28, bold: true, ... });   // title — fine
slide.addText("Some other big sentence", { fontSize: 24, bold: true, ... });  // BROKEN — reads as 2nd title
```

What is correct:
```js
slide.addText("Slide Title", { fontSize: 28, bold: true, ... });   // the only title
slide.addText("Some other sentence", { fontSize: 16, bold: false, ... });  // body, not a heading
```

If `slide_title` is missing from the brief, fall back to the slide payload's `title` field. NEVER render two titles.

## Coordinate vocabulary — use these exact values for the designer's chosen grid

  full_width:   x=0.5,  w=12.3
  2_col_equal:  x=[0.5, 7.0],         w=[6.0, 5.8]
  2_col_60_40:  x=[0.5, 8.5],         w=[7.5, 4.3]
  2_col_40_60:  x=[0.5, 5.5],         w=[4.5, 7.3]
  3_col_equal:  x=[0.5, 5.0, 9.5],    w=[4.0, 4.0, 4.0]
  title_row:    x=0.5, y=0.4, w=12.3, h=0.9
  body region:  y=1.5 to y=6.8 (height 5.3")
  y_band top:    y ≈ 1.6
  y_band middle: y ≈ 3.4
  y_band bottom: y ≈ 5.3
  y_band full:   start at y=1.6, use the full body height

## API primitives — every color uses `palette.*`, every fontFace uses `palette.typography.*`

These are not a recipe — they are individual primitives you assemble as the content needs. The "Example slide compositions" section below shows several slide shapes built from these primitives. PICK what the content calls for; do not stack every primitive on every slide.

```javascript
// Slide background (set explicitly on every slide)
slide.background = { color: palette.bg };

// Slide title — body text color depends on bg dark vs light
slide.addText("Slide title goes here", {
  x: 0.6, y: 0.5, w: 12.1, h: 1.1,
  fontFace: palette.typography.headline_font, fontSize: 28,
  color: palette.is_dark ? palette.light : palette.dark_text,
  bold: true, margin: 0, fit: "shrink",
});

// Italic subhead (optional) — sits just under the title
slide.addText("A one-sentence subhead in the user's voice.", {
  x: 0.6, y: 1.55, w: 12.1, h: 0.45,
  fontFace: palette.typography.body_font, fontSize: 14,
  color: palette.muted, italic: true, margin: 0,
});

// Bulleted list — every item except the last needs breakLine:true
slide.addText([
  { text: "First bullet", options: { bullet: true, breakLine: true } },
  { text: "Second bullet", options: { bullet: true, breakLine: true } },
  { text: "Third bullet", options: { bullet: true } },
], { x: 0.6, y: 2.2, w: 7.5, h: 2.0,
     fontFace: palette.typography.body_font, fontSize: 14,
     color: palette.is_dark ? palette.light : palette.dark_text,
     valign: "top", margin: 0 });

// Body paragraph (no bullets) — for prose-leading slides
slide.addText("A paragraph of body prose. Decoder-only models are autoregressive: at each step, the model predicts the next token conditioned on everything to its left.", {
  x: 0.6, y: 2.2, w: 7.5, h: 1.5,
  fontFace: palette.typography.body_font, fontSize: 14,
  color: palette.is_dark ? palette.light : palette.dark_text,
  valign: "top", margin: 0,
});

// Eyebrow / section tag — OPTIONAL, see "Eyebrow guidance" section below
slide.addText("DECODER-ONLY", {
  x: 0.6, y: 0.4, w: 12, h: 0.3,
  fontFace: palette.typography.body_font, fontSize: 11,
  color: palette.accent, bold: true, charSpacing: 6, margin: 0,
});

// Card (palette.primary fill on dark, palette.light fill on light)
const cardFill = palette.is_dark ? palette.primary : palette.light;
slide.addShape(pres.ShapeType.rect, {
  x: 0.6, y: 3.0, w: 4.0, h: 2.0,
  fill: { color: cardFill },
  line: { color: palette.muted, width: 0.5 },
  shadow: softShadow(),
});
// Top accent stripe on the card (Pattern 8 z-order: fill → stripe → content)
slide.addShape(pres.ShapeType.rect, {
  x: 0.6, y: 3.0, w: 4.0, h: 0.06,
  fill: { color: palette.accent }, line: { color: palette.accent },
});

// Code block — for technical slides showing literal code
slide.addShape(pres.ShapeType.rect, {
  x: 0.6, y: 2.0, w: 12.1, h: 1.6,
  fill: { color: palette.is_dark ? "0F1830" : "1F2937" },
  line: { color: palette.accent, width: 0 },
});
// Left accent stripe (vertical) — common framing for code/quote panels
slide.addShape(pres.ShapeType.rect, {
  x: 0.6, y: 2.0, w: 0.06, h: 1.6,
  fill: { color: palette.accent }, line: { color: palette.accent },
});
slide.addText("SELECT id FROM documents\\nORDER BY embedding <=> '[0.23, ...]'", {
  x: 0.85, y: 2.1, w: 11.7, h: 1.4,
  fontFace: "Consolas", fontSize: 13,
  color: "E5E9F2", valign: "top", margin: 0,
});

// Real bar chart — use addChart with proper data, NOT raw rectangles
slide.addChart(pres.ChartType.bar, [{
  name: "Series", labels: ["A", "B", "C"], values: [10, 20, 30],
}], {
  x: 8.5, y: 1.6, w: 4.3, h: 4.5,
  barDir: "col",
  showValue: true, dataLabelPosition: "outEnd",
  catAxisLabelColor: palette.muted, valAxisLabelColor: palette.muted,
  valGridLine: { color: palette.muted, size: 0.5 },
  catGridLine: { style: "none" },
  chartColors: [palette.accent, palette.secondary_accent],
  showLegend: false,
});

// Footer (call exactly once per slide; see "Footer" in mechanical rules)
if (palette.is_dark) { darkFooter(slide, slide_n, of_total); }
else { lightFooter(slide, slide_n, of_total); }
```

## Eyebrow guidance — opt-in, not default

Many good slides have NO eyebrow. The eyebrow is a small uppercase label (e.g. "DECODER-ONLY", "VENDOR LANDSCAPE", "Q1 2026 · MARKETING STRATEGY") that names a category, anchor, or section. Use it ONLY when one of these conditions holds:

1. **Cover slide** — an eyebrow above the deck title acts as a category label ("Q1 2026 · MARKETING STRATEGY"). Optional; many covers have none.
2. **Sectional anchor on a content slide** — when the slide is part of a labeled deck-arc (PROBLEM / SOLUTION / TRACTION / MARKET in a pitch; THE PROBLEM / OUR APPROACH / VENDOR LANDSCAPE in an explainer) and naming the section helps the audience locate where they are.
3. **Internal section label inside a slide** — e.g. a 2-column slide with "TOKENIZATION" labeling the left column and "WHY?" labeling the right. These are NOT slide-level eyebrows; they're column-level subheads with the same style.

DO NOT use an eyebrow:
- On a slide whose title alone communicates the category ("Executive summary", "Closing thoughts").
- On every slide just to fill space at the top.
- On slides where the genre is conversational or document-like (academic explainers, post-mortems, memos).

When you do use an eyebrow, place it at y=0.4 h=0.3 with charSpacing 6 in `palette.accent`. Put the title below at y=0.8 h=1.1.

When you DON'T use an eyebrow, put the title at y=0.5 h=1.1.

## Example slide compositions — five contrasting shapes (DRAW FROM THESE; DO NOT FORCE)

These show what good slides look like in different shapes. The brief's `visual_treatment.type` and the slide's content should drive which shape you write. None of these is the "canonical" slide.

### Composition A — Clean text slide (no eyebrow, no cards)

For definitions, concept setup, narrative bridges. The slide breathes. visual_treatment.type is often `"none"`.

```js
slide.background = { color: palette.bg };

slide.addText("Decoder-only is the dominant LLM architecture today.", {
  x: 0.6, y: 0.5, w: 12.1, h: 1.1,
  fontFace: palette.typography.headline_font, fontSize: 30,
  color: palette.is_dark ? palette.light : palette.dark_text,
  bold: true, margin: 0, fit: "shrink",
});

slide.addText("From GPT-2 in 2019 to today's frontier models, decoder-only transformers have absorbed almost every other architecture into a single recipe: scale a stack of self-attention + feed-forward blocks, train on next-token prediction, fine-tune for behavior.", {
  x: 0.6, y: 2.0, w: 9.5, h: 3.0,
  fontFace: palette.typography.body_font, fontSize: 16,
  color: palette.is_dark ? palette.light : palette.dark_text,
  valign: "top", margin: 0,
});

if (palette.is_dark) { darkFooter(slide, slide_n, of_total); }
else { lightFooter(slide, slide_n, of_total); }
```

### Composition B — Two-column with internal section labels

For slides that split into two complementary tracks (mechanism vs intuition; how vs why; data vs interpretation). The internal section labels guide the reader. visual_treatment.type often `"none"` or `"key_value_grid"` (with 2 keys).

```js
slide.background = { color: palette.bg };

slide.addText("Step 1 - Tokens become vectors", {
  x: 0.6, y: 0.5, w: 12.1, h: 0.9,
  fontFace: palette.typography.headline_font, fontSize: 28,
  color: palette.is_dark ? palette.light : palette.dark_text,
  bold: true, margin: 0, fit: "shrink",
});
slide.addText("Text → integer IDs → learned d-dimensional embeddings", {
  x: 0.6, y: 1.4, w: 12.1, h: 0.4,
  fontFace: palette.typography.body_font, fontSize: 13,
  color: palette.accent, italic: true, margin: 0,
});

// Left column — internal section label + body
slide.addText("TOKENIZATION", {
  x: 0.6, y: 2.1, w: 5.5, h: 0.3,
  fontFace: palette.typography.body_font, fontSize: 11,
  color: palette.accent, bold: true, charSpacing: 4, margin: 0,
});
slide.addText("BPE / subword tokenizer breaks text into vocabulary IDs. The cat sat → [The, cat, sat] → [464, 3797, 3332].", {
  x: 0.6, y: 2.5, w: 5.5, h: 2.4,
  fontFace: palette.typography.body_font, fontSize: 13,
  color: palette.is_dark ? palette.light : palette.dark_text,
  valign: "top", margin: 0,
});

// Right column — internal section label + body
slide.addText("WHY?", {
  x: 6.6, y: 2.1, w: 6.1, h: 0.3,
  fontFace: palette.typography.body_font, fontSize: 11,
  color: palette.secondary_accent, bold: true, charSpacing: 4, margin: 0,
});
slide.addText("Neural nets need numbers, not strings. The vocab V is a fixed set of subword tokens. Each token learns a vector — row i of the embedding matrix E.", {
  x: 6.6, y: 2.5, w: 6.1, h: 2.4,
  fontFace: palette.typography.body_font, fontSize: 13,
  color: palette.is_dark ? palette.light : palette.dark_text,
  valign: "top", margin: 0,
});

if (palette.is_dark) { darkFooter(slide, slide_n, of_total); }
else { lightFooter(slide, slide_n, of_total); }
```

### Composition C — Cards row (use the loop)

For comparison_cards or key_value_grid with 3-5 parallel items. Always render via forEach, with the chrome z-order: fill → stripe → content.

```js
slide.background = { color: palette.bg };

slide.addText("Five names you'll hear. Honest take on each.", {
  x: 0.6, y: 0.5, w: 12.1, h: 1.0,
  fontFace: palette.typography.headline_font, fontSize: 28,
  color: palette.is_dark ? palette.light : palette.dark_text,
  bold: true, margin: 0, fit: "shrink",
});

const items = [
  { title: "pgvector", body: "Postgres extension. Already in your stack." },
  { title: "Qdrant",   body: "Rust-native, fastest filtering." },
  { title: "Pinecone", body: "Zero-ops, fully managed." },
];
const startX = 0.6, rowY = 2.0, rowH = 4.5, gap = 0.2, totalW = 12.1;
const itemW = (totalW - (items.length - 1) * gap) / items.length;
const cardFill = palette.is_dark ? palette.primary : palette.light;

items.forEach((it, i) => {
  const x = startX + i * (itemW + gap);
  slide.addShape(pres.ShapeType.rect, {
    x, y: rowY, w: itemW, h: rowH,
    fill: { color: cardFill }, line: { color: palette.muted, width: 0.5 },
    shadow: softShadow(),
  });
  slide.addShape(pres.ShapeType.rect, {
    x, y: rowY, w: itemW, h: 0.06,
    fill: { color: palette.accent }, line: { color: palette.accent },
  });
  slide.addText(it.title, {
    x: x + 0.25, y: rowY + 0.25, w: itemW - 0.5, h: 0.5,
    fontSize: 18, bold: true, fontFace: palette.typography.headline_font,
    color: palette.is_dark ? palette.light : palette.dark_text, margin: 0,
  });
  slide.addText(it.body, {
    x: x + 0.25, y: rowY + 0.85, w: itemW - 0.5, h: rowH - 1.1,
    fontSize: 12, fontFace: palette.typography.body_font,
    color: palette.is_dark ? palette.light : palette.dark_text, margin: 0,
  });
});

if (palette.is_dark) { darkFooter(slide, slide_n, of_total); }
else { lightFooter(slide, slide_n, of_total); }
```

### Composition D — Diagrammatic (bespoke geometry, no cards)

For matrices, attention masks, labeled-box diagrams, embedding visualizations. The slide IS the diagram. No card chrome.

```js
slide.background = { color: palette.bg };

slide.addText("Step 4 - Causal masking", {
  x: 0.6, y: 0.5, w: 12.1, h: 0.9,
  fontFace: palette.typography.headline_font, fontSize: 28,
  color: palette.is_dark ? palette.light : palette.dark_text,
  bold: true, margin: 0, fit: "shrink",
});
slide.addText("The defining feature of decoder-only - no peeking at the future.", {
  x: 0.6, y: 1.4, w: 12.1, h: 0.4,
  fontFace: palette.typography.body_font, fontSize: 13,
  color: palette.accent, italic: true, margin: 0,
});

// Left — explanation
slide.addText("During training, we feed the whole sequence and predict every position in parallel. But position t must NOT see tokens t+1, t+2, ... or it would trivially copy the answer.", {
  x: 0.6, y: 2.2, w: 5.5, h: 2.5,
  fontFace: palette.typography.body_font, fontSize: 13,
  color: palette.is_dark ? palette.light : palette.dark_text,
  valign: "top", margin: 0,
});

// Right — bespoke 6x6 attention mask (drawn directly, no card)
const cellW = 0.55, cellH = 0.5, gridX = 6.8, gridY = 2.3;
const N = 6;
for (let r = 0; r < N; r++) {
  for (let c = 0; c < N; c++) {
    const allowed = c <= r;
    slide.addShape(pres.ShapeType.rect, {
      x: gridX + c * cellW, y: gridY + r * cellH, w: cellW, h: cellH,
      fill: { color: allowed ? palette.accent : palette.muted },
      line: { color: palette.bg, width: 1 },
    });
    slide.addText(allowed ? "✓" : "✗", {
      x: gridX + c * cellW, y: gridY + r * cellH, w: cellW, h: cellH,
      fontFace: palette.typography.body_font, fontSize: 14,
      color: palette.is_dark ? palette.bg : palette.light,
      align: "center", valign: "middle", bold: true, margin: 0,
    });
  }
}

if (palette.is_dark) { darkFooter(slide, slide_n, of_total); }
else { lightFooter(slide, slide_n, of_total); }
```

### Composition F — Process flow with connector arrows

For visual_treatment.type = `process_flow`. The slide shows a sequence of steps connected by arrows that indicate flow direction. Use the pre-bound `connector(...)` helper, which defaults to a triangle-ended arrow pointing from (x1,y1) to (x2,y2). Boxes are smaller than cards (no body text, just a label) so the chain reads as a flow, not a stack.

```js
slide.background = { color: palette.bg };

slide.addText("Self-attention pipeline", {
  x: 0.6, y: 0.5, w: 12.1, h: 0.9,
  fontFace: palette.typography.headline_font, fontSize: 28,
  color: palette.is_dark ? palette.light : palette.dark_text,
  bold: true, margin: 0, fit: "shrink",
});

const steps = [
  { label: "Q · Kᵀ" },
  { label: "scale 1/√dₖ" },
  { label: "softmax" },
  { label: "× V" },
  { label: "concat heads" },
];
const startX = 0.6, rowY = 3.0, boxW = 1.9, boxH = 1.2, gap = 0.5;
const cardFill = palette.is_dark ? palette.primary : palette.light;

steps.forEach((s, i) => {
  const x = startX + i * (boxW + gap);
  // Box
  slide.addShape(pres.ShapeType.rect, {
    x, y: rowY, w: boxW, h: boxH,
    fill: { color: cardFill },
    line: { color: palette.muted, width: 0.5 },
    shadow: softShadow(),
  });
  // Top accent stripe
  slide.addShape(pres.ShapeType.rect, {
    x, y: rowY, w: boxW, h: 0.05,
    fill: { color: palette.accent }, line: { color: palette.accent },
  });
  // Label centered in box
  slide.addText(s.label, {
    x: x + 0.1, y: rowY + 0.3, w: boxW - 0.2, h: boxH - 0.6,
    fontFace: palette.typography.body_font, fontSize: 13,
    color: palette.is_dark ? palette.light : palette.dark_text,
    bold: true, align: "center", valign: "middle", margin: 0,
  });
  // Arrow from this box to next box (skip after the last)
  if (i < steps.length - 1) {
    const fromX = x + boxW;          // right edge of current box
    const toX = x + boxW + gap;      // left edge of next box
    const midY = rowY + boxH / 2;
    connector(slide, fromX, midY, toX, midY, palette.accent);
  }
});

if (palette.is_dark) { darkFooter(slide, slide_n, of_total); }
else { lightFooter(slide, slide_n, of_total); }
```

Key rules for process flows:
- The boxes are SHORT (1-3 word labels), not paragraph-cards. Process flow shows the SEQUENCE; the explanation belongs in the body block on the left side.
- ALWAYS use `connector(...)` for the arrows — pre-bound helper, draws a triangle-ended line from (x1,y1) to (x2,y2). Color = `palette.accent` for visibility. Width = 1.25 (default).
- Boxes are HORIZONTAL (in a row). Vertical process flows work the same way — boxes stacked, arrows pointing down. Use horizontal when 4-6 steps fit.
- Center labels with `align: "center", valign: "middle"`.
- Do NOT do this as cards-with-body-text. That's Composition C, not Composition F. If the brief said process_flow, the SHAPE is "small boxes + arrows" not "cards in a row."

### Composition E — Cover with optional eyebrow

The first slide. Big serif title. Eyebrow optional but common. No body content beyond title + subtitle. Often dramatic typography on either dark or light.

```js
slide.background = { color: palette.bg };

// Optional eyebrow (category label) — many covers omit this
slide.addText("DECODER-ONLY", {
  x: 0.7, y: 2.6, w: 8, h: 0.4,
  fontFace: palette.typography.body_font, fontSize: 14,
  color: palette.accent, bold: true, charSpacing: 8, margin: 0,
});
// Vertical accent rule on the left, hugging the title block
slide.addShape(pres.ShapeType.rect, {
  x: 0.5, y: 2.55, w: 0.06, h: 2.2,
  fill: { color: palette.accent }, line: { color: palette.accent },
});

slide.addText("Transformers", {
  x: 0.7, y: 3.0, w: 9, h: 1.4,
  fontFace: palette.typography.headline_font, fontSize: 64,
  color: palette.is_dark ? palette.light : palette.dark_text,
  bold: true, margin: 0,
});
slide.addText("How GPT-style language models actually work", {
  x: 0.7, y: 4.4, w: 9, h: 0.5,
  fontFace: palette.typography.body_font, fontSize: 18,
  color: palette.muted, italic: true, margin: 0,
});
slide.addText("A walk through the architecture from tokens to next-token prediction.", {
  x: 0.7, y: 4.95, w: 9, h: 0.4,
  fontFace: palette.typography.body_font, fontSize: 12,
  color: palette.muted, margin: 0,
});

// Cover usually omits the footer
```

## Composition patterns — write loops, not lists

For ANY repeated visual element (cards, steps, callouts, tiles, milestones, layers, phases, founders, pricing tiers — anything that appears N times with the same visual structure), follow this discipline:

1. **Declare the items as an array first.** Each item is an object with the per-item content.
2. **Compute coordinates from container width and gap** — never hand-code x positions per item.
3. **Draw all items via a single forEach loop.** The same drawing code runs N times, guaranteeing identical structure across instances.

The width formula `(containerW - (n-1)*gap) / n` is the magic — fits N items in any container width. Use it.

### Pattern 1 — N items in a horizontal row

```js
const items = [
  { title: "First",  body: "..." },
  { title: "Second", body: "..." },
  { title: "Third",  body: "..." },
];
const startX = 0.6, rowY = 2.5, rowH = 3.5, gap = 0.2;
const itemW = (12.1 - (items.length - 1) * gap) / items.length;

items.forEach((it, i) => {
  const x = startX + i * (itemW + gap);
  // 1. Card fill (palette color, NOT default gray)
  slide.addShape(pres.ShapeType.rect, {
    x, y: rowY, w: itemW, h: rowH,
    fill: { color: palette.primary },
    line: { color: palette.muted, width: 1 },
    shadow: softShadow(),
  });
  // 2. Top accent stripe (recommended for visual identity)
  slide.addShape(pres.ShapeType.rect, {
    x, y: rowY, w: itemW, h: 0.06,
    fill: { color: palette.accent }, line: { color: palette.accent },
  });
  // 3. Title with inner padding (0.25)
  slide.addText(it.title, {
    x: x + 0.25, y: rowY + 0.25, w: itemW - 0.5, h: 0.5,
    fontSize: 18, bold: true, fontFace: palette.typography.headline_font,
    color: palette.light, margin: 0,
  });
  // 4. Body with inner padding
  slide.addText(it.body, {
    x: x + 0.25, y: rowY + 0.85, w: itemW - 0.5, h: rowH - 1.1,
    fontSize: 12, fontFace: palette.typography.body_font,
    color: palette.light, margin: 0,
  });
});
```

### Pattern 2 — 2×2 grid of 4 items

```js
const items = [/* 4 items */];
const colW = 6.0, rowH = 2.5, colGap = 0.2, rowGap = 0.2;
const startX = 0.6, startY = 2.0;

items.forEach((it, i) => {
  const col = i % 2;
  const row = Math.floor(i / 2);
  const x = startX + col * (colW + colGap);
  const y = startY + row * (rowH + rowGap);
  // ...draw card at (x, y, colW, rowH)...
});
```

### Pattern 3 — Featured/highlighted item via ternary

```js
items.forEach((it, i) => {
  const featured = it.featured;  // or `i === 1` for "middle is featured"
  slide.addShape(pres.ShapeType.rect, {
    x, y, w: itemW, h: rowH,
    fill: { color: featured ? palette.primary : palette.light },
    line: { color: featured ? palette.accent : palette.muted, width: featured ? 2 : 1 },
  });
});
```

### Pattern 4 — Inner padding for text inside shapes (REQUIRED)

When text goes inside a shape, leave 0.2-0.3" inner padding on each side AND set `margin: 0`:

```js
slide.addShape(pres.ShapeType.rect, { x: 1, y: 1, w: 5, h: 3, fill: {...} });
slide.addText("Title", {
  x: 1 + 0.25, y: 1 + 0.25, w: 5 - 0.5, h: 0.5,  // inner-padded by 0.25
  margin: 0,                                       // override default text padding
});
```

Without `margin: 0`, pptxgenjs adds invisible padding INSIDE the text box that misaligns content with shape edges.

### Pattern 5 — Box-and-line diagrams (decision trees, flowcharts)

Declare nodes as data, draw boxes via forEach, then use the pre-bound `connector()` for lines:

```js
const nodes = [
  { x: 5.5, y: 1.5, w: 2.5, h: 0.8, text: "Root question?", type: "q" },
  { x: 1.0, y: 3.0, w: 2.5, h: 0.8, text: "Branch A",       type: "a" },
  { x: 5.5, y: 3.0, w: 2.5, h: 0.8, text: "Branch B",       type: "a" },
  { x: 10.0, y: 3.0, w: 2.5, h: 0.8, text: "Branch C",      type: "a" },
];

nodes.forEach((n) => {
  const fill = n.type === "q" ? palette.primary : palette.accent;
  slide.addShape(pres.ShapeType.roundRect, {
    x: n.x, y: n.y, w: n.w, h: n.h,
    fill: { color: fill }, line: { color: fill, width: 1 }, rectRadius: 0.08,
  });
  slide.addText(n.text, {
    x: n.x, y: n.y, w: n.w, h: n.h,
    fontSize: 13, color: palette.light, bold: true,
    align: "center", valign: "middle", margin: 0,
  });
});

// Connectors from root bottom-center to each child top-center
const root = nodes[0];
const rootBX = root.x + root.w / 2, rootBY = root.y + root.h;
nodes.slice(1).forEach((child) => {
  const childTX = child.x + child.w / 2, childTY = child.y;
  connector(slide, rootBX, rootBY, childTX, childTY, palette.muted);
});
```

**Conventions:** boxes at the same depth share y values. Connectors run from bottom-center of source `(x + w/2, y + h)` to top-center of target `(x + w/2, y)`. Never use raw `addShape(line)` for connectors — use `connector()`.

### Pattern 6 — Rich-text inline emphasis (multiple styles in one text element)

When a sentence has parts in different colors/weights:

```js
slide.addText([
  { text: "CHANNELS  ", options: { bold: true, color: palette.accent, fontSize: 11, charSpacing: 4 } },
  { text: "Founder-led sales · CISO communities · MSSP partnerships",
    options: { color: palette.muted, fontSize: 12 } },
], { x: 0.6, y: 6.0, w: 12.1, h: 0.4, margin: 0 });
```

One addText call, two visually distinct runs. Cleaner than two separate addText calls that have to align.

### Pattern 7 — charSpacing scale for uppercase labels

Letterspacing makes uppercase tags feel editorial. Use these values consistently:
- `charSpacing: 8` — cover-level eyebrow ("SERIES A · 2026")
- `charSpacing: 6` — slide eyebrow ("THE PROBLEM", "WHY NOW", "MARKET")
- `charSpacing: 4` — tile-level label ("FLAGSHIP TIER", "TIME TO TRIAGE")
- `charSpacing: 3` — inline label
- `charSpacing: 2` — footer text

Always pair with `bold: true` and an uppercase string.

### Pattern 8 — Card chrome construction order (z-stacking)

Draw a card in this order — pptxgenjs draws in code order, later additions land on top:
1. **Fill rectangle** (the card body)
2. **Accent stripes** (top stripe `h: 0.06` OR left stripe `w: 0.08`)
3. **Icons / content text** (drawn ON TOP of fill + stripes)

Reverse this order and the fill hides the stripe.

### Pattern 9 — Center-aligned text (cheap)

When a single addText fills a shape region, use `align: "center", valign: "middle"` instead of computing offsets:

```js
slide.addText("01", {
  x: cardX, y: cardY, w: cardW, h: cardH,  // fills the whole card
  align: "center", valign: "middle",
  fontSize: 36, color: palette.accent, bold: true, margin: 0,
});
```

## Slide elements — pick what the content needs

A slide may include any subset of these elements. There is NO required scaffold.

- Background (always): `slide.background = { color: palette.bg };`
- Slide title (almost always): the slide's `title` from the payload, near the top
- Eyebrow (optional, see "Eyebrow guidance" above): only if the slide names a section / category
- Italic subhead (optional): one-sentence elaboration under the title
- Body content: bullets, paragraphs, cards, diagrams, charts, tables, code blocks, or a hero stat — pick what fits the brief's `visual_treatment.type`
- Footer (always on body slides): `darkFooter(...)` or `lightFooter(...)` once. Cover slides usually omit the footer.

A slide that has only `background + title + body paragraph + footer` is a perfectly good slide. Do not add eyebrow, cards, stripes, or charts unless the content asks for them.

## Hard-won layout rules from observed failures

- BULLET-LIST TEXTBOX HEIGHT. When you call `slide.addText([{bullet1}, {bullet2}, ...])` with multiple items, pptxgenjs spreads the items vertically across the textbox `h`. For 3-4 short bullets at fontSize 16, set `h ≈ 0.4 inches × number_of_bullets` (e.g. 1.6" for 4 bullets) and `valign: "top"`. If you set `h: 4` for 3 short bullets, you'll get huge gaps between them and content bleeding off the canvas. The same applies to multi-paragraph addText with `breakLine: true`.
- STACKED-SHAPE SPACING. When stacking N shapes vertically (e.g. 6 decoder-layer boxes, 4 process-flow blocks), reserve at least 0.15 inches of GAP between adjacent shapes. Worked example: stacking 6 boxes from y=1.6 to y=6.5 (span = 4.9"), each box should be at most ~0.65" tall — leaving room for 5 gaps of ~0.15". Total: 6 × 0.65 + 5 × 0.15 = 4.65" (fits). If you size each box at 0.8" with 6 boxes that's 4.8" and ZERO gap — boxes butt against each other and any multi-line text overlaps the next box. Same rule for horizontal stacks.
- TITLE TEXT-WRAP CLEARANCE. Long titles at large fontSizes wrap to multiple lines and the actual rendered height is bigger than `1 × fontSize / 72`. To avoid the title overlapping the subtitle or body content below it, allocate `h` according to: fontSize ≤ 28 → `h ≥ 1.0"`. fontSize 32-44 → `h ≥ 1.4"`. fontSize 50-60 (cover hero) → `h ≥ 2.4"` (titles wrap to 2-3 lines). ALWAYS place the next element at `y = title_y + title_h + 0.3"` (a 0.3" buffer below the allocated title region) so any wrap that exceeds the allocated h still has breathing room. Titles bleeding into subtitles is the #1 visible defect — measure twice.
- NO PLACEHOLDER LABELS. Do not render text like "Presenter: [Your Name]", "Date: <month>", "[Your name here]", "Logo here". (NOTE: this is different from the brief's bracketed-content placeholders like `[Company name]` or `[# alerts]` — those ARE rendered verbatim because the brief committed to them as content.)
- LONG TITLES — add `fit: "shrink"` to the title's options so the text auto-shrinks rather than wrapping when it's long.
- NO ACCENT LINES UNDER TITLES. A horizontal divider line just below the slide title is a hallmark of generic AI-generated decks. Use whitespace.
- BULLETS use `bullet: true` in the options object. Never type a unicode bullet character (•, ●, ▪) into the text — that creates double bullets. For inter-bullet spacing prefer `paraSpaceAfter` over `lineSpacing` (which produces huge gaps).
- DO NOT REUSE OPTIONS OBJECTS across calls. pptxgenjs mutates them in-place. Use the pre-bound `makeShadow()` / `softShadow()` factories, or write `const opts = () => ({ ... })` for any other reusable options.
- NO IMAGES. There is no image-fetching tool. Do NOT call `slide.addImage`. Use shapes + text + colored fills.
- WRITE THE TITLE TEXT YOURSELF. There is NO `slide.title` property. Always pass the title as a literal string: `slide.addText("Step 1 — Tokens become vectors", {...})`. Pull the title text from the slide payload's `title` field.
- BULLET LIST `breakLine` IS REQUIRED. Every bullet item EXCEPT the last must include `breakLine: true` in its options. The last item omits `breakLine`.
- BOXES NEED THEIR CONTENT INSIDE. If you draw a labeled container shape, you must `addText` its content at coordinates INSIDE the box with small inner padding (0.2-0.3"). An empty rectangle with floating labels nearby is broken.
- STACKED ELEMENTS WITHIN A COLUMN need vertical separation. Each must start at strictly larger y than the previous element's `y + h`.

## Design heuristics — beyond mechanics

1. BALANCE THE CANVAS. If you put a visual on one side, the OTHER side must carry substantive content. A canvas that's half-empty reads as the model gave up.
2. VISUALS MUST REPRESENT THE CONCEPT. Drawing three boxes labeled t1/t2/t3 does NOT visualize a causal mask — they're just labeled boxes. A causal mask needs the triangular shape with shaded vs unshaded cells. Before drawing a visual, ask: would a reader understand the concept FROM THE VISUAL ALONE?
3. NUMBERS NEED STRUCTURE. If you render a matrix or table as numbers on a grid, draw cell borders or fills. Floating numbers read as stray text, not a matrix.
4. EMPTY-SLIDE FALLBACKS. If a slide genuinely has little to say, prefer (a) larger typography (title 44pt, body 20pt), (b) more supporting context (a quote, a sub-finding), or (c) a different visual treatment. Don't leave a 60%-empty slide.
5. AVOID SPARSE LISTS. 3 short items spread across 4" of textbox height looks empty. Compress, expand each item with sub-line context, or replace with a more compact treatment.
6. CONCEPTUAL ANCHORS. For a definition or philosophy slide, prefer ONE strong typographic statement (term at 60-80pt, definition at 18-20pt below) over a paragraph at 16pt.

## Mechanical rules — apply ALL of these, every time

- TITLE: write the title text as a literal string. There is NO `slide.title` property. Always pass the literal: `slide.addText("Some Title", {...})`. Add `fit: "shrink"` for long titles.
- BULLETS: every item EXCEPT the last must include `breakLine: true` in its options. Use `bullet: true`, never unicode bullet chars.
- BOXES THAT CONTAIN CONTENT: place the content INSIDE the box bounds with small inner padding (0.2-0.3"). An empty box with floating labels nearby is broken.
- STACKED ELEMENTS WITHIN A COLUMN: each must start at strictly larger y than the previous element's y+h. The grid prevents column-vs-column overlap; YOU must prevent within-column overlap.
- CHARTS: use `slide.addChart(pres.ChartType.bar, [{name, labels, values}], {...})`. Two raw rectangles is NOT a chart. Pass real data; pptxgenjs draws axes, labels, gridlines.
- NO IMAGES: do NOT call `slide.addImage`. There is no image-fetching tool. Use shapes + text + fills.
- NO HARDCODED HEX COLORS: every color comes from `palette.*`. No literal `"FFFFFF"`, no `"E5E9F2"` — use `palette.light`, `palette.bg`, etc.
- NO HARDCODED FONT NAMES: every `fontFace` comes from `palette.typography.headline_font` or `palette.typography.body_font`.
- NO REUSED OPTION OBJECTS for shadows: always call `makeShadow()` / `softShadow()` fresh per shape (the factories already handle this). Never store one shadow object and pass it to two shapes.
- NO `pres.writeFile` — the harness handles saving.
- PLAIN TEXT ONLY in slide content — no markdown markers (`*italic*`, `**bold**`, backticks). For inline emphasis, pass an array of `{text, options}` runs to `addText` (see Pattern 6 above).
- CHARSPACING RULE: `charSpacing > 0` ONLY on single-word UPPERCASE labels (eyebrows, internal section labels like "PROBLEM", "TOKENIZATION", "VENDOR LANDSCAPE"). NEVER apply charSpacing to body text, multi-word phrases, mixed-case strings, or anything containing spaces and lowercase letters. Letter-spacing on a phrase like "Multi-head self-attention" makes every glyph separated by a wide gap — it looks broken. If you want a column header in a card or grid, render it WITHOUT charSpacing at fontSize ~13 bold; reserve the letter-spaced look for true uppercase eyebrows.
- ACCENT FILL RULE: `palette.accent` is for STRIPES, EYEBROWS, KEY EMPHASIS — NEVER for large fills. Don't fill a 7-inch-wide architectural block with palette.accent (the result is a wall of bright color that drowns the content). Cards, diagram blocks, matrix cells, and any rectangle wider than ~2 inches should fill with `palette.primary` on dark decks (or `palette.light` on light decks). Use accent for top stripes (h: 0.06"), eyebrow text color, key callout text, chart series colors, and small icon-sized shapes. Same rule for `palette.secondary_accent` — narrow accent uses only.
- DIAGRAM SHAPES use `palette.primary` fill: architecture blocks, decoder layers, process boxes, matrix cells, comparison cards. Reserve `palette.accent` for highlighting WITHIN those shapes (a stripe across the top, a check mark inside a cell, a colored arrow connecting boxes). For matrices specifically: cells representing one state (e.g. "allowed") may use `palette.accent` since each cell is small (~0.5"); but for blocks larger than 1.5×1.5 inches, use `palette.primary`.
- MATH AND CODE TYPOGRAPHY: math expressions use `fontFace: "Cambria Math"` — Calibri renders subscripts and curly braces poorly. Code uses `fontFace: "Consolas"`. Body_font (Calibri/Helvetica) is for body PROSE only.

  **Detection has two paths:** (a) the brief flags the block with `"contains_math": true` or `"contains_code": true` — when present, USE the corresponding fontFace, no judgment needed. (b) The brief did NOT flag, but you spot math notation (Σ, √, integrals, `_{...}`, `^{...}`, Greek letters, fractions, equations) or code (function calls, `import`, `=>`, syntax keywords) in the text — switch fontFace yourself to Cambria Math or Consolas respectively.

  Examples that need Cambria Math: `softmax(QK^T / √d_k)`, `L = -Σ log P(x_t | x_{<t})`, `∂L/∂θ`, `Attention(Q,K,V)=...`. Examples that need Consolas: `torch.tril(torch.ones(seq_len, seq_len))`, `mask = mask.unsqueeze(0)`, any multi-line code block.
- CARD DENSITY / AUTO-SIZING RULE: a card's height MUST be derived from its content, NOT from the designer's allocated band. The designer assigns a region (e.g. "right_body, full") — that's the maximum vertical space available, NOT a target. Use this formula to size each card:

      card_h = 0.4 (title row) + 0.25 × (n_body_lines) + 0.4 (inner padding)

  `n_body_lines` is the visible line count of the body text rendered at fontSize 12 in the card's width. A 25-word body at card_w=4" wraps to ~3 lines, so card_h ≈ 0.4 + 0.75 + 0.4 = 1.55".

  If the designer's allocated band is taller than the card needs, that's FINE — leave the bottom of the band empty AND start the next element earlier. Do NOT inflate cards to fill bands. A 1.6"-tall card on a 5"-tall band is correct; a 5"-tall card on the same band with 4" of empty navy is BROKEN.

  If multiple cards in a row have varying content lengths (card 1 has 3 body lines, card 2 has 1), use the MAX content height for all cards (so they align), but don't use the full band height. The cards should be just-tall-enough to contain the longest one, not band-tall.

  A card's content must fill ≥40% of its interior. If you find your card_h gives <40% fill (e.g. one short sentence in a 3"-tall card), shrink the card. Empty card bottoms read as the model gave up.

- BAND VOIDS RULE: when the designer gives you separate `top` / `middle` / `bottom` y_bands but the brief only has 2-3 elements, do NOT spread them artificially across all bands. Pack elements at the top of the body region (starting y=1.6) with natural ~0.3" gaps, and leave the bottom empty. A slide with 2 elements at y=1.6 and y=6.5 (with 4" of empty middle) is BROKEN. Same 2 elements at y=1.6 and y=2.8 (just below each other) is correct.

- Y-OVERFLOW CHECK: BEFORE you call addText / addShape / addChart for any element, verify `y + h ≤ 6.9` (the footer reserve). For element STACKS (forEach loops over N items where each item is at `y_i = startY + i × (item_h + gap)`), verify the LAST item: `startY + (N-1) × (item_h + gap) + item_h ≤ 6.9`. If the calculation says you'd overflow, REDUCE either `item_h` or `N` BEFORE drawing. Example: 6 cards × (0.7 + 0.15) starting at y=1.6 = 1.6 + 5.1 = 6.7 (OK); 6 cards × (0.85 + 0.15) starting at y=1.6 = 1.6 + 6.0 = 7.6 (OVERFLOWS — cards run past the footer line). Do this math in your reasoning section, not after running. Also check title wrap: titles at fontSize ≥28 with >7 words may wrap to 2 lines, eating ~0.4" extra height — leave room.
- MATRIX AXIS LABELS — pptxgenjs has NO rotated text. Do NOT try to fake a vertical y-axis label by stacking each character on its own line ("Q\\nu\\ne\\nr\\ny" or one character per addText). That collides with the matrix rows. Instead, place a single horizontal text line ABOVE the column headers ("Key position →") and a single horizontal text line BELOW the row labels at the bottom-left ("↓ Query position"). Or, split the axis label into two short horizontal pieces flanking the matrix. Never stack characters vertically.
- BRACKETED PLACEHOLDERS PASS THROUGH UNCHANGED: if a content_block contains "[Company name]" or "[Founder name]" or "[Date]", render it verbatim. The user fills these in later. Do NOT invent a name to replace the placeholder.
- ILLUSTRATIVE TAG PASS THROUGH: if the brief marks a content_block with `"illustrative": true` and the text doesn't already include `(illustrative)`, append ` (illustrative)` to the rendered text. This is the user's signal that the specific is a placeholder pending verification.
- LOOP REPEATED ELEMENTS: if the brief's visual_treatment has 3+ parallel items (cards, steps, tiles), render via the forEach loop pattern in Composition patterns above. NEVER unroll N items into N separate addText/addShape blocks — that breaks visual consistency and is harder to debug.
- SPEAKER NOTES: if the brief's `speaker_notes` field is non-empty, render it via `slide.addNotes(text)` — this attaches presenter notes that are stored in the .pptx but NOT visible on the rendered slide. Do NOT also draw the speaker_notes content as on-slide text. If `speaker_notes` is empty or absent, omit `addNotes` entirely.
- FOOTER: always end the slide with the appropriate footer helper — `darkFooter(slide, slide_n, of_total)` if `palette.is_dark`, else `lightFooter(slide, slide_n, of_total)`. Exactly once per slide.

## Reason in these sections, in order

## 1. Read the layout map

Restate the grid the designer chose. List each region from the designer's `regions` map and which content block it holds. Identify the visual_treatment's region (often `right_body` or `body_full_height`).

## 2. Plan the code region by region

For each region, write out (in prose) the exact x/y/w/h, fontSize, fontFace, color (all from palette), and which block's text goes there. Use the exact grid coordinates from the vocabulary above.

For the visual_treatment, decide which composition pattern applies:
- comparison_cards / key_value_grid → Pattern 1 (N items in row) or Pattern 2 (2x2 grid)
- process_flow → Pattern 1 + connector arrows (Pattern 5)
- bar_chart → API pattern's `addChart`
- stat_callout → centered large number with caption (Pattern 9 alignment)
- matrix → Pattern 5 box-and-line

Verify within-column y values stack with strict inequality (each next y > previous y + h).

## 3. Mechanical rules checklist

For each mechanical rule above, state whether it applies to your code and how you've satisfied it. If a rule doesn't apply (e.g. no chart on this slide), say so. Be explicit about: palette color usage, `fontFace` from `palette.typography`, no hardcoded hex, footer helper called.

## Output

After the three reasoning sections, output a SINGLE JavaScript code block (```js or ```javascript):

```js
// pptxgenjs code that places each block at the assigned region
slide.background = { color: palette.bg };
slide.addText("...", { ... });
slide.addShape(...);
// ...end with footer helper
```

The code block is the only thing parsed.
"""


def _try_parse_json(s: str) -> dict | None:
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    fixed = re.sub(r",(\s*[}\]])", r"\1", s)
    if fixed != s:
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
    depth = 0
    start = -1
    for i, ch in enumerate(s):
        if ch == "{":
            if start == -1:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                cand = s[start:i+1]
                try:
                    return json.loads(cand)
                except json.JSONDecodeError:
                    pass
                fixed_cand = re.sub(r",(\s*[}\]])", r"\1", cand)
                try:
                    return json.loads(fixed_cand)
                except json.JSONDecodeError:
                    pass
    return None


def _strip_fences(s: str) -> str:
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", s.strip(), flags=re.MULTILINE)


def _stage1_reflect_impl(user_prompt: str) -> dict:
    resp = chat_complete(
        messages=[
            {"role": "system", "content": _REFLECTION_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )
    raw = resp.choices[0].message.content or ""
    ready = False
    questions: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip().rstrip("*").rstrip(".").strip()
        if stripped == "READY_TO_PLAN":
            ready = True
            break
        if stripped.startswith("NEEDS_CLARIFICATION"):
            after = stripped.split(":", 1)[1] if ":" in stripped else ""
            questions = [q.strip() for q in after.split("|") if q.strip()]
            ready = False
            break
    if not ready and not questions:
        questions = ["(model did not emit a clear signal — please rephrase)"]
    return {"prose": raw.strip(), "ready": ready, "questions": questions}


def _stage2_blueprint_impl(user_prompt: str, reflection_prose: str) -> dict:
    user_msg = (
        f"USER'S REQUEST:\n{user_prompt}\n\n"
        f"YOUR EARLIER REFLECTION:\n{reflection_prose}"
    )
    resp = chat_complete(
        messages=[
            {"role": "system", "content": _BLUEPRINT_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )
    raw = resp.choices[0].message.content or ""
    parsed = _try_parse_json(_strip_fences(raw))
    if parsed is not None:
        return parsed

    # One retry — gpt-oss occasionally emits trailing commas or unescaped quotes
    resp2 = chat_complete(
        messages=[
            {"role": "system", "content": _BLUEPRINT_PROMPT},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": raw},
            {"role": "user", "content":
                "Your previous response was not valid JSON. Re-emit the SAME "
                "blueprint as a strictly-valid JSON object — no markdown fences, "
                "no trailing commas, all strings double-quoted, all internal "
                "quotes escaped."},
        ],
    )
    parsed = _try_parse_json(_strip_fences(resp2.choices[0].message.content or ""))
    if parsed is not None:
        return parsed
    raise RuntimeError(f"blueprint stage did not return valid JSON.\nFirst response:\n{raw[:600]}")


_PALETTE_FALLBACK = {
    "palette_name": "Default light / paper + navy + violet",
    "rationale": "Neutral light fallback used when palette stage fails. "
                  "Picked light because it works for a wider range of deck "
                  "genres without imposing dramatic-editorial framing.",
    "is_dark": False,
    "tokens": {
        "bg": "F8F9FB",          # cool off-white
        "primary": "1F2937",     # charcoal — text headlines, dark surfaces
        "accent": "8B5CF6",      # violet
        "secondary_accent": "14B8A6",  # teal
        "light": "FFFFFF",       # pure white card surface
        "muted": "6B7280",       # cool gray — chrome, footers
        "dark_text": "111827",   # near-black body text
    },
    "typography": {
        "headline_font": "Georgia", "body_font": "Calibri",
        "rationale": "Default editorial pairing.",
    },
}


def _pick_palette_impl(reflection_prose: str, blueprint: dict,
                         debug_dir: Path | None = None) -> dict:
    """Deck-level: pick a coherent palette + typography. Single LLM call.
    On any failure (LLM error, malformed JSON, missing tokens), fall back to a
    safe default."""
    slide_titles = [s.get("title", "") for s in (blueprint.get("slides") or [])]
    payload = {
        "deck_title": blueprint.get("deck_title", ""),
        "deck_subtitle": blueprint.get("deck_subtitle", ""),
        "reflection_prose": reflection_prose[:4000],
        "slide_titles": slide_titles,
    }
    user_msg = (
        f"Deck context (JSON):\n{json.dumps(payload, indent=2)}\n\n"
        "Reason through the four sections, then emit the JSON output."
    )
    raw = ""
    parsed: dict = {}
    try:
        resp = chat_complete(messages=[
            {"role": "system", "content": _PALETTE_PROMPT},
            {"role": "user", "content": user_msg},
        ])
        raw = resp.choices[0].message.content or ""
        parsed = _try_parse_json(_strip_fences(raw)) or {}
    except Exception as e:
        log.warning("palette stage failed: %s; using fallback", e)

    # Validate: ensure all required tokens are present
    tokens = parsed.get("tokens") or {}
    required = ["bg", "primary", "accent", "secondary_accent", "light", "muted", "dark_text"]
    if not all(k in tokens for k in required):
        log.warning("palette missing required tokens; using fallback")
        parsed = dict(_PALETTE_FALLBACK)
    typography = parsed.get("typography") or {}
    if "headline_font" not in typography or "body_font" not in typography:
        parsed.setdefault("typography", {})
        parsed["typography"].setdefault("headline_font", "Georgia")
        parsed["typography"].setdefault("body_font", "Calibri")

    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / "palette.json").write_text(json.dumps({
            "raw_response": raw,
            "parsed": parsed,
        }, indent=2))
    return parsed


def _distill_evidence_impl(query: str, raw_results: str) -> dict:
    user_msg = (
        f"RESEARCH QUESTION:\n{query}\n\nWEB SEARCH RESULTS:\n{raw_results[:6000]}"
    )
    try:
        resp = chat_complete(
            messages=[
                {"role": "system", "content": _EVIDENCE_DISTILL_PROMPT},
                {"role": "user", "content": user_msg},
            ],
        )
    except Exception:
        return {"confidence": "none", "evidence": "", "sources": []}
    parsed = _try_parse_json(_strip_fences(resp.choices[0].message.content or ""))
    if parsed is None:
        return {"confidence": "none", "evidence": "", "sources": []}
    return {
        "confidence": parsed.get("confidence", "none"),
        "evidence": parsed.get("evidence", "") or "",
        "sources": parsed.get("sources", []) or [],
    }


def _gather_evidence_for_blueprint(
    blueprint: dict, session: "SlideSession | None" = None,
) -> None:
    slides = blueprint.get("slides") or []
    needing = [s for s in slides if (s.get("evidence_needed") or "").strip()]
    if not needing:
        return
    no_tavily = not os.environ.get("TAVILY_API_KEY")
    n_total = len(needing)
    for i, s in enumerate(needing, start=1):
        if session:
            title = (s.get("title") or f"slide {s.get('n','?')}")[:40]
            session.set_progress("evidence",
                f"Researching slide {i} of {n_total}: {title}",
                current=i, total=n_total)
        if no_tavily:
            s["evidence"] = ""
            s["confidence"] = "none"
            s["sources"] = []
            continue
        raw = _web_search_impl(s["evidence_needed"], max_results=4)
        if raw.startswith("Error") or raw.startswith("No results"):
            s["evidence"] = ""
            s["confidence"] = "none"
            s["sources"] = []
            continue
        d = _distill_evidence_impl(s["evidence_needed"], raw)
        s["evidence"] = d["evidence"]
        s["confidence"] = d["confidence"]
        s["sources"] = d["sources"]


_JS_TAGGED_RE = re.compile(r"```(?:js|javascript)\s*\n(.*?)```", re.DOTALL)
_JS_BARE_RE = re.compile(r"```\s*\n(.*?)```", re.DOTALL)


def _extract_js(raw: str) -> str | None:
    # Prefer the LAST js/javascript-tagged code block (the model may quote
    # backticks earlier in the reasoning, e.g. when citing API names in the
    # self-audit). The last tagged block is the actual code.
    matches = _JS_TAGGED_RE.findall(raw)
    if matches:
        return matches[-1].strip()
    # Fall back to the LAST untagged fenced block.
    matches = _JS_BARE_RE.findall(raw)
    if matches:
        return matches[-1].strip()
    # If there's no fenced block, do NOT return the raw response — that would
    # dump prose reasoning into the runner. Treat as "no code".
    return None


_GLYPH_NORMALIZE = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-",
    "–": "-", "—": "-", "―": "-",
})


_COVER_RULES = """# Cover slide (this slide is n=1, the cover)

Special considerations for this slide ONLY:

- The visible title at the top should be the `deck_title` from the payload, NOT the slide's `title` field (which may literally say "Cover" or similar).
- The `deck_subtitle` from the payload goes below the title, smaller and lighter.
- DO NOT render presenter names, dates, "[Your Name]", "[Logo]", or any placeholder labels. The cover is just title + subtitle + optional accent. Presenter/date are user-supplied later if at all.
- Cover slides benefit from large typography. Title at 50-60pt, subtitle at 22-26pt.
- A cover doesn't need bullet points or body paragraphs. Clean composition with strong typographic hierarchy is enough.
"""


_CLOSING_RULES = """# Closing slide (this is the last slide of the deck)

Special considerations for this slide ONLY:

- Frame as recap / key takeaways / next steps — match the deck's genre.
- 3-5 takeaways works well; more than that looks like an unfocused list.
- Optional: include a clear call-to-action ("approve budget by X", "schedule kickoff").
- A closing slide can be more text-heavy than other slides since it's a summary.
"""


def _build_coder_prompt(slide_n: int, total: int) -> str:
    parts = [_CODER_PROMPT]
    if slide_n == 1:
        parts.append(_COVER_RULES)
    if slide_n == total:
        parts.append(_CLOSING_RULES)
    return "\n\n".join(parts)


def _design_llm_call(messages: list[dict],
                      temperature: float = 0.0,
                      top_p: float = 0.1):
    """Design + revision route explicitly to RITS gpt-oss-120b — not via
    chat_complete (whose default provider is watsonx). Forced explicit so the
    design model can't drift if PALETTE_LLM_PROVIDER changes.

    Defaults clip both temperature and top_p tightly. gpt-oss-120b is an MoE
    model with non-determinism even at temperature=0; lowering top_p to 0.1
    shrinks the nucleus and reduces run-to-run variance significantly. Only
    set higher values when intentional creative variation is desired (cover
    composition, palette aesthetics)."""
    return rits_chat_complete(messages=messages, temperature=temperature,
                               top_p=top_p)


def _slide_brief_impl(bp_slide: dict, deck_title: str, deck_subtitle: str,
                       slide_n: int, of_total: int,
                       debug_dir: Path | None = None,
                       designer_feedback: str | None = None,
                       previous_brief: dict | None = None) -> dict:
    """Stage 1: decide what the slide says + what kind of visual.

    If designer_feedback is supplied, this is a retry — the previous brief
    couldn't be laid out by the designer, and the brief should slim down."""
    payload = {
        "n": slide_n, "of_total": of_total,
        "deck_title": deck_title, "deck_subtitle": deck_subtitle,
        "slide": bp_slide,
    }
    user_msg = (
        f"Slide payload (JSON):\n{json.dumps(payload, indent=2)}\n\n"
        "Reason through the four sections, then emit the JSON output."
    )
    if designer_feedback and previous_brief is not None:
        user_msg += (
            f"\n\nNOTE: your previous brief produced output the layout designer "
            f"could not place on the canvas. The designer's reason: "
            f"\"{designer_feedback}\". Slim down the content_blocks. Drop the "
            f"weakest block(s) — typically the footnote or evidence_callout — "
            f"and keep the slide focused on its main message. Re-run the four "
            f"reasoning sections, then emit a slimmer JSON output.\n\n"
            f"PREVIOUS BRIEF (too dense):\n{json.dumps(previous_brief, indent=2)}"
        )
    resp = _design_llm_call([
        {"role": "system", "content": _BRIEF_PROMPT},
        {"role": "user", "content": user_msg},
    ])
    raw = resp.choices[0].message.content or ""
    parsed = _try_parse_json(_strip_fences(raw)) or {}

    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)
        suffix = "_retry" if designer_feedback else ""
        (debug_dir / f"slide_{slide_n:02d}_brief{suffix}.json").write_text(
            json.dumps({
                "input_payload": payload,
                "designer_feedback": designer_feedback,
                "previous_brief": previous_brief,
                "raw_response": raw,
                "parsed": parsed,
            }, indent=2))
    return parsed


def _slide_designer_impl(brief: dict, slide_n: int, of_total: int,
                          debug_dir: Path | None = None,
                          log_suffix: str = "") -> dict:
    """Stage 2: decide where the brief's blocks go on the canvas."""
    user_msg = (
        f"Slide brief (JSON):\n{json.dumps(brief, indent=2)}\n\n"
        f"This is slide {slide_n} of {of_total}. "
        "Reason through the four sections, then emit the JSON output."
    )
    resp = _design_llm_call([
        {"role": "system", "content": _DESIGNER_PROMPT},
        {"role": "user", "content": user_msg},
    ])
    raw = resp.choices[0].message.content or ""
    parsed = _try_parse_json(_strip_fences(raw)) or {}

    if debug_dir is not None:
        (debug_dir / f"slide_{slide_n:02d}_designer{log_suffix}.json").write_text(
            json.dumps({
                "input_brief": brief,
                "raw_response": raw,
                "parsed": parsed,
            }, indent=2))
    return parsed


def _slide_coder_impl(brief: dict, designer: dict, slide_n: int, of_total: int,
                       deck_title: str, deck_subtitle: str, bp_slide: dict,
                       debug_dir: Path | None = None,
                       previous_attempt: str | None = None,
                       previous_error: str | None = None,
                       retry_attempt: int = 0) -> str | None:
    """Stage 3: translate brief + designer's layout map into pptxgenjs code."""
    payload = {
        "n": slide_n, "of_total": of_total,
        "deck_title": deck_title, "deck_subtitle": deck_subtitle,
        "slide": bp_slide,
    }
    user_msg = (
        f"Slide payload (JSON):\n{json.dumps(payload, indent=2)}\n\n"
        f"Slide brief (JSON):\n{json.dumps(brief, indent=2)}\n\n"
        f"Slide designer's layout map (JSON):\n{json.dumps(designer, indent=2)}\n\n"
        f"This is slide {slide_n} of {of_total}. "
        "Reason through the three sections, then emit the JS code block."
    )
    if previous_attempt and previous_error:
        user_msg += (
            f"\n\nNOTE: your previous code threw a runtime error and the slide "
            f"was lost. Reason about what caused the error, then write a corrected "
            f"JS code block. Same brief and designer output as above — only fix "
            f"the code-level mistake.\n\n"
            f"PREVIOUS ERROR:\n  {previous_error}\n\n"
            f"PREVIOUS CODE:\n```js\n{previous_attempt}\n```"
        )
    system_prompt = _build_coder_prompt(slide_n, of_total)
    resp = _design_llm_call([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ])
    raw = resp.choices[0].message.content or ""
    js = _extract_js(raw)
    cleaned = js.translate(_GLYPH_NORMALIZE) if js else None

    if debug_dir is not None:
        suffix = f"_retry{retry_attempt}" if retry_attempt > 0 else ""
        (debug_dir / f"slide_{slide_n:02d}_coder{suffix}.json").write_text(
            json.dumps({
                "input_brief": brief,
                "input_designer": designer,
                "input_payload": payload,
                "system_prompt": system_prompt,
                "previous_error": previous_error,
                "previous_attempt": previous_attempt,
                "raw_response": raw,
                "extracted_js": cleaned or "",
            }, indent=2))
    return cleaned


def _design_slide_chain(bp_slide: dict, deck_title: str, deck_subtitle: str,
                         slide_n: int, of_total: int,
                         debug_dir: Path | None = None,
                         previous_attempt: str | None = None,
                         previous_error: str | None = None,
                         retry_attempt: int = 0,
                         cached_brief: dict | None = None,
                         cached_designer: dict | None = None,
                         ) -> tuple[str | None, dict, dict]:
    """Run brief -> designer -> coder for one slide.

    Returns (js_code, brief, designer). On retry (after build_pptx exec_error),
    pass cached_brief and cached_designer to skip those stages and re-run only
    the coder with the previous error context.

    On a fresh run, if the designer flags incompatible_brief, the chain
    re-runs the brief with that feedback (kick-back) so it slims down, then
    re-runs the designer once more.
    """
    if cached_brief is not None:
        brief = cached_brief
    else:
        brief = _slide_brief_impl(bp_slide, deck_title, deck_subtitle,
                                    slide_n, of_total, debug_dir=debug_dir)
        if brief.get("flag") == "muddy_purpose":
            log.warning("slide %d: brief flagged muddy_purpose: %s",
                         slide_n, brief.get("reason", ""))

    if cached_designer is not None:
        designer = cached_designer
    else:
        designer = _slide_designer_impl(brief, slide_n, of_total,
                                         debug_dir=debug_dir)
        if designer.get("flag") == "incompatible_brief" and cached_brief is None:
            # Designer says the brief is too dense to lay out. Re-run the brief
            # with that feedback so it slims down, then re-run the designer.
            reason = designer.get("reason", "(no reason given)")
            log.info("slide %d: designer flagged incompatible_brief; "
                     "re-running brief with feedback: %s",
                     slide_n, reason[:120])
            brief = _slide_brief_impl(
                bp_slide, deck_title, deck_subtitle, slide_n, of_total,
                debug_dir=debug_dir,
                designer_feedback=reason,
                previous_brief=brief,
            )
            designer = _slide_designer_impl(brief, slide_n, of_total,
                                             debug_dir=debug_dir,
                                             log_suffix="_retry")
            if designer.get("flag") == "incompatible_brief":
                log.warning("slide %d: STILL incompatible_brief after retry: %s",
                             slide_n, designer.get("reason", ""))

    js = _slide_coder_impl(brief, designer, slide_n, of_total,
                            deck_title, deck_subtitle, bp_slide,
                            debug_dir=debug_dir,
                            previous_attempt=previous_attempt,
                            previous_error=previous_error,
                            retry_attempt=retry_attempt)
    return js, brief, designer


_HTTP = httpx.Client(
    headers={"User-Agent": "Mozilla/5.0 (compatible; Palette/1.0)"},
    follow_redirects=True, timeout=15,
)


_CRITIC_PROMPT_BASE = """You are reviewing a single slide from a presentation deck. The slide image is attached. The deck canvas is 13.333 inches wide × 7.5 inches tall — keep this in mind for any spatial estimates.

Your job: audit the slide image against the criteria below. For each criterion write a one-line assessment and one of three severity tags:
- CRITICAL: the slide will fail to communicate or render correctly. Must be fixed.
- SHOULD-FIX: the slide is OK but a clear improvement exists. Worth fixing.
- OK: this criterion is satisfied.

Be specific. Quote what you see in the image — exact words on the slide, observed visual issues, etc.

For any spatial issue (overlap, underutilization, misalignment), include a `fix_suggestion` field with a quantitative magnitude in canvas inches:
  "Move <element> down by ~0.5 inches"
  "Reduce <element> width by ~1 inch"
  "Increase gap between <A> and <B> by ~0.3 inches"
  "Enlarge <element> font from ~16pt to ~22pt"
Round to 0.1 inch (or ~5pt for type sizes). Better an approximate magnitude than no magnitude — the designer needs concrete deltas.

Criteria:

1. CANVAS UTILIZATION. Estimate the fraction of the canvas covered by content. <40% is CRITICAL, 40-60% is SHOULD-FIX, ≥60% is OK. Suggest specific shifts: "extend body text height by ~2 inches" or "enlarge title from 36pt to 50pt".

2. VISUAL ELEMENTS REPRESENT THE CONCEPT. If there's a diagram, table, or chart, does it actually communicate the topic? "Three labeled boxes" pretending to be a mask matrix is CRITICAL. If the slide has no visual and the topic is genuinely paragraph-shaped, that's OK.

3. TEXT AND SHAPE OVERLAP. Any text elements overlapping each other or shapes obscuring content? CRITICAL if text is unreadable. Quantify the overlap: "title and subtitle overlap by ~0.5 inches; move subtitle down by ~0.6 inches".

4. TITLE PRESENT AND PROMINENT. Is there a clear slide title at the top, large and legible?

5. NO PLACEHOLDER STRINGS. Look for "[Your Name]", "TBD", "Presenter:", "Click here", "Logo here", "Date: <month>". CRITICAL if present.

6. NUMBERS WITH STRUCTURE. If the slide shows a matrix/table/grid of numbers, does it have visible cell borders/backgrounds?

7. TYPOGRAPHIC HIERARCHY. Title noticeably larger than body? Sub-headers intermediate? Or is everything the same size?

8. STACKED-SHAPE OVERLAP. If stacked boxes, do they overlap neighbors? Quantify: "shape gaps too small by ~0.15 inches".

9. MATH/CODE TYPOGRAPHY. If you see math notation (Σ, √, ^{...}, _{...}, fractions, Greek letters, equations like "QK^T / √d") rendered in a sans-serif body font, that's CRITICAL — should be Cambria Math. If you see code (function calls, `import`, multi-line technical syntax) NOT in a monospace font, that's CRITICAL — should be Consolas. Also CRITICAL: raw LaTeX source rendered as text (visible `\\bigl(`, `\\sin`, `\\frac{}{}`, literal `_{...}` or `^{...}` characters) — that means the model emitted LaTeX source instead of rendered math.

10. DUAL-TITLE / SECONDARY HEADING. Is there a single visible title at the top, OR is there a SECOND large bold heading just below the title (e.g., "Three core tasks powered by..." rendered at fontSize ~24 bold below the actual title)? CRITICAL if two headings stack at the top.

11. CITATION / SOURCE LEAKS. Look for URLs ("https://...", "github.com/..."), paper citations ("Vaswani et al., 2017", "Brown et al., 2020"), blog/forum names ("Hugging Face forums", "GitHub issues", "Princeton blog", "Medium"), or "Source:"/"Sources:"/"Reference:" prefixes appearing as visible slide content. CRITICAL — these belong in speaker notes, not on the slide.

12. HALLUCINATED MODEL/CITATION NAMES. Look for clearly fictional model versions or organization names. Real GPT versions: GPT-2, GPT-3, GPT-3.5, GPT-4, GPT-4o, GPT-5. NOT real: GPT-13, GPT-14, GPT-15, etc. If you see a non-existent GPT-N, or an unfamiliar benchmark organization (e.g. "Codesota", "Kilo AI") that doesn't match common ML knowledge, flag CRITICAL.

13. DOUBLE BULLETS. Look for items beginning with TWO bullet glyphs ("• • Hugging Face Transformers" or "• ● Item"). The model may have used `bullet: true` AND prefixed the text with a literal "•". CRITICAL — render shows duplicated bullet markers.

14. CANVAS OVERFLOW. Does any text or shape appear to bleed past the bottom of the canvas, or does the slide look unusually compressed/scaled (suggesting PDF-rendering caught content past the canvas edge)? CRITICAL if content is being clipped at the footer line.

15. CARD DENSITY. If the slide has cards or boxes with content, does each card's content fill at least 40% of the card interior? Or are there cards that are 4+ inches tall containing only one short sentence with vast empty bottoms? SHOULD-FIX if cards are <40% filled — recommend reducing card_h to match content.

16. VOID REGIONS / VERTICAL PACING. Are there 3+ inches of empty space between elements that should be packed closer? E.g., title at y=0.5, body at y=1.5, then nothing until cards at y=5.5 — that's 4 inches of empty middle. SHOULD-FIX with magnitude.

Return ONLY a JSON object — no prose, no code fences:

{
  "issues": [
    {"criterion": "<name>", "severity": "CRITICAL|SHOULD-FIX|OK", "reason": "<one-line, quote what you see>", "fix_suggestion": "<quantitative magnitude OR null if N/A>"},
    ...
  ],
  "verdict": "REVISE|ACCEPT",
  "summary": "<2-3 sentences summarizing the slide and major issues>"
}

`verdict` is REVISE if there is at least one CRITICAL or SHOULD-FIX issue; otherwise ACCEPT.
"""


_CRITIC_PROMPT_RECHECK_SUFFIX = """

# Re-check pass

This slide was previously flagged for these issues. The designer attempted a revision; you are now seeing the revised version. For EACH previously-flagged issue, classify its `status_vs_previous` field as one of:
- "RESOLVED" — the issue is fixed
- "STILL_PRESENT" — the issue persists at roughly the same severity
- "PARTIALLY_RESOLVED" — improvement is visible but the issue isn't fully fixed
- "NEW" — use this ONLY for a CRITICAL or SHOULD-FIX issue on a criterion that was OK in the previous critique but is now a problem (i.e. the revision broke something that was fine)

For criteria that are simply OK now and were not flagged before, use status_vs_previous=null (or omit the field). Do NOT mark OK items as "NEW" — NEW means a regression or a new problem that didn't exist before, not "this criterion wasn't on the prior list."

Be honest — if the revision didn't actually fix the spatial issue (you can see overlap is still there), mark STILL_PRESENT. Don't credit effort.

Previous critique's flagged issues:
{previous_issues_block}
"""


def _build_critic_prompt(previous_issues: list[dict] | None = None) -> str:
    if not previous_issues:
        return _CRITIC_PROMPT_BASE
    flagged = [i for i in previous_issues
                if i.get("severity") in ("CRITICAL", "SHOULD-FIX")]
    if not flagged:
        return _CRITIC_PROMPT_BASE
    block = "\n".join(
        f"  - [{i.get('severity')}] {i.get('criterion','')}: {i.get('reason','')}"
        for i in flagged
    )
    return _CRITIC_PROMPT_BASE + _CRITIC_PROMPT_RECHECK_SUFFIX.format(
        previous_issues_block=block
    )


def _critique_slide_visually(png_path: Path, bp_slide: dict, slide_n: int,
                              total: int,
                              previous_issues: list[dict] | None = None) -> dict:
    """Send a rendered slide PNG + slide brief to Qwen3-VL for critique.
    If previous_issues is supplied, the critic also classifies each
    previously-flagged issue as RESOLVED / STILL_PRESENT / PARTIALLY_RESOLVED.
    Returns {issues, verdict, summary, raw}."""
    if not png_path.exists():
        return {"issues": [], "verdict": "ACCEPT", "summary": "(no image)",
                "raw": "", "error": f"png missing at {png_path}"}
    with open(png_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    brief = json.dumps({
        "slide_number": slide_n,
        "total_slides": total,
        "title": bp_slide.get("title", ""),
        "purpose": bp_slide.get("purpose", ""),
        "content_summary": bp_slide.get("content_summary", ""),
    }, indent=2)

    prompt = _build_critic_prompt(previous_issues)
    user_msg = prompt + f"\n\nSlide brief (what the author asked for):\n{brief}\n"

    try:
        resp = vision_chat_complete(messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": user_msg},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ],
        }], max_tokens=2500)
    except Exception as e:
        return {"issues": [], "verdict": "ACCEPT", "summary": "(critic call failed)",
                "raw": "", "error": f"{type(e).__name__}: {e}"}

    raw = resp.choices[0].message.content or ""
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(),
                     flags=re.MULTILINE)
    parsed = _try_parse_json(cleaned) or {}
    return {
        "issues": parsed.get("issues", []),
        "verdict": parsed.get("verdict", "ACCEPT"),
        "summary": parsed.get("summary", ""),
        "raw": raw,
    }


def _revise_slide_with_critique(bp_slide: dict, deck_title: str,
                                 deck_subtitle: str, slide_n: int, total: int,
                                 previous_code: str, critique: dict,
                                 brief: dict, designer: dict,
                                 debug_dir: Path | None = None,
                                 pass_label: str = "") -> str | None:
    """Re-call the coder with the critique findings as previous_error context.

    Routes vision-critic findings (CRITICAL + SHOULD-FIX issues with
    fix_suggestion deltas) back through `_slide_coder_impl`, treating the
    critique as the prior failure to correct. The coder receives the SAME
    brief and designer outputs but a structured error message describing the
    rendered defects.
    """
    issues = critique.get("issues", []) or []
    flagged = [i for i in issues
                if i.get("severity") in ("CRITICAL", "SHOULD-FIX")]
    if not flagged:
        log.info("slide %d: critique has no flagged issues, skipping revision",
                  slide_n)
        return None

    issue_lines = []
    for i in flagged:
        line = (f"  - [{i.get('severity')}] {i.get('criterion','')}: "
                f"{i.get('reason','')}")
        if i.get('fix_suggestion'):
            line += f"  | FIX: {i['fix_suggestion']}"
        issue_lines.append(line)
    error_block = (
        "The rendered slide was audited by a vision critic against the V1.4 "
        "defect categories (math typography, citation leaks, dual-title, "
        "overflow, hallucinations, sparse canvas, double bullets, card density, "
        "void regions). The following issues were flagged on YOUR previous code:"
        f"\n\n{chr(10).join(issue_lines)}\n\n"
        f"REVIEWER SUMMARY: {critique.get('summary', '')}\n\n"
        "Re-write the slide JS using the SAME brief and designer output. Apply "
        "the fix_suggestion magnitudes literally (e.g. if it says 'move element "
        "down by ~0.5 inches', shift y by 0.5). Do NOT change the layout grid "
        "or visual_treatment — only fix the called-out defects."
    )

    new_js = _slide_coder_impl(
        brief=brief,
        designer=designer,
        slide_n=slide_n,
        of_total=total,
        deck_title=deck_title,
        deck_subtitle=deck_subtitle,
        bp_slide=bp_slide,
        debug_dir=debug_dir,
        previous_attempt=previous_code,
        previous_error=error_block,
        retry_attempt=0,
    )

    if debug_dir is not None:
        suffix = f"_{pass_label}" if pass_label else ""
        (debug_dir / f"slide_{slide_n:02d}_critique_revision{suffix}.json").write_text(
            json.dumps({
                "critique_issues": critique.get("issues", []),
                "critique_summary": critique.get("summary", ""),
                "previous_code": previous_code,
                "revision_error_block": error_block,
                "revised_js": new_js or "",
            }, indent=2)
        )

    return new_js


def _web_search_impl(query: str, max_results: int = 4) -> str:
    api_key = os.environ.get("TAVILY_API_KEY")
    if not api_key:
        return "Error: TAVILY_API_KEY is not set."
    try:
        from tavily import TavilyClient  # type: ignore
    except ImportError:
        return "Error: tavily not installed (`pip install tavily-python`)."
    try:
        resp = TavilyClient(api_key=api_key).search(query=query, max_results=max_results)
    except Exception as e:
        return f"Error during search: {type(e).__name__}: {e}"
    results = resp.get("results") or []
    if not results:
        return f"No results for: {query}"
    return "\n\n".join(
        f"Title: {r.get('title','')}\nURL: {r.get('url','')}\nSnippet: {r.get('content','')}"
        for r in results
    )


def _read_webpage_impl(url: str, max_chars: int = 8000) -> str:
    try:
        resp = _HTTP.get(f"https://r.jina.ai/{url}", headers={"Accept": "text/plain"})
        resp.raise_for_status()
        text = resp.text if len(resp.text) > 100 else ""
    except Exception:
        text = ""
    if not text:
        try:
            resp = _HTTP.get(url)
            resp.raise_for_status()
            text = re.sub(r"<[^>]+>", " ", resp.text)
            text = re.sub(r"\s+", " ", text).strip()
        except Exception as e:
            return f"Error fetching {url}: {type(e).__name__}: {e}"
    if len(text) > max_chars:
        return text[:max_chars] + f"\n\n[truncated at {max_chars} chars]"
    return text


@dataclass
class SlideSession:
    session_id: str
    root: Path
    reflection: dict = field(default_factory=dict)
    blueprint: dict = field(default_factory=dict)
    progress: dict = field(default_factory=dict)

    def set_progress(self, stage: str, message: str,
                      current: int = 0, total: int = 0) -> None:
        self.progress = {"stage": stage, "current": current,
                         "total": total, "message": message}

    @classmethod
    def create(cls, sessions_root: Path, session_id: str | None = None) -> "SlideSession":
        sid = session_id or uuid.uuid4().hex[:12]
        root = sessions_root / sid
        (root / "output").mkdir(parents=True, exist_ok=True)
        return cls(session_id=sid, root=root)

    @property
    def output_dir(self) -> Path:
        return self.root / "output"

    @property
    def pptx_path(self) -> Path:
        return self.output_dir / "deck.pptx"


def _plan_deck_impl(topic: str) -> dict:
    reflection = _stage1_reflect_impl(topic)
    if not reflection["ready"]:
        return {"status": "needs_clarification", "reflection": reflection}
    blueprint = _stage2_blueprint_impl(topic, reflection["prose"])
    blueprint.setdefault("slides", [])
    return {"status": "ready", "reflection": reflection, "blueprint": blueprint}


def _build_deck_impl(session: SlideSession) -> str:
    bp = session.blueprint
    if not bp or not bp.get("slides"):
        return "Error: no blueprint to build. Call plan_deck first."

    slides = bp["slides"]
    deck_title = bp.get("deck_title", "")
    deck_subtitle = bp.get("deck_subtitle", "")
    n_total = len(slides)

    # Persist planning artifacts so we can inspect them after the run.
    planning_dir = session.output_dir / "planning_log"
    planning_dir.mkdir(parents=True, exist_ok=True)
    refl = getattr(session, "reflection", {}) or {}
    if refl.get("prose"):
        (planning_dir / "reflection.txt").write_text(refl["prose"])
    (planning_dir / "blueprint.json").write_text(json.dumps(bp, indent=2))

    # Pick the deck-level palette. One LLM call. Falls back to a default
    # if the call fails or returns malformed JSON.
    session.set_progress("palette", "Picking palette…", 0, 1)
    try:
        palette = _pick_palette_impl(refl.get("prose", ""), bp,
                                       debug_dir=planning_dir)
    except Exception as e:
        log.warning("palette stage errored: %s; using fallback", e)
        palette = dict(_PALETTE_FALLBACK)
    setattr(session, "palette", palette)

    session.set_progress("evidence", "Researching evidence…", 0, n_total)
    try:
        _gather_evidence_for_blueprint(bp, session=session)
    except Exception as e:
        log.warning("evidence gathering failed: %s", e)

    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    debug_dir = session.output_dir / "design_log"
    slide_js: list[str | None] = [None] * n_total
    slide_briefs: list[dict | None] = [None] * n_total
    slide_designers: list[dict | None] = [None] * n_total
    completed = [0]
    progress_lock = threading.Lock()

    def design_one(i: int, bp_slide: dict) -> tuple[int, str | None, dict, dict]:
        title = (bp_slide.get("title") or f"slide {i}")[:40]
        log.info("design slide %d/%d: %s", i, n_total, title)
        brief: dict = {}
        designer: dict = {}
        try:
            js, brief, designer = _design_slide_chain(
                bp_slide, deck_title, deck_subtitle, i, n_total,
                debug_dir=debug_dir,
            )
        except Exception as e:
            log.warning("design failed for slide %d: %s", i, e)
            js = None
        with progress_lock:
            completed[0] += 1
            session.set_progress("designing",
                f"Designed {completed[0]} of {n_total} slides",
                current=completed[0], total=n_total)
        return i, js, brief, designer

    with ThreadPoolExecutor(max_workers=6) as exe:
        futures = [exe.submit(design_one, i, s)
                   for i, s in enumerate(slides, start=1)]
        for fut in as_completed(futures):
            i, js, brief, designer = fut.result()
            slide_js[i - 1] = js
            slide_briefs[i - 1] = brief
            slide_designers[i - 1] = designer

    MAX_RETRIES = 2
    pptx = None
    for attempt in range(MAX_RETRIES + 1):
        session.set_progress("rendering",
            "Rendering deck…" if attempt == 0 else f"Re-rendering after fixing slide errors (try {attempt})…",
            n_total, n_total)
        pptx, failures = build_pptx(slide_js, session.output_dir, deck_title=deck_title, palette=palette)
        exec_failures = [f for f in failures if f.get("kind") == "exec_error"]
        if not exec_failures or attempt == MAX_RETRIES:
            if exec_failures:
                log.warning("max retries reached; %d slides still failing",
                            len(exec_failures))
            break
        log.info("retry pass %d: %d slides failed, regenerating",
                 attempt + 1, len(exec_failures))
        for f in exec_failures:
            n = f.get("slide", 0)
            if not (1 <= n <= n_total):
                continue
            bp_slide = slides[n - 1]
            session.set_progress("retrying",
                f"Fixing slide {n}: {(bp_slide.get('title') or '')[:40]}",
                current=n, total=n_total)
            log.info("retry slide %d (error: %s)", n, f.get("message", "")[:120])
            try:
                new_js, _b, _d = _design_slide_chain(
                    bp_slide, deck_title, deck_subtitle, n, n_total,
                    debug_dir=debug_dir,
                    previous_attempt=slide_js[n - 1],
                    previous_error=f.get("message", "(unknown error)"),
                    retry_attempt=attempt + 1,
                    cached_brief=slide_briefs[n - 1],
                    cached_designer=slide_designers[n - 1],
                )
                if new_js:
                    slide_js[n - 1] = new_js
            except Exception as e:
                log.warning("retry failed for slide %d: %s", n, e)

    render_previews(pptx, session.output_dir)

    if os.environ.get("PALETTE_SKIP_CRITIQUE") == "1":
        log.info("vision critique disabled (PALETTE_SKIP_CRITIQUE=1) — skipping")
        session.set_progress("done", "Deck rendered (critique disabled)",
                             current=n_total, total=n_total)
        return f"Built {n_total} slide(s); critique skipped."

    # Vision critique pass: render PNGs of every slide, send each to the VLM,
    # parse issues, and revise slides that need fixes. One revision per slide.
    pngs = sorted(session.output_dir.glob("slide-*.png"))
    if pngs and len(pngs) == n_total:
        critique_completed = [0]

        def critique_one(i: int, bp_slide: dict, png: Path) -> tuple[int, dict]:
            log.info("critique slide %d/%d", i, n_total)
            res = _critique_slide_visually(png, bp_slide, i, n_total)
            (debug_dir / f"slide_{i:02d}_critique.json").write_text(
                json.dumps(res, indent=2)
            )
            with progress_lock:
                critique_completed[0] += 1
                session.set_progress("critiquing",
                    f"Critiqued {critique_completed[0]} of {n_total} slides",
                    current=critique_completed[0], total=n_total)
            return i, res

        critiques: dict[int, dict] = {}
        with ThreadPoolExecutor(max_workers=6) as exe:
            futures = [exe.submit(critique_one, i, slides[i - 1], pngs[i - 1])
                       for i in range(1, n_total + 1)]
            for fut in as_completed(futures):
                i, res = fut.result()
                critiques[i] = res

        # Pick slides that need revision (any CRITICAL or SHOULD-FIX issue,
        # i.e. verdict == "REVISE" from the critic)
        to_revise = [
            i for i in range(1, n_total + 1)
            if critiques.get(i, {}).get("verdict") == "REVISE"
            and slide_js[i - 1] is not None
        ]
        log.info("vision critique: %d/%d slides flagged for revision",
                 len(to_revise), n_total)

        if to_revise:
            revision_completed = [0]
            n_to_revise = len(to_revise)

            def revise_one(i: int) -> tuple[int, str | None]:
                log.info("revise slide %d (per critique)", i)
                try:
                    new_js = _revise_slide_with_critique(
                        slides[i - 1], deck_title, deck_subtitle,
                        i, n_total,
                        previous_code=slide_js[i - 1] or "",
                        critique=critiques[i],
                        brief=slide_briefs[i - 1] or {},
                        designer=slide_designers[i - 1] or {},
                        debug_dir=debug_dir,
                    )
                except Exception as e:
                    log.warning("revision failed for slide %d: %s", i, e)
                    new_js = None
                with progress_lock:
                    revision_completed[0] += 1
                    session.set_progress("revising",
                        f"Revising {revision_completed[0]} of {n_to_revise} flagged slides",
                        current=revision_completed[0], total=n_to_revise)
                return i, new_js

            with ThreadPoolExecutor(max_workers=6) as exe:
                futures = [exe.submit(revise_one, i) for i in to_revise]
                for fut in as_completed(futures):
                    i, new_js = fut.result()
                    if new_js:
                        slide_js[i - 1] = new_js

            # Re-build and re-render with the revised slides
            session.set_progress("rendering", "Rendering revised deck…",
                                  n_total, n_total)
            pptx, _ = build_pptx(slide_js, session.output_dir, deck_title=deck_title, palette=palette)
            render_previews(pptx, session.output_dir)

            # ── Verify-the-fix pass ────────────────────────────────────────
            # Re-critique only the slides we just revised. The new critic
            # call sees the previous issues and marks each as RESOLVED /
            # STILL_PRESENT / PARTIALLY_RESOLVED.
            pngs2 = sorted(session.output_dir.glob("slide-*.png"))
            recheck_completed = [0]
            n_revised = len(to_revise)

            def recheck_one(i: int) -> tuple[int, dict]:
                log.info("re-critique slide %d (verify-the-fix)", i)
                prev_issues = critiques.get(i, {}).get("issues", [])
                res = _critique_slide_visually(
                    pngs2[i - 1], slides[i - 1], i, n_total,
                    previous_issues=prev_issues,
                )
                (debug_dir / f"slide_{i:02d}_critique_pass2.json").write_text(
                    json.dumps(res, indent=2)
                )
                with progress_lock:
                    recheck_completed[0] += 1
                    session.set_progress("re-critiquing",
                        f"Re-checking {recheck_completed[0]} of {n_revised} revised slides",
                        current=recheck_completed[0], total=n_revised)
                return i, res

            critiques_pass2: dict[int, dict] = {}
            with ThreadPoolExecutor(max_workers=6) as exe:
                futures = [exe.submit(recheck_one, i) for i in to_revise]
                for fut in as_completed(futures):
                    i, res = fut.result()
                    critiques_pass2[i] = res

            # Slides where pass-2 critic still flags STILL_PRESENT CRITICAL
            # issues (or NEW CRITICAL issues) get one more revision.
            # PARTIALLY_RESOLVED and SHOULD-FIX issues are accepted — pass 1
            # already moved the needle and another revision often does not
            # help (and sometimes makes things worse).
            still_broken = []
            for i in to_revise:
                c2 = critiques_pass2.get(i, {})
                if c2.get("verdict") != "REVISE":
                    continue
                worth_retrying = any(
                    it.get("severity") == "CRITICAL"
                    and it.get("status_vs_previous") in ("STILL_PRESENT", "NEW")
                    for it in c2.get("issues", [])
                )
                if worth_retrying:
                    still_broken.append(i)

            log.info("verify-the-fix pass: %d/%d revised slides still need attention",
                     len(still_broken), len(to_revise))

            if still_broken:
                pass2_completed = [0]
                n_pass2 = len(still_broken)

                def revise_again(i: int) -> tuple[int, str | None]:
                    log.info("revise slide %d (pass 2)", i)
                    try:
                        new_js = _revise_slide_with_critique(
                            slides[i - 1], deck_title, deck_subtitle,
                            i, n_total,
                            previous_code=slide_js[i - 1] or "",
                            critique=critiques_pass2[i],
                            brief=slide_briefs[i - 1] or {},
                            designer=slide_designers[i - 1] or {},
                            debug_dir=debug_dir,
                            pass_label="pass2",
                        )
                    except Exception as e:
                        log.warning("pass-2 revision failed for slide %d: %s", i, e)
                        new_js = None
                    with progress_lock:
                        pass2_completed[0] += 1
                        session.set_progress("revising",
                            f"Revising {pass2_completed[0]} of {n_pass2} (pass 2)",
                            current=pass2_completed[0], total=n_pass2)
                    return i, new_js

                with ThreadPoolExecutor(max_workers=6) as exe:
                    futures = [exe.submit(revise_again, i) for i in still_broken]
                    for fut in as_completed(futures):
                        i, new_js = fut.result()
                        if new_js:
                            slide_js[i - 1] = new_js

                # Final re-build + re-render
                session.set_progress("rendering",
                    "Rendering after second revision pass…",
                    n_total, n_total)
                pptx, _ = build_pptx(slide_js, session.output_dir, deck_title=deck_title, palette=palette)
                render_previews(pptx, session.output_dir)

    n_ok = sum(1 for j in slide_js if j is not None)
    session.set_progress("idle", "", 0, 0)
    return f"Built {n_ok}/{n_total} slides."


def _clear_deck_impl(session: SlideSession) -> str:
    session.reflection = {}
    session.blueprint = {}
    session.progress = {}
    for f in session.output_dir.glob("*"):
        try:
            f.unlink()
        except Exception:
            pass
    return "Cleared. Call plan_deck(topic) to start a new deck."


def make_tools(session: SlideSession) -> dict[str, Callable]:
    def plan_deck(topic: str) -> str:
        session.set_progress("planning", "Reflecting on the deck…")
        result = _plan_deck_impl(topic)
        session.reflection = result.get("reflection", {})
        session.blueprint = result.get("blueprint", {}) or {}
        session.set_progress("idle", "")

        if result["status"] == "needs_clarification":
            ref = result["reflection"]
            lines = ["I need a bit more info before planning:"]
            for i, q in enumerate(ref.get("questions", []), start=1):
                lines.append(f"  {i}. {q}")
            return "\n".join(lines)

        bp = result["blueprint"]
        slides = bp.get("slides") or []
        out = []
        for i, s in enumerate(slides, start=1):
            num = s.get("n", i)
            title = s.get("title", "")
            out.append(f"{num}. {title}")
        out.append("")
        out.append("Say 'build it' to materialise the slides, or describe any changes.")
        return "\n".join(out)

    def build_deck() -> str:
        try:
            return _build_deck_impl(session)
        finally:
            session.set_progress("idle", "")

    def clear_deck() -> str:
        return _clear_deck_impl(session)

    def web_search(query: str, max_results: int = 4) -> str:
        return _web_search_impl(query, max_results=max_results)

    def read_webpage(url: str) -> str:
        return _read_webpage_impl(url)

    return {
        "plan_deck": plan_deck,
        "build_deck": build_deck,
        "clear_deck": clear_deck,
        "web_search": web_search,
        "read_webpage": read_webpage,
    }
