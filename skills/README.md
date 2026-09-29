# Palette skill — install guide

Palette turns a request into a slide deck. The **skill** lets your coding
agent (Claude Code, IBM Bob, …) use it: you ask for a deck in chat, approve a
plan, and get a `.pptx`.

This page gets you from zero to your first deck. Details and troubleshooting:
[`palette/README.md`](palette/README.md).

---

## Step 1 — Install the skill

```bash
npx skills add https://github.com/IBM/project-palette/tree/palette_skill/skills/palette -g -a claude-code -a bob -y
```

> **Branch:** the skill and these instructions live on the `palette_skill` branch until it's
> merged into `main`. After that, the shorter `npx skills add IBM/project-palette …` and a plain
> `git clone` (default branch) work too.

This needs Node.js (`brew install node` if `npx` isn't found). It puts the
skill in one place and links it to your agents:

```
~/.agents/skills/palette/      ← the skill (README.md, SKILL.md, scripts/deck.py)
~/.claude/skills/palette   →   linked for Claude Code
~/.bob/skills/palette      →   linked for IBM Bob
```

<details>
<summary>Prefer to copy it by hand?</summary>

```bash
git clone -b palette_skill https://github.com/IBM/project-palette.git
mkdir -p ~/.agents/skills ~/.claude/skills
cp -R project-palette/skills/palette ~/.agents/skills/palette
ln -sfn ../../.agents/skills/palette ~/.claude/skills/palette    # Claude Code
```

IBM Bob, Cursor, Codex and Cline read `~/.agents/skills` directly; Claude Code
needs the link.
</details>

## Step 2 — Connect it to Palette (pick one)

The skill is the remote control; Palette does the work. Tell the skill where
Palette runs with **one** variable:

| | **Option A — Local** | **Option B — Remote** |
|---|---|---|
| Palette runs | on your machine | on a shared server |
| You set | `PALETTE_HOME` | `PALETTE_URL` |
| Extra installs | Palette + Node + LibreOffice (~15 min) | none |
| Choose it if | you develop Palette, or want it offline from the server | you just want decks |

### Option A — Local

```bash
git clone -b palette_skill https://github.com/IBM/project-palette.git ~/palette && cd ~/palette
brew install node poppler && brew install --cask libreoffice
make install
cp .env.example .env          # then fill in the model settings (ask Praveen or Anu)
echo 'export PALETTE_HOME=~/palette' >> ~/.zshrc
```

Check it: `make serve-doctor` should end with `"can_build": true`.

The model settings in `.env` are the palette model endpoint
(`PALETTE_CE_BASE_URL`, `PALETTE_CE_API_KEY`) and watsonx
(`GPT_OSS_120B_PROVIDER=watsonx`, `WATSONX_APIKEY`, `WATSONX_PROJECT_ID`) —
or `RITS_API_KEY` if you use RITS.

### Option B — Remote

```bash
echo 'export PALETTE_URL=https://palette.1gxwxi8kos9y.us-east.codeengine.appdomain.cloud' >> ~/.zshrc
```

Check it: `source ~/.zshrc && curl -s $PALETTE_URL/health` should show
`"status":"ok"`.

> If both variables are set, `PALETTE_URL` (Option B) wins.

## Step 3 — Ask for a deck

1. Make an empty folder and open it: `mkdir ~/palette-test && cd ~/palette-test`
2. Start a **new** Claude Code session there — or open the folder in IBM Bob
   (quit and reopen Bob first so it picks up the variable).
3. Ask:

   > Build me a 3-slide deck explaining prompt caching to backend engineers.

4. The agent shows you a **plan**. Ask for changes, or say *"looks good, build it"*.
5. A few minutes later: `~/palette-test/deck/deck.pptx`.

---

## Keep it up to date

```bash
npx skills update                            # the skill
cd ~/palette && git pull && make install     # Palette itself (Option A only)
```

Remove it: `npx skills remove palette -g`.

## Something wrong?

| You see | Do this |
|---|---|
| The agent writes slides itself, or never mentions Palette | The skill isn't installed for that agent — `npx skills list -g`, then redo Step 1 |
| `cannot find the Palette checkout` | Step 2 isn't done, or the session started before it — set the variable and open a **new** session |
| `cannot reach the Palette server` (Option B) | `curl -s $PALETTE_URL/health` — the server may be down; ask Praveen or Anu |
| Build still running after 15 minutes | Models unreachable — Option A: `make serve-doctor`; Option B: ask Praveen or Anu |

More in [`palette/README.md`](palette/README.md#troubleshooting).
