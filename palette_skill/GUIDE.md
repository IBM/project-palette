# The Palette skill

What it is, what the build produces, and how to get from an empty machine to an
agent that builds you a deck.

> **Before anything else:** `palette_skill/`, `tests/`, `scripts/`, `.github/`
> and `pyproject.toml` are **untracked**. A `git clean -fd` deletes all of it.
> Commit them before you go anywhere near a clean-up command.

---

## 1. What a skill is

A skill is a folder an agent can open when a task matches it. Not a plugin, not
an API integration — a **markdown file with a description**, plus whatever files
the workflow needs.

The mechanism is the same on every host that supports them:

1. At startup the host scans a skills directory and reads each `SKILL.md`'s
   frontmatter. Only the one-line `description` goes into the model's prompt.
2. When a task matches that description, the model asks for the skill by name.
3. The host hands back the **full markdown body**, which becomes the model's
   instructions for that task.
4. Companion files land where the agent can reach them.

The economics are the point. A description costs ~60 tokens on every turn; the
body costs nothing until it is needed. You can ship twenty capabilities without
paying for twenty playbooks on every message.

**The description is the whole routing decision.** If it does not contain the
words a user would actually say, the skill is never opened, however good the
body is.

## 2. What *this* skill is

Palette is a deck builder: a FastAPI service wrapping a three-stage model
pipeline, plus Node, LibreOffice, Poppler and IBM Plex. None of that can live
inside an agent sandbox.

So the skill is **not** a copy of Palette. It is:

- **instructions** for driving Palette's HTTP API, and
- **a thin client** (`httpx` and nothing else) that ships with them.

The agent installs the client, calls the service, and gets back a `.pptx`. The
heavy machinery stays on the server, where it already works.

```
   CUGA / Claude Code                 Palette service
   ┌──────────────────┐               ┌──────────────────────┐
   │ reads description│               │ Stage 1  crafter     │
   │ opens SKILL.md   │  ── HTTP ──►  │ Stage 2  designer    │
   │ runs palette-skill               │ Stage 3  geometry fix│
   │ writes ./deck/   │  ◄── .pptx ── │ Node + LibreOffice   │
   └──────────────────┘               └──────────────────────┘
```

The only coupling is the HTTP contract. Palette's pipeline can be rewritten
without the skill noticing.

## 3. The parts

| File | Role |
|---|---|
| `contract.py` | Every endpoint, written once. The client, the docs and the tests all read this. |
| `client.py` | `PaletteClient` — the API surface, including bounded polling. |
| `cli.py` | `palette-skill`, one JSON object per command. |
| `hosts.py` | Host profiles. The only host-specific part of the whole skill. |
| `service.py` | Host-side supervisor: start/stop/health/doctor for a local Palette. |
| `server_entry.py` | `palette-serve` — runs the server in the foreground. |
| `build_skill.py` | Renders the generated regions of the payload. |
| `install.py` | Installs, checks for drift, uninstalls. |
| `release.py` | Builds the shippable wheel and per-host tarballs. |
| `payload/SKILL.md` | What the agent is actually told. |
| `payload/reference.md` | Signatures and error types, loaded on demand. |

## 4. What each command produces

### `make skill-build` → `palette_skill/payload/`

Rewrites five fenced regions inside the two markdown files. Everything outside
the fences is hand-written and never touched.

| Region | Rendered from | Why it is generated |
|---|---|---|
| `execution` | `hosts.py` | Tool names and step limits differ per host |
| `endpoints` | `contract.py` | The API table, with CLI and Python for each route |
| `defaults` | `contract.py` | Base URL, auth env var, terminal stages |
| `models` | `config.py` | The three model menus the server will actually accept |
| `examples` | `config.py` | Curated plans, intersected with what is on disk |

Nothing else changes. It writes no code and touches nothing outside `payload/`.

### `make skill-check` → nothing, an exit code

Re-renders in memory and compares. Exit 1 if a region is stale, meaning
Palette's code moved and the skill did not.

### `make skill-test` → nothing, an exit code

**The full suite.** Walks `app.py`'s routes and request models, `config.py`'s
rosters, and the payload, asserting the skill still describes this server. Also
checks the client declares no runtime dependency beyond `httpx`, since that
wheel ships into every agent sandbox.

### `make skill` → both of the above

The one to run after every Palette change. About a second.

### `make skill-install CUGA=<path>` → an installed directory

Regenerates, builds a wheel, and writes:

```
<agent>/.cuga/skills/palette/
├── SKILL.md                                    what the agent is told
├── reference.md                                loaded on demand
├── vendor/palette_skill-0.1.0-py3-none-any.whl the client, httpx-only
└── .palette-skill.json                         host, version, commit, per-file hashes
```

