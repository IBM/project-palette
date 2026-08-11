# Project Palette 

Audience: IBM internal demo audience, update on Palette progress.

Preferences:
- Tone: confident, technical, executive briefing
- Sub-brand: base IBM
- Length: 9 slides

[[body text minimum 16pt -- bullets and captions should be readable from across the room]]

## Cover
Title: "Project Palette". Subtitle: "Recap and Updates". Date: June 2, 2026.

## Recap -- where we were
Open-source LLMs underperform on slide-deck generation. [[render as two stacked takeaway boxes]]
- **1 -- Open-source models do not build good decks.** Deck-building needs specialized code knowledge, and frontier models (Claude, GPT) have been heavily trained unlike open models.
- **2 -- v2: 250 decks of training data.** [[highlight 250 decks in accent color]] Trained LoRA on gpt-oss-20b -- deck visually had Claude-like style. Demonstrated how fine-tuning can close the gap with frontier models.

## What we did next 
Distilled IBM's design guidelines AND taught the LoRA to honor user directives. [[render as a two-column layout -- the "What we did" bullets as a numbered list on the left, the "Example plan snippet" rendered with Consolas font as a markdown code block on the right]]

**What we did**
1. **Identified IBM's guidelines** -- Carbon color palette, Plex Light typography, 48 canonical layouts, sentence case, IBM voice.
2. **Crafted ~1,000 training decks** -- a mix of normal and IBM-aesthetic decks for register diversity. [[highlight "~1,000 training decks" in accent color]]
3. **Diverse training examples** -- different chart types, layouts, and inline `[[user directives]]` as a first-class control surface.

**Example plan snippet** -- [[Show the directives below as is.. they are demonstrating the example]]

    ## Demo Title
    "[[Render as numbered list]]"
    1. Palette.
    2. Is. "[[Make this bold and italicized]]"
    3. Awesome "[[Use red font]]"

## Deployment -- live on RITS
The fine-tuned adapter is available on RITS. [[show two-columns -- Web UI left, Local install right]]

**Web UI**
1. Open **http://9.47.166.122:18814/** in any browser. [[highlight "http://9.47.166.122:18814/" in accent color]]
2. No installation -- author a plan, generate a deck, download the .pptx.
3. Hits the live RITS endpoint

**Local install**
1. Clone: `git clone https://github.com/IBM/project-palette/`
2. Install: `cd project-palette && make install`
3. Run: `python app.py`
4. Same RITS endpoint, runs the same adapter.

## PresentBench Benchmark
We beat Manus and other closed-source systems.
[[render as a data table at x=0.3, y=2.0, w=12.7, h=4.5 spanning full canvas width. Column widths: System=3.5, Talk=1.6, Economics=1.6, Education=1.6, Avg=1.6, Open-source=2.7. Row height 0.55in. Header font 16pt bold, body cell font 16pt. Center-align all numeric columns.  Open-source column shows centered ✓ or ✗ icon. Use same order, DO NOT change it]]
- **NotebookLM** -- 69.2 | 58.2 | 55.0 | 60.8 | No
- **Palette (ours)** -- **60.9 | 59.1 | 59.0 | 59.7 | Yes** [[highlight the row of "Palette (ours)" in accent color]]
- **Manus 1.6** -- 63.0 | 52.8 | 50.7 | 55.5 | No
- **Tiangong** -- 59.8 | 46.5 | 53.7 | 53.3 | No
- **PPTAgent v2** -- 56.6 | 46.1 | 46.1 | 49.6 | Yes
- **Qwen** -- 38.6 | 26.5 | 36.6 | 33.9 | No

Caption: 
- Palette **beats** Manus by 4pt and is 1pt behind NotebookLM. Best performing open-source system. [[highlight beats]]
- Beats NotebookLM on **Correctness** and **Fidelity** (Hallucination)

## Next steps 
[[render as a two-column comparison layout -- Model improvement left, Delivery right. Don't add more text. Use 16 point font]]

**Model improvement**
- **Editing capability** 
- **Multi-modal support**
- **RL-based training** 
- **Additional benchmarking**

**Delivery**
- **CUGA sub-agent** 
- **Bob skill**
- **Reference apps**
- **watsonx deployment**

Caption: This is a hard problem but provides differentiation. Improvements will take time and effort.


## Critic Loop 
After the coder writes the JS, two critics review the output and request rewrites BEFORE the user sees the deck. 

1. **Detect** -- deterministic detector measures every slide element
2. **Repair** -- editor model (gpt-oss-120b) rewrites flagged slides given the geometry facts
3. **Verify** -- re-render, re-detect; accept ONLY if defects strictly decreased AND text content was preserved.
4. **Keep or Revert** -- bad rewrites are reverted to the original; the critic never makes a slide worse.


## User Directives 
Inline `[[directive]]` markers let users steer treatment, emphasis, and structure. The LoRA was trained to honor them as render hints. [[render as a 4-row data table with three columns: Category | What it controls | Example]]

- **Length** | Adjust deck or section size | `[[under 6 slides]]` or `[[compress the intro]]`
- **Treatment** | Name the rendering shape for a section | `[[render as a bar chart]]`
- **Emphasis** | Style a specific phrase or word | `[[Make this bold]]`
- **Hints** | General user hints | `[[be concise]]`

Caption: Directives are scoped two ways -- **deck-level** (between Preferences and the first `##`) apply globally; **section-level** (appended to a `##` or a bullet) apply locally. If a directive is impossible, the LoRA falls back to a sensible default rather than failing.

## Known Limitations

While we have made significant progress, Palette must still overcome limitations of a smaller model.[[Numbered list]]

1. Heavy dependence on planner quality -- user needs to work alongside gpt-120b.
2. Struggles with dense slides -- struggle with spatial orientation. 
3. Critic loop is important, but can still fail -- gpt-120b can make mistakes.
4. User directives are really important -- support designer with clear instructions.


