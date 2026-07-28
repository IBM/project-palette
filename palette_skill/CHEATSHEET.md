# Cheatsheet — tear down, rebuild, test

Every runnable command for the skill, in one place: **reset → build → release
→ install → run → verify.** If you want to *do* something, it is here.

The others explain rather than instruct — [GUIDE.md](GUIDE.md) for what the
skill is and why, [TESTING.md](TESTING.md) for the verification ladder,
[README.md](README.md) for the client's own layout. A map of all of them is in
the repo root README.

Two repos are involved. Throughout:

```bash
PAL=~/Documents/GitHub/project-palette-july25     # the skill is generated here
CUGA=~/Documents/GitHub/cuga-agent-july25         # the skill is consumed here
```

---

## 0. The whole loop, copy-paste

From a working checkout to a deck. Each line is explained in the section noted.

```bash
cd $PAL && source .venv/bin/activate     # every session — else `palette-skill: command not found`
palette-skill serve ensure               # §3 — start the server
palette-skill health                     # §3 — rits_key_set must be true, or stop here
make release                             # §2 — dist/: wheel + one tarball per host
make skill-install CUGA=$CUGA            # §4 — or untar the release instead
cd $CUGA && PALETTE_URL=http://127.0.0.1:18814 uv run cuga start demo_palette   # §5
```

Then on port 7860, ask the **Deck Builder** agent:

> Draft a plan for a Q3 sales review, show it to me, then build it.

Four to twelve minutes, mostly polling. Verify from your own shell (§6):

```bash
cd $CUGA
ls -l cuga_workspace/*/deck/
cat  cuga_workspace/*/deck/.palette-deck.json          # want "stage": "done"
grep -E "draft_async|build_async" ~/.local/state/palette/server.log | tail -2
open cuga_workspace/*/deck/deck.pptx
```

**One thread id across both log lines** means one healthy session. Several
draft ids with no build is the classic failure.

Starting from nothing — no `.venv`, fresh clone, or after a Level 3 reset —
run `make install` first (§1, Level 3).

---

## 1. Reset — pick a level

Each level includes the ones above it. **Start at the lowest that covers what
you actually changed** — the higher levels cost minutes, not seconds.

### Level 0 — nothing (usually right)

Changed only Palette source or skill prose? `make skill-install` overwrites in
place. There is nothing to tear down. Skip to §2.

### Level 1 — soft: drop the installed skill and artifacts

For "rebuild the skill and reinstall it cleanly". Seconds.

```bash
cd $PAL
make skill-uninstall HOST=cuga CUGA=$CUGA
rm -rf dist
```

### Level 2 — medium: also wipe agent + server state

Adds: past deck sessions, the server log, and every thread workspace. Do this
when old runs are confusing your reading of the logs.

```bash
cd $PAL && make clean-state          # stops the service, deletes ~/.local/state/palette
cd $CUGA && rm -rf cuga_workspace    # every thread's sandbox workspace
```

> `clean-state` deletes `server.log` — the ground truth for "did a build ever
> start". Keep it if you are still diagnosing a run.

### Level 3 — clean room: also drop the toolchain

Proves the whole thing from nothing. Costs a few minutes to reinstall.

```bash
cd $PAL
make distclean CUGA=$CUGA        # + uninstalls skills, drops .venv/node_modules/dist
make install                     # creates .venv, installs .[dev] + npm deps
source .venv/bin/activate        # ← easy to forget; nothing below works without it
```

`make install` creates `.venv` if it is missing and installs the package
itself, so `palette-skill` and `pytest` land in `.venv/bin`. It prints
`palette-skill --version` at the end — if you do not see a version, the install
did not finish, whatever else scrolled past.

Doing it by hand instead? **One set of quotes, not two:**

```bash
uv venv && source .venv/bin/activate
uv pip install -e '.[dev]'       # ✓
uv pip install -e '".[dev]"'     # ✗ "Expected package name…, found `\"`"
npm install
```

`.[dev]` includes `.[server]` plus `pytest`. Installing `.[server]` alone leaves
you without a test runner.

> **Never run this with another project's venv active.** `uv venv` creates a
> venv with no `pip` of its own, so a bare `pip install` here resolves to
> whatever is on `PATH` — Homebrew's, or the `(cuga)` env — and puts Palette's
> dependencies somewhere you did not intend. `make install` targets
> `.venv/bin/python` explicitly to avoid that.

### Level 4 — nuke it, both repos

Nothing left anywhere: no service, no state, no installed skill, no artifacts,
no venv, no agent workspaces. Everything below is rebuildable from source.

```bash
# ── Palette ───────────────────────────────────────────────────────────
cd $PAL
git status --short                    # commit anything you want to keep — FIRST
palette-skill serve stop || true      # in case a launchd agent is running
palette-skill serve uninstall || true # remove the launchd agent itself
make distclean CUGA=$CUGA             # skills, state, .venv, node_modules, dist
rm -rf dist                           # if you also want the artifacts gone

