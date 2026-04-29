# deck_forge

Conversational slide-builder backing the Palette agent. Uses gpt-oss-120b (RITS) to plan, research, and design each slide as `pptxgenjs` JavaScript executed via Node.

## Run

```
export RITS_API_KEY=...
export TAVILY_API_KEY=...   # optional; without it, evidence stage degrades to "(illustrative)"
uv run --with-requirements requirements.txt python main.py
```

UI at http://127.0.0.1:18814.

## Pipeline

1. `plan_deck(topic)` — reflection (genre, audience, content needs) → blueprint (per-slide content brief, JSON).
2. User confirms.
3. `build_deck()` — for each blueprint slide:
   - Web-search any flagged `evidence_needed`, distill to `{confidence, evidence, sources}`.
   - Ask gpt-oss for pptxgenjs JS designing this slide.
   - Stitch all per-slide JS into a Node runner, execute, render `deck.pptx`.
   - Convert each slide to PNG (LibreOffice + pdftoppm) for the preview pane.

## Requirements

- Python 3.10+
- Node.js (for pptxgenjs); local `node_modules` lives in this directory.
- LibreOffice (`soffice`) and `poppler` (`pdftoppm`) on PATH for PNG previews.
