<p align="center">
  <img src="assets/palette_icon.png" alt="" width="110">
</p>

<h1 align="center">Palette</h1>

<p align="center">
  Generate slide decks from a plan you can edit.
</p>

---

Palette is a chat-style harness around a fine-tuned `gpt-oss-20b-palette-lora`. You describe a deck in plain English (or upload reference docs); a stage-1 planner drafts an editable markdown plan; the LoRA turns the plan into per-slide `pptxgenjs` JavaScript; a Node renderer + LibreOffice produce a `.pptx` and a PDF preview you can iterate on slide by slide.

```
       request + refs           plan.md
       ──────────────►  Stage 1  ──────►  Stage 2  ──────►  Stage 3  ───►  .pptx
                       (crafter)         (designer         (geometry
                                          + coder)          detector +
                                                            repair loop)
```

You can:

- **Draft from a request** ("Build a deck about RAG architectures for engineers")
- **Attach reference docs** (PDF, DOCX, PPTX, Markdown) and have them shape the plan
- **Start from an example plan** that ships with the repo
- **Edit the plan freely** as Markdown, then build/regenerate
- **Retry individual slides** that came out poorly, or **type instructions** to refine one specifically
- **Download the `.pptx`** when you're satisfied

---

## Quick start

The whole loop is `clone → install → set key → run`. Five to ten minutes the first time, two minutes on a warm machine.

### 1. Prerequisites

Install once per machine. macOS instructions shown; equivalents work on Linux.

| Tool | Why | macOS install |
|---|---|---|
| Python 3.11+ | the app | `brew install python@3.12` |
| [`uv`](https://docs.astral.sh/uv/) | env + Python deps | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Node 20+ | runs `pptxgenjs` (the renderer) | `brew install node` |
| LibreOffice | converts `.pptx → .pdf` for inline previews | `brew install --cask libreoffice` |
| Poppler | `pdfplumber`'s backend (geometry detector) | `brew install poppler` |
| IBM Plex (font family) | Palette's typography for IBM-styled decks | already ships on macOS — verify with `fc-list \| grep Plex` |

On Debian/Ubuntu: `apt install python3 python3-pip nodejs npm libreoffice poppler-utils fonts-ibm-plex`. The Dockerfile (`Dockerfile` at the repo root) captures the full Linux setup if you'd rather not install locally.

### 2. Clone and install

```bash
git clone <this-repo-url>
cd project-palette

# Python: uv creates an isolated env in .venv and installs deps
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt

# Node: just pptxgenjs
npm install
```

> One-liner alternative: `make install` runs `pip install -r requirements.txt && npm install`. Use this if you've already got an activated env.

### 3. Set your RITS key

Palette talks to IBM's RITS inference service. Export your bearer token before starting:

```bash
export RITS_API_KEY=<your key>
```

Without this, the server starts fine but every build will fail at the first model call.

### 4. Run

```bash
python app.py
#   Palette  ->  http://127.0.0.1:18814
```

Open the URL. You should see the Palette UI — a chat composer on the left, a deck preview area on the right.

---

## Using Palette

**Empty state.** When you first land, the left pane shows an empty-state prompt. Three ways to start:

1. **Describe what you want** in the composer at the bottom (e.g. *"Build a deck explaining vector databases to backend engineers"*) and hit Send.
2. **Attach reference files** via the 📎 button next to the composer, then describe how to use them.
3. **Start from an example plan** — click that button to pick from `reference_plans/` (RAG, all-hands, etc.). Loads a pre-built plan you can edit.

**Plan editing.** Once a plan exists it shows as rendered Markdown in the chat thread. Click **Edit** in the plan card's header to switch to a textarea, make changes, and click **Done**. Then click **Build deck** (the orange button at the bottom of the plan card).

**While the deck builds.** Status messages stream into the chat — designer running, slides coding, repair loop, ready. A 10–15 slide deck takes ~2–4 minutes on the default models.

**Iterating.** When the deck is built:

- Click any slide thumbnail at the bottom of the preview pane to view it large
- Hit **↻ Retry this slide** (the orange button below the slide image) to re-roll just that one slide at a small temperature variation, then re-run the geometry repair on it
- Or type a specific instruction in the chat — *"make slide 5 a table"*, *"swap the order of bullets 2 and 3"* — for surgical edits
- Tweak the plan markdown and hit **Regenerate deck** for whole-deck changes

**Download.** When the deck looks right, click the **Download** button in the header to get the `.pptx`.

**Other controls in the header:**

- **⚙ Models** — pick the model for each pipeline stage (planner / designer + coder / critic). Defaults are sensible.
- **Tips** — opens an in-app cheat sheet of all the above
- **Load plan** — load any `.md` file as a plan directly, skipping the planner
- **New** — start a fresh session

---

## Running in a container (optional)

If you'd rather not install Python / Node / LibreOffice on your machine:

```bash
docker build -t palette .
docker run --rm -p 8080:8080 -e RITS_API_KEY=$RITS_API_KEY palette
# -> http://localhost:8080
```

The Dockerfile installs everything Palette needs — Python, Node, LibreOffice, Poppler, fonts (IBM Plex, Inter, JetBrains Mono, Carlito). ~1.3 GB image, ~5 minutes to build the first time.

---

## Configuration

| Env var | Required | Default | What |
|---|---|---|---|
| `RITS_API_KEY` | **yes** | — | Bearer token for RITS. Without it, builds fail at the first model call. |
| `PORT` | no | `18814` | Bind port. Useful in container deployments where the platform injects a port. |
| `RITS_BASE_URL` | no | (set in `config.py`) | Override only if pointing at a non-default RITS endpoint. |

---

## How it's wired

```
app.py             FastAPI app, routes, per-session logging
ui.py              Single-file HTML/CSS/JS frontend (served at /)
config.py          Paths, model roster, RITS client config

intake.py          Stage 1 — crafter:   request + refs  →  plan.md
pipeline.py        Stage 2 — designer + coder:  plan.md  →  per-slide JS
render.py          Node + pptxgenjs renderer; LibreOffice → PDF previews
detector.py        Geometry defect detector (pdfplumber-based, no model)
refine.py          Stage 3 — detector + editor + verify-gate repair loop

llm.py             RITS HTTP client (custom RITS_API_KEY header)
session.py         Per-thread session state
harness_prompts.py System prompts for crafter / critic / editor
prompts.py         SFT designer + coder prompts (the LoRA's contract)
postprocess.py     Deterministic JS fixes (deck.json normalisation, etc.)

reference_plans/   Example plans (also serve as crafter exemplars)
assets/            Logo, favicons, IBM brand assets used by the renderer
icons/carbon/      Carbon icon library
workspace/         Per-session decks (gitignored, ephemeral)
```

---

## License

See [`LICENSE`](./LICENSE).
