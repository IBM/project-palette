---
name: palette
description: >-
  Create, revise, and render slide decks (PowerPoint .pptx) from a
  natural-language request or pasted material. Use whenever the user wants to
  build a presentation / deck / slides, turn notes or content into slides, or
  change a deck. Works in two reviewable steps — a markdown plan the user
  approves, then a rendered .pptx.
---

# Palette — slide deck builder

Palette turns a request (or pasted material) into a rendered slide deck in two
reviewable steps:

1. **Plan** — a markdown outline of the deck, which the user reviews and approves.
2. **Deck** — a rendered PowerPoint `.pptx`, built from the approved plan.

**Always let the user approve the plan before building the deck.** The plan is
the cheap place to iterate; the deck build renders every slide and takes a few
minutes, so you don't want to build from an unapproved plan.

## Commands

Run these from the Palette harness directory (where `palette.py` lives). Each
command prints its result to stdout and, on failure, prints `error: ...` and
exits non-zero — relay that message to the user.

Two environment variables have to be set, and a missing one is not obvious
from the failure it causes:

| Variable | Why |
|---|---|
| `RITS_API_KEY` | every model call. Without it, each command fails at the first one |
| `PALETTE_HOME` | the checkout holding `palette.py`. Not needed here, where you are already in it, but the packaged skill (`skills/palette/`) shells in from elsewhere and cannot guess it |

```bash
export PALETTE_HOME=/path/to/project-palette
export RITS_API_KEY=<key>
```

### 1. Create a plan
```
python palette.py build-plan "<the user's request>" --out plan.md
```
- Pass the user's request through **as-is** — do not reformat or restructure it.
- If the user pasted **material for the deck** (notes, content, data, an excerpt),
  add it with `--context` so the plan is grounded in it:
  ```
  python palette.py build-plan "<request>" --context "<the pasted material>" --out plan.md
  ```
- Output: the markdown plan (also saved to `plan.md`).

### 2. Revise a plan
```
python palette.py edit-plan "<the change the user asked for>" --plan plan.md --out plan.md
```
- Use for **any** change to the plan: slide count, tone, wording, content,
  adding or removing a slide, changing a specific slide.
- Pass the user's requested change through as-is.
- Output: the full revised plan.

### 3. Build the deck (renders the .pptx)
```
python palette.py build-deck --plan plan.md --out-dir <output_directory>
```
- Runs the full pipeline and writes `deck.pptx` (plus `deck.json` and slide
  preview PNGs) into `<output_directory>`.
- Output: the path to the rendered `.pptx` on stdout.
- **This is slow (~minutes)** — tell the user it's building before you run it.
- Optional: `--palette-family <style>` sets the visual style (default
  `ibm_watsonx`). Only use it if the user asks for a specific look.

## Workflow

The loop is **build-plan → confirm → [edit-plan → confirm] × N → build-deck**.

1. **User asks for a deck** → run **build-plan** with their request (add
   `--context` if they pasted material). Save the plan.
2. **Confirmation gate (required).** Present the plan to the user and explicitly
   ask them to confirm before anything is built — for example:
   > Here is the plan for your deck. Does this look right? Reply **yes** to
   > build the deck, or tell me what you'd like to change.

   **Never call build-deck until the user has confirmed this plan.**
3. **Branch on the user's reply:**
   - **Confirms** ("yes", "looks good", "go ahead", "build it") → run
     **build-deck**, then give the user the `.pptx` path it returned.
   - **Asks for changes** ("no…", "change…", "add a slide about…") → run
     **edit-plan** with their instruction, then **return to step 2** — present
     the revised plan and ask for confirmation again.
4. Repeat until the user confirms, then build.

## Guidance

- **Keep your job simple.** Pass the user's words straight through to
  `build-plan` / `edit-plan`. The tools do the parsing, grounding, and
  formatting — don't pre-process the request yourself.
- **The confirmation gate is mandatory.** Always present the plan and get the
  user's explicit confirmation before running `build-deck` — never build an
  unconfirmed plan, and never jump straight from the request to `build-deck`.
- **Presentation context, not upload.** This host may not support file uploads.
  If the user pastes their material into the chat, pass it via `--context` — it
  is used as the source material the deck is built from (shaped into slides, not
  copied verbatim).
- **Report the path.** After `build-deck`, the user finds their deck at the
  `.pptx` path the command printed. State that path clearly.
- **For programmatic parsing**, add `--json` to any command to get a JSON
  envelope (`{"ok": true, ...}`) instead of raw text.