# ── CUGA ──────────────────────────────────────────────────────────────
cd $CUGA
rm -rf .cuga/skills/palette           # in case it was untarred rather than installed
rm -rf cuga_workspace                 # every thread's sandbox workspace
rm -rf /tmp/.venv                     # the shared sandbox venv the agent installs into

# ── rebuild ───────────────────────────────────────────────────────────
cd $PAL && make install && source .venv/bin/activate
```

Then §0 from the top.

`/tmp/.venv` is worth knowing about: it is the venv **inside** the agent
sandbox, shared across threads and rebuilt on demand. A stale client there is
invisible from either repo, and it is why an agent can keep running an old
`palette-skill` after you have installed a new one.

**Survives even this:** your `RITS_API_KEY` in `~/.config/palette/env`. It is
the one thing on the machine that cannot be rebuilt from source, so no target
removes it without `PURGE_CONFIG=1` — which backs it up to `/tmp` first:

```bash
make distclean CUGA=$CUGA PURGE_CONFIG=1     # only if you mean it
```

---

## 2. Build and release

Two modes, matching the two reasons you might build.

**Local build** — for your own testing. Overwrites freely, no ceremony:

```bash
cd $PAL
make skill                    # verify the skill matches contract.py + config.py
make release                  # -> dist/  (wheel + one tarball per host)
```

**A real release** — for anyone else. Writes `__version__`, demands a clean
tree, and refuses to reuse a version already in `dist/`:

```bash
make release VERSION=0.2.0
git commit -am 'release 0.2.0' && git tag v0.2.0
```

The version is in every artifact's *filename*, so two builds sharing one cannot
be told apart by whoever you hand them to, and `dist/` overwrites the older
silently. That is why `VERSION` turns the guards on.

`release` depends on `skill` either way, so it refuses to build from a stale
payload.

| Artifact | For |
| --- | --- |
| `palette_skill-0.1.0-py3-none-any.whl` | PyPI, or a direct install |
| `palette-skill-0.1.0-cuga.tar.gz` | drop into a CUGA skills root |
| `palette-skill-0.1.0-claude-code.tar.gz` | drop into `~/.claude/skills/` |
| `palette-skill-0.1.0-generic.tar.gz` | any other host |

Pin a deployment so consumers need no env var:

```bash
make release BASE_URL=https://palette.example.cloud
```

---

## 3. Start the server

```bash
palette-skill serve doctor    # what is missing, per mode
palette-skill serve ensure    # start it, wait for health
palette-skill serve status    # up? which mode? which workspace?
palette-skill serve logs      # tail it
```

First run on a machine: `palette-skill serve init`, then put your key in
`~/.config/palette/env`.

**Check `rits_key_set` before anything else** — if it is false, every build dies
at the first model call and nothing downstream can help:

```bash
palette-skill health
```

---

## 4. Install into CUGA

**From the checkout** — the normal loop while developing:

```bash
cd $PAL && make skill-install CUGA=$CUGA
```

**From a release** — no Palette source required, the way a stranger consumes it:

```bash
cd $CUGA && mkdir -p .cuga/skills
tar xzf $PAL/dist/palette-skill-0.1.0-cuga.tar.gz -C .cuga/skills/
```

Either way, confirm what landed:

```bash
cd $PAL && make skill-status CUGA=$CUGA
```

### Pointing at a Palette that runs elsewhere

Code Engine, a shared box, a colleague's machine — the skill is unchanged, only
the URL differs. **Skip §3 entirely**: no `serve` commands, no Node, no
LibreOffice, no RITS key. That is the service owner's problem, not yours.

*Pin it into the skill* — best when a team shares one deployment, because every
agent then works with no environment set at all:

```bash
cd $PAL
make release BASE_URL=https://palette.example.cloud       # into the artifact
# or, into an install straight from the checkout:
.venv/bin/python -m palette_skill.install \
  --into $CUGA --base-url https://palette.example.cloud