The wheel is what lets the agent install the client offline — no git access, no
registry auth. The manifest is how `skill-status` knows which side moved.

`--host claude-code` renders a different `execution` region (`Bash` instead of
`run_command`, no step-limit guidance) and installs to `~/.claude/skills/palette`.

### `make skill-status CUGA=<path>` → a drift report

Compares the installed copy, the current sources and the current payload, and
names whichever moved. Re-renders for the host recorded in the manifest, then
restores the payload, so checking one host never disturbs another.

### `make skill-uninstall` / `clean-state` / `distclean`

Removal, in three sizes. `skill-uninstall` refuses any directory without our
manifest, so a mistyped `--skills-root` cannot delete your data. **None of them
touch `~/.config/palette/env`**, which holds your RITS key — `PURGE_CONFIG=1` is
the explicit opt-out and it backs the file up first.

---

## 5. Start fresh, build the skill, use it

Both repos are siblings in `~/Documents/GitHub/`. Every command runs from the
Palette repo unless stated.

### Step 1 — wipe

```bash
cd ~/Documents/GitHub/project-palette-july25
git status --short          # commit anything untracked FIRST
make distclean CUGA=../cuga-agent-july25
```

Removes both installed skills, `~/.local/state/palette`, the running service,
`.venv`, `node_modules`, `build`, `*.egg-info`. Keeps both repos and your key.

### Step 2 — rebuild

```bash
uv venv
uv pip install -e '.[dev]'     # NOT .[server] — [dev] adds pytest on top
npm install                    # pptxgenjs; the renderer fails without it
make hooks                     # pre-commit guard
```

`[server]` is for a box that only runs the service. Install that and
`make skill-test` fails on a missing pytest — and adding pytest to
`[project.dependencies]` to fix it correctly trips
`test_client_runtime_is_httpx_only`, because that wheel ships to every sandbox.

`npm install` is not optional. Without `pptxgenjs` the renderer fails **three
minutes into a build**, not at startup.

### Step 3 — build the skill

```bash
make skill-build      # writes payload/SKILL.md + reference.md
make skill            # all green ← the skill is ready
```

Inspect what will ship:

```bash
head -6 palette_skill/payload/SKILL.md    # the description a model routes on
```

### Step 4 — start Palette

```bash
palette-skill serve init      # first time only; writes ~/.config/palette/env
$EDITOR ~/.config/palette/env # set RITS_API_KEY=...

palette-skill serve doctor    # want: can_build true, process ready
palette-skill serve ensure    # start, wait for /health
```

Read `doctor` before continuing. `container: blocked` is fine unless you ran
`make docker-build`. `can_build: false` means the key is not visible.

### Step 5 — install into CUGA

```bash
make skill-install CUGA=../cuga-agent-july25
make skill-status  CUGA=../cuga-agent-july25    # "in sync (…, host cuga, …)"
```

Confirm CUGA sees it — no LLM or server needed:

```bash
cd ../cuga-agent-july25
uv sync
uv run pytest tests/e2e/skills/test_palette_skill_invocation.py -q   # 6 passed
```

### Step 6 — use it

```bash
PALETTE_URL=http://127.0.0.1:18814 uv run cuga start demo_palette
```

Open the demo on port 7860. The **Deck Builder** agent appears with `palette` in
the skills panel. Ask:

> Build me a deck about vector databases for backend engineers.

Expect `load_skill("palette")` → install wheel → `start-draft` → poll →
`start-build` → poll ~10× → download. **Five to seven minutes**, mostly polling.
That is normal, not a stall.

### Step 7 — check it actually built something

The agent's `./` is the sandbox workspace, not your shell's:

```bash
ls -l cuga_workspace/*/deck/
```

An agent can report a deck it never built. Ask Palette, not the chat:

```bash
ls -td ~/.local/state/palette/workspace/*/ | head -1 | xargs ls -l
```

A real build leaves `deck.pptx`, `deck.pdf`, `slide-*.png`, `deck.json` and
`output_js/`. **Only `session.log` means a draft ran and no build followed.**

---

## 5b. When Palette is deployed elsewhere

If Palette already runs somewhere — Code Engine, a shared box, a colleague's
machine — the skill is unchanged. It is an HTTP client; only the URL differs.

**Skip entirely:** `serve init` / `doctor` / `ensure`, `npm install`,
LibreOffice, Poppler, Node, IBM Plex, and the RITS key. All of that belongs to
whoever runs the service. You do not need `.[server]` either.

**Two ways to point at it.**

*Pin it into the skill* — best when a team shares one deployment, because every
agent then works with no environment set at all:

```bash
uv pip install -e '.[dev]'        # only the client + tests
make skill-build
python -m palette_skill.install \
  --into ../cuga-agent-july25 \
  --base-url https://palette.example.cloud
```

