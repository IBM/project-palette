# Palette skill

Ask your coding agent for a slide deck; get a plan to approve, then a
rendered `.pptx` in IBM's design language.

> **You:** Build me a 3-slide deck explaining prompt caching to backend engineers.
> **Agent:** *(writes a plan)* Here's the plan — want changes, or shall I build it?
> **You:** Looks good, build it.
> **Agent:** *(3–5 minutes later)* Done — `deck/deck.pptx`, 3 slides.

Works in any agent that loads `SKILL.md` skills — tested in **Claude Code**
and **IBM Bob**; also CUGA and others supported by
[`npx skills`](https://skills.sh).

---

## Pick a mode

New here? The step-by-step install guide is [`../README.md`](../README.md).


The skill is a small folder (`SKILL.md` + `scripts/deck.py`) that drives
Palette, which does the actual work. One variable decides where Palette runs:

| | **Option A — Local** (`PALETTE_HOME`) | **Option B — Remote** (`PALETTE_URL`) |
|---|---|---|
| Palette runs on | your machine (a checkout) | a shared Palette server |
| You install | Palette + Node + LibreOffice, then the skill | the skill, nothing else |
| Setup time | ~15 minutes | 1 minute |
| Best for | developing Palette, or working offline from the server | using the skill |

If both are set, `PALETTE_URL` (Option B) wins. Same commands, same results either way.

---

## Option A — Local install

### 1. Set up Palette (once per machine)

```bash
git clone https://github.com/IBM/project-palette.git && cd project-palette
brew install node poppler && brew install --cask libreoffice   # renderer + previews
make install                                                    # Python + Node deps into .venv
cp .env.example .env                                            # model settings — see below
echo "export PALETTE_HOME=$PWD" >> ~/.zshrc                     # tell the skill where Palette is
```

In `.env`, fill in the **model backends** (ask a Palette maintainer for values):

| Setting | For |
|---|---|
| `PALETTE_CE_BASE_URL`, `PALETTE_CE_API_KEY` | designer + coder: the palette model on the Code Engine GPU fleet |
| `GPT_OSS_120B_PROVIDER=watsonx`, `WATSONX_APIKEY`, `WATSONX_PROJECT_ID` | planner + editor: gpt-oss-120b on watsonx.ai |
| `RITS_API_KEY` | only if you use RITS instead (needs the IBM VPN) |

Check it: `make serve-doctor` — every line should say `ok` and `can_build: true`.

### 2. Add the skill

```bash
npx skills add IBM/project-palette -g -a claude-code -a bob -y
```

That puts the skill in `~/.agents/skills/palette` and links it for Claude
Code (`~/.claude/skills/palette`) and IBM Bob (`~/.bob/skills/palette`). Drop the `-a …` flags to pick agents interactively;
drop `-g` to install into the current project only. From a checkout,
`make skill-install-claude` does the same for Claude Code.

---

## Option B — Remote install (2 steps)

```bash
echo 'export PALETTE_URL=https://palette.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud' >> ~/.zshrc
npx skills add IBM/project-palette -g -a claude-code -a bob -y
```

That's all — no clone, no Python/Node/LibreOffice, no model keys; the server
has them. Open a **new** Claude Code or Bob session and
[try it](#try-it). (The URL above is the shared instance; ask Praveen or Anu
if it moves. Check it's up: `curl -s $PALETTE_URL/health`.)

Decks build on the server and are downloaded into `./deck/` when done. The
server holds sessions in memory, so a server restart mid-build fails that
build (the skill says so) — just start it again.

---

## Try it

Open a **new** Claude Code or Bob session (so it picks up the variable and the
skill) and ask:

> Build me a 3-slide deck explaining prompt caching to backend engineers.

The agent runs the skill, shows you a plan, and waits. Reply to approve or ask
for changes; it then builds the deck (3–10 minutes) into `./deck/deck.pptx`.

---

## What `PALETTE_HOME` points at (Option A)

The skill runs **whatever that checkout contains right now** — its code, on
whichever branch is checked out, and its `.env`. Switch branches there and the
agent's next deck uses the new branch.

| Setup | `PALETTE_HOME` | Install the skill with |
|---|---|---|
| **Everyday use** (recommended) | a dedicated clone on `main`, e.g. `~/palette` — not your dev working copy, so switching branches there doesn't change what your agent runs | `npx skills add IBM/project-palette -g -a claude-code -a bob -y` (the skill from GitHub's `main`) |
| **Developing Palette or the skill** | your working copy, on your branch | `npx skills add "$PALETTE_HOME" -g -a claude-code -a bob -y` — the skill from the **same** checkout, so the commands it calls exist there (`make skill-install-claude` does this for Claude Code) |

Keep the two in step: the installed skill calls `palette.py` commands and
flags, so a skill from one branch can break against a checkout on another.

To update:

```bash
cd "$PALETTE_HOME" && git pull && make install    # Palette: code + dependencies
npx skills update                                  # the skill (or re-run the add command)
```

`make verify` in the checkout checks that the installed copy (in `~/.claude/skills`) matches it.

## How it works

```
                                   ┌─ remote: $PALETTE_URL ──► Palette web app (HTTP) ─┐
agent ──► skills/palette/scripts/  │                                                    ├─► same core ──► models
          deck.py                  └─ local:  $PALETTE_HOME ─► palette.py (+ .env) ────┘    functions     fleet + watsonx
          (plan · edit · start · status)
```

- **Remote:** `deck.py` calls the app's async endpoints (`/draft_async`,
  `/edit_plan_async`, `/build_async`, `/result`), then downloads `deck.pptx`
  and the slide previews into `--out-dir`. The server's own configuration
  decides the models.
- **Local:** `deck.py` runs Palette from `$PALETTE_HOME` with that checkout's
  `.env`, so the skill uses exactly the models the checkout is configured for.
  Anything already exported in the agent's environment wins over `.env`.
- Slow steps detach: `plan` returns the plan (usually within 90s), `start`
  kicks off the build and `status` is polled — so hosts that cap how long one
  command may run can't kill a build half-way.
- The web app, the local service and the skill all call the same functions
  (`intake.craft_plan`, `intake.edit_plan`, `pipeline.generate_deck`).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `cannot find the Palette checkout` | Neither variable is set in the agent's shell — add `PALETTE_URL` (remote) or `PALETTE_HOME` (local) to `~/.zshrc` and open a new session |
| `cannot reach the Palette server at …` (remote) | The server is down or the URL is wrong — `curl -s $PALETTE_URL/health` |
| `the Palette server no longer knows this build` (remote) | The server restarted mid-build — ask the agent to build again |
| The agent writes slides itself instead of using the skill | The skill isn't installed where that agent looks — `npx skills list -g` |
| Build runs past 15 minutes | Models unreachable — local: `tail -20 deck/build.log`, check `.env` and `make serve-doctor`; remote: the server's GPU fleet may be stopped |
| `soffice` / `node` not found in the build log | Step 1's `brew install` line |
| Slide previews on a Mac show a serif fallback instead of IBM Plex | LibreOffice looks fonts up by family name, and IBM's macOS Plex install names weights differently (`IBM Plex Sans Medm`) from the Linux packages the deck targets. Only the local previews/PDF are affected; the `.pptx` still names IBM Plex. The Code Engine app and `make serve-start` (container) render with the right fonts |

Update the skill: `npx skills update`. Remove it: `npx skills remove palette -g`.

More: [`docs/deploying-the-skill.md`](../../docs/deploying-the-skill.md) (other
agent hosts), [`docs/cli.md`](../../docs/cli.md) (the same commands at a terminal).
