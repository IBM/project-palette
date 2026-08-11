# Project Palette - Deck building
Audience: IBM AI automation leadership
Slides: 10

Preferences:
- Background: light
- Tone: candid, technical-but-accessible, focused on what this unblocks for the automation roadmap

## Cover
Project Palette - Building decks for IBMers

## Why this matters
- Everyone builds decks — executives, marketing, consultants, engineers.
- While using agents to build decks can save a lot of time ... 
- Confidentiality rules out the use of frontier models. Opportunity for us to exploit.

## How models actually build decks
- Writing code using a slide-rendering library. Two dominant ones are pptxgenjs (JavaScript) and python-pptx (Python).
- Complex : Own grammar, units, formats, coordinate systems.
- Frontier models have been trained on this specifically to work. Not the case with open models. 

Side by side code comparison: 

pptxgenjs (JavaScript):
```js
slide.background = { color: "FFFFFF" };
slide.addText("Q3 Revenue", {
  x: 0.5, y: 0.5, w: 9, h: 0.7,
  fontFace: "Inter", fontSize: 32, bold: true,
  color: "1A1A1A"
});
```

python-pptx (Python):
```python
prs = Presentation()
run = tx.text_frame.paragraphs[0].add_run()
run.text = "Q3 Revenue"
run.font.size, run.font.bold = Pt(32), True
shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE,
    Inches(.5), Inches(1.5), Inches(4), Inches(2))
shp.fill.solid()
shp.fill.fore_color.rgb = RGBColor(0x0A, 0x25, 0x40)
```

## CUGA agent
- First attempt: gave CUGA the deck-building tools and asked it to produce a deck end-to-end.
- Backed by gpt-oss-120b, the agent could call the tools but the output was poor — defaults everywhere, no narrative, no design intent.
- Adding tools => agent can do it != can do it well (this is an important takeaway, so find a way to emphasize it in a box or something)
- Hypothesis: Can a more specialized architecture and instruction stack do better?

## Palette agent
- Approach 1 : Can the model generate deck building code end-to-end. Provide systematic instructions about pptxgenjs.
- Failed (bold red). Numerous code errors, hallucinations, no consistent layouts.
- Approach 2 : Multi-stage architecture (maybe show a process flow) with planner -> designer -> coder -> critic loops.
- Failed (bold red). Better, but still inability to understand spatial coordinates, broken charts, overlapping slide elements, poor slide design.
- Important takeaway : Open-source models have a ceiling.

## Fine-tuning - Design
- Two phases of building a deck, separation of concerns. 
- Phase 1: Designing the deck (narrative arc, slide content, layout, palette) , wants the whole deck in scope
- Phase 2: Coding the deck (accurate rendering, handle overlap, charts, whitespace) , wants one slide at a time.

## Fine-tuning - Approach
- GPT-oss 20B model. 
- Synthesized 240 decks , average 10 slides.
- Trained two LoRA adapters, one for each phase
- (Show the flow using some visual diagram) User input , this can also be fed into an optional agent planner -> markdown plan -> Slide designer (Phase 1) -> JSON deck brief -> Slide coder (Phase 2) -> PPTX .. (You can change the phrasing as needed to make a clean diagram)

## Demo
- (Sparse slide with centered text with appropriate font sizes) Game Time! Claude vs. Palette

## Literature
(Couple of sections on fine-tuning and benchmarks)
- SlideCoder / SlideMaster (EMNLP 2025) — a 7B model fine-tuned on slide-rendering code beats GPT-4o-class baselines by ~40 points on layout fidelity and execution accuracy.
- AutoPresent (2025) — an 8B fine-tuned model produces decks rated comparable to GPT-4o by human evaluators.
- Takeaway: small fine-tuned models can close the gap with frontier

Benchmarks 
- SlideBench (2026) — head-to-head leaderboard for AI presentation tools
- PresentBench (2026) — visual style, typography, layout balance, and cross-modal grounding. 
- Takeaway: The community is acknowledging importance of this use-case

## Next steps

- Phase 1 - Data : scalable data synthesis, improved diversity of genres, charts, etc.
- Phase 2 - Methodology: SFT -> RLVR + VLM as a critic in the loop
- Phase 3 - Agent: Editing, multimodal sources, reference decks/sources.
