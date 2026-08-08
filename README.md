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

make install                 # creates .venv, installs .[dev] + npm deps
source .venv/bin/activate
```

`make install` creates `.venv` if it is missing, installs the package itself so
the `palette-skill` and `palette-serve` commands exist, and prints a version at
the end — if you do not see one, it did not finish.

<details>
<summary>By hand instead</summary>

```bash
uv venv && source .venv/bin/activate
uv pip install -e '.[dev]'       # one set of quotes; '".[dev]"' fails to parse
npm install
```

`.[dev]` is `.[server]` plus `pytest`. Note `-e .`, not `-r requirements.txt` —
the latter installs the dependencies but not the package, leaving every
`palette-skill …` command as "command not found".

Run this with **no other project's venv active**. A `uv venv` has no `pip` of
its own, so a bare `pip install` lands wherever `PATH` points instead.

</details>

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

**While the deck builds.** Status messages stream into the chat — designer running, slides coding, repair loop, ready. A 10–15 slide deck takes ~3–10 minutes on the default models; the spread is how many geometry repair passes it needs.

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

## Using Palette without the UI (CLI / import)

`palette.py` exposes the same pipeline as importable functions and a CLI, so an
agent or script can drive Palette without running the server. The web UI above
is unaffected. Three commands, used as a loop — **build a plan → confirm →
edit → build the deck**:

```bash
# 1. plan from a request  (add --context "<pasted material>" to ground it)
python palette.py build-plan "Build me a 5-slide deck on RAG" --out plan.md

# 2. revise the plan  (repeat as needed)
python palette.py edit-plan "Make it 3 slides and use a casual tone" --plan plan.md --out plan.md

# 3. render the deck  -> writes deck.pptx into the out dir and prints its path
python palette.py build-deck --plan plan.md --out-dir ./my_deck
```

Add `--json` to any command for a `{"ok": true, ...}` envelope. Or import directly:

```python
from palette import build_plan, edit_plan, build_deck
plan = build_plan("Build me a 5-slide deck on RAG")
plan = edit_plan(plan, "Make it 3 slides and use a casual tone")
result = build_deck(plan, out_dir="./my_deck")   # {"deck", "pptx", "previews", ...}
```

For agent / skill integration, `SKILL.md` documents the commands and the
confirm-before-build workflow.

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

## Running Palette as a local service

`python app.py` is fine for a dev loop, but an agent wants Palette *there*, not
started by hand each time. `palette-skill serve` supervises it:

```bash
make serve-init      # write ~/.config/palette/env, then put your RITS_API_KEY in it
make serve-doctor    # what's missing, per mode — run this first
make serve-start     # start and wait until /health answers
make serve-status    # up? which mode? which workspace?
make serve-logs      # tail
make serve-stop
```

Three backends, and `serve start` picks one unless you pass `--mode`:

| Mode | How it runs | When to use it |
|---|---|---|
| `process` | Detached `python app.py`, pid + log under `~/.local/state/palette` | Default on a machine with the toolchain installed |
| `container` | `docker`/`podman run -d --restart unless-stopped` | No Node/LibreOffice/Poppler needed; identical to Code Engine |
| `launchd` | A LaunchAgent — `make serve-install` | You want it up after every login, restarted on crash |

`serve start` prefers `container` when an image exists and falls back to
`process`. Container mode sets its own restart policy, so launchd is only for
process mode.

**`serve doctor` before anything else.** It checks the checkout, the RITS key,
Node, `pptxgenjs`, LibreOffice, Poppler, the container runtime, and the image,
then tells you which modes are ready and what is blocking the rest — rather
than letting a deck fail three minutes into a build.

### Configuration

One file, `~/.config/palette/env`, mode 600, read by every mode:

```bash
RITS_API_KEY=...                                  # required for any build
PALETTE_HOME=/path/to/project-palette             # process + launchd modes
PALETTE_PORT=18814
PALETTE_WORKSPACE=~/.local/state/palette/workspace
```

The key is deliberately **not** written into the launchd plist — the agent just
points at this file, so the secret has one location and one set of permissions.

`PALETTE_WORKSPACE` matters: by default Palette writes session decks into
`./workspace` inside its own checkout, which is wrong for a long-lived service
and unwritable for a sandboxed caller. The supervisor points it at the state
directory instead.

### Installing the server

```bash
pip install -e '.[server]'      # same deps as requirements.txt, one list
palette-serve --port 18814      # foreground; this is what launchd supervises