```

*Or set it per run* — best when the URL varies by person or session:

```bash
PALETTE_URL=https://palette.example.cloud uv run cuga start demo_palette
```

`$PALETTE_URL` wins over a pinned URL either way. Why you would choose one over
the other, and what a pinned artifact stops telling the agent, is in
[GUIDE.md](GUIDE.md) §5b.

---

## 5. Run it

```bash
cd $CUGA
PALETTE_URL=http://127.0.0.1:18814 uv run cuga start demo_palette
```

Port 7860 → **Deck Builder** agent. Ask:

> Draft a plan for a Q3 sales review, show it to me, then build it.

**Four to twelve minutes**, mostly polling — that is normal. The preset raises
two settings for you (`setdefault`, so your own env still wins):

| Setting | Default | Preset | Why |
| --- | --- | --- | --- |
| `sandbox_execution_timeout` | 30s | 120s | Each poll is a step; at 30s a long build costs twenty-odd |
| `cuga_lite_nl_auto_continue` | false | true | A prose progress note would otherwise read as a final answer |

---

## 6. Verify — believe the files, not the chat

The agent should hand you an absolute path and point at the **Files** panel. To
check independently, from `$CUGA`:

```bash
ls -l cuga_workspace/*/deck/                    # deck.pptx, slide-01.png …
cat  cuga_workspace/*/deck/.palette-deck.json   # "stage": "done"
```

> Already inside `cuga_workspace`? Drop the prefix: `ls -l */deck/`.

Three independent signals, in increasing strength:

```bash
# 1. the orchestrator ran (this file is written by `palette-skill deck`, nothing else)
cat cuga_workspace/*/deck/.palette-deck.json

# 2. one session carried both stages — several draft ids with no build is the classic failure
grep -E "draft_async|build_async" ~/.local/state/palette/server.log | tail -2

# 3. it came out of Palette's renderer (IBM Plex is forced there, and nowhere else)
unzip -p cuga_workspace/*/deck/deck.pptx ppt/slides/slide1.xml | grep -c "IBM Plex"
```

Open it:

```bash
open cuga_workspace/*/deck/deck.pptx
```

---

## 7. Automated instead of by hand

```bash
cd $PAL  && uv run --no-project pytest tests/ -q          # contract, client, docs, release
cd $CUGA && uv run pytest tests/unit/test_demo_palette_preset.py -q
cd $CUGA && PALETTE_DECK_OUT=~/Desktop/decks \
            uv run pytest tests/e2e/skills/test_palette_deck_e2e.py -m e2e -q -s
```

The e2e run drives a real model against a real server and produces real `.pptx`
files. Minutes, not seconds. **Do not pipe it through `head`** — SIGPIPE kills
pytest partway and it looks like a failure that never happened.

---

## When it goes wrong

| Symptom | Cause | Fix |
| --- | --- | --- |
| `version must look like 1.2.3` | `VERSION` is not X.Y.Z | `make release VERSION=0.2.0` |
| `working tree is dirty` | releasing uncommitted work | commit or stash; the artifact records a commit that must describe it |
| `0.2.0 is already built` | that version is in `dist/` | bump `VERSION` — never reship a version under new bytes |
| Agent runs an old `palette-skill` after you reinstalled | stale client in the sandbox venv | `rm -rf /tmp/.venv` (§1 Level 4) |
| `palette-skill: command not found` | `.venv` not activated, or `make install` never ran | `source .venv/bin/activate`, else `make install` |
| `error: Failed to parse: ".[dev]"` | doubled quotes — the shell kept the inner pair | `uv pip install -e '.[dev]'` |
| `make install` → `Error 1` | no venv, or a foreign one active | `deactivate`, then `make install` from `$PAL` |
| Deps landed in the wrong environment | bare `pip` resolved outside `.venv` | `make install` — it targets `.venv/bin/python` explicitly |
| `pytest: command not found` | installed `.[server]`, which has no test deps | `uv pip install -e '.[dev]'` |
| `no matches found: cuga_workspace/*/...` | you are already inside `cuga_workspace` | drop the prefix: `*/deck/` |
| Agent reports `deck/deck.pptx` and you cannot find it | stale skill — the relative path predates `pptx_path` | `make skill-install CUGA=$CUGA` |
| Agent drives `start-draft` / `wait-draft` separately | stale skill; `deck` supersedes those | `make skill-install CUGA=$CUGA` |
| Several `draft_async` ids, no `build_async` | agent lost the session (pre-`deck` behaviour) | `make skill-install CUGA=$CUGA` |
| Run stops mid-build, "shall I keep polling?" | `cuga_lite_nl_auto_continue` off | use `demo_palette`, or export it true |
| Every build fails instantly | `rits_key_set: false` | key into `~/.config/palette/env`, `serve restart` |
| `PaletteUnavailable` on a `https://` URL | remote deployment down or wrong URL | confirm the URL; **do not** `serve ensure` — that starts a second, local server |
| `make skill-check` fails | payload drifted from `contract.py`/`config.py` | `make skill-build` |
| Deck built but agent claims otherwise | it stopped before the last poll | call `palette-skill deck --dest ./deck` yourself; it resumes |

---

## Where things live

| Path | What |
| --- | --- |
| `$CUGA/.cuga/skills/palette/` | the installed skill the agent reads |
| `$CUGA/cuga_workspace/<thread>/deck/` | the agent's copy — pptx, previews, plan, state |
| `~/.local/state/palette/workspace/<tid>/` | the server's copy — also `deck.pdf`, `deck.json`, `output_js/` |
| `~/.local/state/palette/server.log` | ground truth for what actually ran |
| `~/.config/palette/env` | `RITS_API_KEY`; survives every reset level |
| `$PAL/dist/` | release artifacts |

The two deck copies are not a mistake. The server builds on the host, where
Node and LibreOffice live; `palette-skill deck` downloads the result into the
sandbox over HTTP. When a slide looks wrong, the server copy has the extras
worth reading.
