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

Palette ships as an **agent skill**: a `SKILL.md` plus a dependency-light HTTP
client. An agent installs the client, calls the same API the web UI calls, and
gets back a `.pptx` — no LibreOffice, Node, or fonts on the agent's side.

```bash
make skill                                  # verify the skill matches this repo
make skill-install CUGA=~/code/cuga-agent   # install into an agent's skills root
make skill-status  CUGA=~/code/cuga-agent   # is that copy still current?
make release                                # shippable artifacts in dist/
```

`make release` produces a wheel plus a self-contained tarball per agent host.
A consumer untars it into their skills root — no Palette checkout, no network,
no build step. Add `BASE_URL=https://…` to pin a deployment into the artifact.

Then point the agent at a server:

```bash
export PALETTE_URL=https://<your-palette-host>
```

### With CUGA

CUGA ships a preset that starts a supervisor agent with this skill loaded — a
*Deck Builder* rather than a generic skills demo. Ask it for a deck and it
drives one resumable command that owns the session, the plan, the polling and
the download, and reports completion by stat-ing the files rather than by
believing anything. That shape exists because an agent driving the underlying
calls by hand loses the session on a retry and reports a deck it never built.

The agent detects the server on startup and, if it is down, hands the user a
`palette-skill serve` command rather than trying to start one itself.

**Commands:** [palette_skill/CHEATSHEET.md](palette_skill/CHEATSHEET.md) — §0 is
the whole loop in six lines, §6 is how to tell a real deck from a reported one.
**Concepts:** [palette_skill/GUIDE.md](palette_skill/GUIDE.md).

### Why it can't silently drift

The skill is **generated from this repo and verified against it**, never
hand-written on the agent side:

| Moving part | What keeps it honest |
|---|---|
| Routes | `tests/test_skill_contract.py` walks `app.py`'s decorators. A new or renamed route fails the suite. |
| Request fields | `BuildReq` / `EditReq` / `draft(...)` are compared field-by-field against `palette_skill/contract.py`. |
| Model menus | The model table in `SKILL.md` is rendered from `config.py`. Add a model, `make skill-check` goes red. |
| Example plans | Rendered from `config.USER_FACING_EXAMPLES`, intersected with what is on disk. |
| Installed copy | `.palette-skill.json` records commit, version, and per-file hashes. `make skill-status` names what moved. |

So the loop is: change Palette → `make skill` tells you if the skill is stale →
`make skill-install` ships it. Nothing is reconstructed by hand.

The skill folder is **copied**, not symlinked, on purpose: `Path.rglob` stopped
following directory symlinks in Python 3.13, so a symlinked skill would be
discovered on 3.12 and silently vanish on an interpreter upgrade.

### Background builds

A deck takes three to ten minutes; agent sandboxes routinely kill a step after
30 seconds. `POST /build_async` starts a build and returns immediately, so the
caller polls `/progress` and reads `/result` across several short steps. The
blocking `POST /build` is unchanged, and `PaletteClient` feature-detects which
one a deployment has via `/openapi.json` — so the skill works against an older
deployment too, just without progress reporting.

See [`palette_skill/GUIDE.md`](palette_skill/GUIDE.md) for what the skill is and
how to build it, [`palette_skill/README.md`](palette_skill/README.md) for the client,
[`palette_skill/payload/SKILL.md`](palette_skill/payload/SKILL.md) for what the
agent is actually told, and [`palette_skill/TESTING.md`](palette_skill/TESTING.md)
for how to verify the whole chain end to end.

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

palette_skill/     Agent skill — HTTP client, generated SKILL.md, installer
tests/             Contract tests binding the skill to app.py + config.py
```

---

## Which doc is for what

Palette's own docs are this file. Everything about the **agent skill** lives
under [`palette_skill/`](palette_skill/), split by what you are trying to do
rather than by topic — so a command appears in exactly one of them.

| Doc | Read it when | Contains |
|---|---|---|
| **this file** | you want to run Palette itself | install, the web UI, config, containers, deployment |
| [`palette_skill/CHEATSHEET.md`](palette_skill/CHEATSHEET.md) | **you want to do something now** | every runnable command: reset levels, build, release, install, run, verify, and a symptom→fix table |
| [`palette_skill/GUIDE.md`](palette_skill/GUIDE.md) | you are changing the skill | what a skill *is*, the architecture diagram, what each `make` target produces, when to cut a release |
| [`palette_skill/TESTING.md`](palette_skill/TESTING.md) | you want to trust it | nine tiers from "no dependencies" to "a real model builds a real deck", and what each failure looks like |
| [`palette_skill/README.md`](palette_skill/README.md) | you are reading the code | why the client is shaped this way, module-by-module layout |
| [`palette_skill/payload/SKILL.md`](palette_skill/payload/SKILL.md) | you want to know what the agent is told | the instructions themselves — largely generated, so read it rather than editing the generated regions |
| [`palette_skill/payload/reference.md`](palette_skill/payload/reference.md) | you are calling the client from Python | full signatures, error types, the CLI surface |
| [`docs/skill-handbook.html`](docs/skill-handbook.html) | you want the whole picture in one page | build → release → update → consume → verify, with the failure modes that shaped it. A standalone page — open it locally, or serve it as-is. |

The handbook is self-contained — no CDN, no fonts to fetch, no build step — so
`python -m http.server -d docs` or dropping it on any static host both work.
It is generated from `docs/skill-handbook.body.html` by `make handbook`; edit
the fragment, not the page, and a test fails the build if the two diverge.

**Start at the cheatsheet.** §0 is six lines from a working checkout to a deck.
The others explain *why*; it is the only one that tells you *what to type*.

That split is deliberate. These instructions used to appear in four to seven
documents each, and they drifted — one described a `make` target that had been
changed an hour earlier. Runnable sequences now live in the cheatsheet alone,
and a test fails the build if another doc grows one.

---

## License

See [`LICENSE`](./LICENSE).