# working on the skill rather than just running the server? use [dev] instead —
# it adds pytest on top of [server]:
pip install -e '.[dev]'
```

`requirements.txt` stays for the Dockerfile's cache-friendly two-step build; a
test asserts the two lists never diverge.

---

## Using Palette from an agent

Palette ships as an **agent skill**: a folder with a `SKILL.md` and one helper
script, in the shape [skills.sh](https://skills.sh) installs use. There is no
package to install and nothing generated — the skill tells an agent which
`palette.py` commands to run, and the agent runs them.

```
skills/palette/
├── SKILL.md            what the agent is told
└── scripts/deck.py     starts a long build detached, so a step limit cannot kill it
```

### Install it

```bash
make skill-install CUGA=../cuga-agent-july25   # a CUGA checkout
make skill-install-claude                      # ~/.claude/skills/palette
make skill-package                             # dist/palette-skill.tar.gz
```

A packaged skill is just the folder — `tar xzf palette-skill.tar.gz -C
<skills-root>/` and it is installed, the same as `npx skills add`.

### What the agent needs

| Variable | Why |
|---|---|
| `PALETTE_HOME` | the checkout holding `palette.py`; the skill cannot guess it |
| `RITS_API_KEY` | every model call. A sandbox only sees what its parent exports |

### The workflow the skill enforces

**build-plan → confirm → [edit-plan → confirm] × N → build-deck.** The
confirmation gate is mandatory: a plan is cheap to change and a rendered deck
costs minutes, so the agent never builds a plan the user has not approved.

### Long builds and step limits

Rendering takes three to ten minutes — longer than some hosts allow a single
command to run. Claude Code's `Bash` permits ten minutes, so it calls
`build-deck` directly. CUGA kills a sandbox step at 120 seconds, so the skill
starts the build detached and polls:

```bash
python skills/palette/scripts/deck.py start  --plan plan.md --out-dir ./deck
python skills/palette/scripts/deck.py status --out-dir ./deck   # until "done": true
```

`status` reports `done` only when `deck.pptx` is on disk and large enough to be
real — never because a process exited 0. A build that writes nothing has
failed, and an agent relaying an exit code would announce a deck that does not
exist.

### Keeping the skill honest

`tests/test_skill.py` reads `palette.py`'s argparse setup and fails if the
skill names a command or flag that does not exist. `make hooks` runs it as a
pre-commit guard whenever `palette.py` or the skill changes — so the two cannot
drift without someone noticing.

```bash
make skill-test
```

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

palette_skill/     Agent skill — HTTP client, generated SKILL.md, installer
tests/             Contract tests binding the skill to app.py + config.py
```

---

## Which doc is for what

| Doc | Read it when |
|---|---|
| **this file** | you want to run Palette — install, the web UI, config, containers, deployment |
| [`SKILL.md`](SKILL.md) | you want the agent-facing instructions on their own |
| [`skills/palette/SKILL.md`](skills/palette/SKILL.md) | you are looking at what actually ships to a host, including the long-build path |
| [`skills/palette/scripts/deck.py`](skills/palette/scripts/deck.py) | you need to know how a build survives a step limit |
| `tests/test_skill.py` | you want to see what keeps the skill and `palette.py` in agreement |

## License

See [`LICENSE`](./LICENSE).
