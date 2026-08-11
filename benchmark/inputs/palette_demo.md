# Project Palette - Update Deck

Audience: project sponsors, product leadership, and the IBM AI research team - readers need a concise status brief and next-step outlook.

Preferences:
- Tone: factual, measured - a status readout.
- Sub-brand: base IBM.
- Length: 5 slides

## Cover
- Project Palette - update deck

## Background - the recap
- v2 (May) - crafted ~250 training decks; fine-tuned a LoRA on gpt-oss-20b
- Result - functional but generic visual style
- v3 - three actions:
  1. Distilled IBM deck design guidelines (palette, typography, layouts, voice)
  2. Built ~1,000 training decks (mix of normal-aesthetic and IBM-style)
  3. Added user-directive handling for inline visual control

## Deployment options
[[Render as a comparison cards]]
- Web UI - access via http://9.47.166.122:18814/; author plan, generate deck, download PPTX
- Local install - `git clone https://github.com/IBM/project-palette/`; `make install`; run `python app.py` (same LoRA endpoint)

## Benchmark results - PresentBench ranking
[[Render as a bar chart]]
  - Palette - 59.7
  - NotebookLM - 60.8
  - Manus - 55.5
  - Tiangong - 53.3
  - PPTAgent v2 - 49.6
  - Qwen - 33.9
- Position: #2 overall, 1 pt behind NotebookLM, 4 pts ahead of Manus, best open-source system (+10 pts over PPTAgent v2)

## Next steps - focus areas
[[Numbered list]]
- Expand training deck diversity to cover emerging IBM layout patterns
- Refine LoRA to improve visual differentiation beyond "Claude-flavored" style
- Gather user feedback on directive handling to prioritize new visual controls
- Target #1 ranking on next PresentBench release (aim > 61 score)