<p align="center">
  <img src="assets/palette_wordmark.png" alt="Palette" width="200">
</p>

# Palette

Generate slide decks from a plan. A chat-style UI turns a request (plus optional reference docs) into an editable markdown plan; the plan is run through a fine-tuned `gpt-oss-20b-palette-lora` to produce a brief and per-slide `pptxgenjs` JavaScript; a Node renderer + LibreOffice produces `.pptx` and a PDF preview.

```
       request  +  refs               plan.md
       ─────────────────►  Stage 1   ─────────►  Stage 2  ─────►  Stage 3
                          (crafter)             (designer        (critic +
                                                  + coder)        editor +
                                                                  geometry
                                                                  repair)
                                                                       │
                                                                       ▼
                                                                  deck.pptx
```

---

## Running locally

### Prerequisites

| Tool | Why | Install (macOS) | Install (Debian/Ubuntu) |
|------|------|------|------|
| Python 3.11+ | the app | `brew install python@3.12` | `apt install python3 python3-pip` |
| Node 20+ | `pptxgenjs` renderer | `brew install node` | `apt install nodejs npm` |
| LibreOffice | `.pptx → .pdf` for previews | `brew install --cask libreoffice` | `apt install libreoffice` |
| Poppler | `pdfplumber` backend | `brew install poppler` | `apt install poppler-utils` |
| IBM Plex, Inter, JetBrains Mono fonts | the LoRA's typography choices | already shipped on macOS / installable from the IBM and rsms.me upstreams | `apt install fonts-ibm-plex fonts-inter fonts-jetbrains-mono` |

### Setup

```bash
git clone <this-repo>
cd project-palette

# Python deps
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Node deps (pptxgenjs)
npm install
```

### Configure the model endpoint

Palette talks to IBM's RITS inference service. Export your key before starting:

```bash
export RITS_API_KEY=<your key>
```

### Run

```bash
python app.py
# -> Palette  ->  http://127.0.0.1:18814
```

Or with the Makefile:

```bash
make install   # pip + npm in one command
make dev       # starts the server on http://localhost:18814
```

Open the URL, drop a request into the input (or pick an example plan from the empty state), and Palette will draft a plan, then build a deck.

---

## Running in a container

Same workflow, fully self-contained:

```bash
docker build -t palette .
docker run --rm -p 8080:8080 -e RITS_API_KEY=$RITS_API_KEY palette
# -> http://localhost:8080
```

The Dockerfile installs Python, Node, LibreOffice, poppler, and the three fonts. ~1.3 GB image.

---

## Deploying to IBM Code Engine

Palette is built to drop into Code Engine as an HTTP application:

1. **Build and push the image** to IBM Container Registry (or any registry Code Engine can pull from):
   ```bash
   ibmcloud cr build --tag us.icr.io/<namespace>/palette .
   ```

2. **Create the Code Engine app**, binding `RITS_API_KEY` as a secret:
   ```bash
   ibmcloud ce application create \
     --name palette \
     --image us.icr.io/<namespace>/palette \
     --port 8080 \
     --env-from-secret rits-key \
     --min-scale 1 --max-scale 1
   ```

3. **Heads-up — workspace is ephemeral.** `workspace/<thread_id>/` is on the pod's local disk; sessions don't survive pod restarts or scaling events. Users should download `.pptx` artifacts before walking away. If you need durable session storage, mount Cloud Object Storage as a workspace backing store — not wired in yet.

4. **Minimum instance config:** 2 vCPU / 4 GB RAM is comfortable. The container peaks during LibreOffice PDF conversion and multi-slide parallel coding.

---

## Configuration

| Env var | Required | Default | What |
|---|---|---|---|
| `RITS_API_KEY` | yes | — | Bearer token for the RITS inference proxy. Without it, builds will fail. |
| `PORT` | no | `18814` | Bind port. Code Engine sets this automatically; locally you can leave it. |
| `RITS_BASE_URL` | no | (set in `config.py`) | Override only if you're pointing at a non-default RITS endpoint. |

---

## How it's wired

```
app.py            FastAPI app, routes, per-session logging
ui.py             Single-file HTML/CSS/JS frontend (served at /)
config.py         Paths, model roster, RITS client config
intake.py         Stage 1 — crafter (request → plan.md)
pipeline.py       Stage 2 — designer + coder (plan → JS per slide)
render.py         Node + pptxgenjs renderer; LibreOffice → PDF
detector.py       Geometry defect detector (pdfplumber-based)
refine.py         Stage 3 — critic + editor + geometry repair loop
session.py        Per-thread session state
harness_prompts.py  System prompts for crafter / critic / editor
prompts.py, postprocess.py  designer + coder SFT prompts and JS fixers
llm.py            RITS HTTP client wrapper

reference_plans/  Example plans (also crafter exemplars; see config.USER_FACING_EXAMPLES)
assets/           Logo, favicons, IBM brand assets used by the renderer
icons/carbon/     Carbon icon library
workspace/        Per-session decks (gitignored, ephemeral)
```

---

## License

See `LICENSE`.