The generated `defaults` region then reads *"Base URL —
`https://palette.example.cloud`. This skill was built for that deployment"*, and
the URL is recorded in the manifest so `--check` compares like with like.

*Or set it at run time* — best when the URL changes per person or per session:

```bash
make skill-install CUGA=../cuga-agent-july25
PALETTE_URL=https://palette.example.cloud uv run cuga start demo_palette
```

`$PALETTE_URL` wins over a pinned URL either way.

**What the agent does when it cannot reach a remote deployment.** It reports the
URL and the error and asks you to confirm it — and specifically does *not*
suggest `palette-skill serve ensure`, because that would start a second, local
Palette with none of your data or models. The skill branches on whether the URL
is loopback, and a test pins that behaviour.

**What you still need the repo for.** The wheel is built from source at install
time, so installing the skill needs a Palette checkout even when the service is
remote. It does not need the server dependencies — `.[dev]` is enough. Making a
deployment serve its own matching skill would remove that last tie; it does not
exist yet.

---

## 5c. Shipping it to people who never clone Palette

```bash
make release                                     # dist/
make release BASE_URL=https://palette.example.cloud
```

Produces four artifacts:

| Artifact | For |
|---|---|
| `palette_skill-<v>-py3-none-any.whl` | PyPI, or a direct `pip install` |
| `palette-skill-<v>-cuga.tar.gz` | drop into a CUGA skills root |
| `palette-skill-<v>-claude-code.tar.gz` | drop into `~/.claude/skills/` |
| `palette-skill-<v>-generic.tar.gz` | any other host |

Each tarball is a complete skill folder — `SKILL.md`, `reference.md`, the client
wheel, and a manifest — rendered for that host. **~72 KB, no network, no build
step, no Palette source required.**

### Consuming it, the way skills.sh skills are consumed

```bash
mkdir -p .agents/skills
tar xzf palette-skill-0.1.0-cuga.tar.gz -C .agents/skills/
```

That is the whole install. Set `[skills] root = "agents"` in CUGA's
`settings.toml` and Palette sits alongside anything you added with
`npx skills add`, discovered the same way.

> CUGA scans **one** skills root. If you use `npx skills add ... -a universal`
> (which writes `.agents/skills/`) and also install Palette under
> `.cuga/skills/`, only one of the two is ever seen. Put them in the same root.

Or, from an index:

```bash
pip install palette-skill
python -m palette_skill.install --host cuga --skills-root .agents/skills
```

The wheel carries the payload as package data, so this needs no checkout
either. There is no vendored wheel in this mode, and the generated SKILL.md
adapts — it tells the agent `uv pip install palette-skill` rather than pointing
at a `vendor/` directory that would not exist.

### Pin the URL, or don't — what changes

`make release` and `make release BASE_URL=…` produce artifacts that differ in
two places, both generated:

| | unpinned | `BASE_URL=https://…` |
|---|---|---|
| Base URL | `$PALETTE_URL`, else `http://127.0.0.1:18814` | the pinned URL; `$PALETTE_URL` still overrides |
| If unreachable | branches: local → `serve` commands, remote → report and ask | report and ask only; **no local commands at all** |

The second row is the one that matters. A remote-pinned artifact deliberately
ships **no** `serve doctor` / `serve ensure` guidance, because following it
would start a *second, local* Palette with none of the user's data or models.
Pinning `127.0.0.1` still counts as local and keeps them.

So:

- **Deployed Palette** → `make release BASE_URL=https://…`. Consumers need no
  environment variable and no local install. Nothing to keep running.
- **Local Palette** → `make release` unpinned. Every consumer must have the
  service up before asking for a deck — `palette-skill serve ensure`, or
  `serve install` for a launchd agent that survives login. The skill tells them
  exactly that when a call fails.
- **Mixed** → ship unpinned and set `PALETTE_URL` per environment. It wins over
  a pinned URL either way.

### A release is pinned to a Palette version

The model menus and example plans inside `SKILL.md` are rendered from the
checkout's `config.py` and then **frozen**. A skill advertising
`palette-qwen-32b` has to ship with a server that serves it, so the artifact
version tracks Palette rather than the client. Everything else — the execution
section, the endpoint table — is re-rendered per host at build time.

---

## 6. Day to day, once it works

```bash
make skill                                    # after every Palette change
make skill-install CUGA=../cuga-agent-july25  # ship it
make skill-status  CUGA=../cuga-agent-july25  # has either side moved?
```

The pre-commit hook runs the first of those automatically when you stage
`app.py`, `config.py`, `session.py`, `requirements.txt`, `pyproject.toml` or
`palette_skill/`.

See [TESTING.md](TESTING.md) for the full eight-tier verification ladder, and
[payload/SKILL.md](payload/SKILL.md) for what the agent is actually told.
