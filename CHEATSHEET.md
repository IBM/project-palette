# Cheatsheet — tear down, restart, test

Copy-pasteable. `README.md` explains Palette; this explains getting a working
loop back after you have broken one.

Everything assumes you are in the Palette checkout, and that these are set:

```bash
export PALETTE_HOME=$PWD
export RITS_API_KEY=<key>          # IBM-internal; needs VPN
```

---

## 0. The whole loop, six lines

```bash
make install                                    # venv + deps
make skill-test                                 # fast tests, no models
make skill-install CUGA=~/code/cuga-agent       # -> .cuga/skills/palette
cd ~/code/cuga-agent
PALETTE_HOME=~/code/project-palette cuga start demo_palette
# then, in the chat: "Build me a 5-slide deck about RAG"
```

## 1. Clean slate, end to end

Five stages, cheapest first, each proving something the next one assumes.
**Stop at the first failure** — a later stage cannot be interpreted while an
earlier one is red. Paths assume the three repos are siblings.

```bash
P=~/code/project-palette;  C=~/code/cuga-agent;  S=~/code/cuga-skills
```

### Stage 1 — the repos agree with themselves (~10s, no network, no key)

```bash
(cd $P && .venv/bin/python -m pytest tests/ -q)
(cd $C && .venv/bin/python -m pytest tests/unit/test_demo_palette_preset.py \
                                     tests/e2e/skills/test_palette_skill_invocation.py -q)
(cd $S && .venv/bin/python -m pytest -q)
```

Expect roughly `103 / 26 / 13 passed`. No models are involved, so a failure
here is a real defect and never the weather. Between them these cover: the
skill naming only commands and flags `palette.py` actually has (read out of
its argparse), the packaging pointing at code that exists, CUGA's preset
settings, and the agent reaching `load_skill` and getting Palette's real
instructions back.

Palette's slice alone, if that is all you want: `make skill-test`.

### Stage 2 — the installed copies are the real ones

**The single most common cause of "my fix did nothing."** An agent reads its
installed copy, never your working tree.

```bash
make -C $P skill-install CUGA=$C
make -C $P skill-install-claude
make -C $P verify CUGA=$C
```

`make verify` is the test for this. It compares every installed copy against
`skills/palette` byte for byte and names the file that differs, checks the
frontmatter a host routes on, and runs the installed `deck.py` to confirm it
resolves `$PALETTE_HOME` from wherever it was copied to. It skips by name for
any location you did not give it, so plain `make verify` still checks Claude
Code's copy.

Expect `12 passed, 1 skipped` — the skip is the real deck build, which is
opt-in. Add `DECK=1` to include it (minutes, needs the VPN):

```bash
make -C $P verify CUGA=$C DECK=1
```

### Stage 3 — Palette builds a deck, no agent involved (~5 min)

This is what separates "Palette is broken" from "the agent is confused".
Needs `RITS_API_KEY` and the VPN.

```bash
cd $P && export PALETTE_HOME=$PWD
S=skills/palette/scripts/deck.py
python $S plan   --request "5 slides on RAG" --out /tmp/p.md --wait   # blocks 40-180s
python $S start  --plan /tmp/p.md --out-dir /tmp/deck
python $S status --out-dir /tmp/deck                                  # repeat until done
```

`--wait` is the convenience for a terminal. **Agents must not use it** — they
run `plan` bare, which returns at once, and collect it with `plan-status`,
because a host that cuts a step short turns a working call into a silent
failure. `status` is instant; the build takes **3-10 minutes**.

Then confirm it is genuinely a Palette deck — §2 below. If Stage 3 passes and
4 or 5 fails, the fault is in the agent or the host, not in Palette.

### Stage 4 — Claude Code

```bash
make -C $P skill-install-claude
```

In a session with `PALETTE_HOME` and `RITS_API_KEY` in the environment:
*"Build me a 5-slide deck about RAG."*

### Stage 5 — CUGA

```bash
grep -q '^PALETTE_HOME=' $C/.env || echo "PALETTE_HOME=$P" >> $C/.env
rm -rf /tmp/.venv                    # the sandbox's own venv; it rebuilds itself
cd $C && cuga start demo_palette
```

Either host should give you **a plan and a question first** — approving it is
a second turn. Then check from your shell rather than the chat, because both
real failures here were an agent reporting failure over a working build:

```bash
ls -l cuga_workspace/*/deck/deck.pptx
cat  cuga_workspace/*/deck/.palette-build.json    # "state": "done"
```

### Stage 6 — the LangGraph ReAct agent (~3 min, fully automated)

The only host you do not have to talk to. Nothing to install: it reads
`skills/palette` out of the checkout.

```bash
cd $P
export PALETTE_HOME=$PWD

$C/.venv/bin/python -m pytest agents/tests -q                  # offline, ~3s
$C/.venv/bin/python benchmark/react_run.py --check --env-file $C/.env

$C/.venv/bin/python agents/palette_react/cli.py --env-file $C/.env --trace \
    --workspace /tmp/palette-smoke \
    "Build a 3-slide deck explaining prompt caching to backend engineers" \
    --reply yes
```

It prints each command as it runs, then the deck's size, slide count, and
whether it carries IBM Plex — so you do not need Stage 2's checks afterwards.
A good run ends like this:

```
133,860 bytes · 3 slides · rendered by Palette
palette calls:
  find      0.0s  {"root": "."}
  plan     48.1s  {"request": "Build a 3-slide deck…", "out": "plan.md"}
  start     0.0s  {"plan": "plan.md", "out_dir": "./deck"}
  status   60.1s
  status   42.1s
```

`start` appearing only after the `yes` is the part worth looking at: it means
the approval gate held. Uses watsonx (`WATSONX_*` in `$C/.env`), not RITS.

## 1b. Where did my deck go?

The first thing to run when a session seems stuck, looping, or silent. It reads
the filesystem, so it is true regardless of what the chat says:

```bash
python $P/skills/palette/scripts/deck.py find --root $C/cuga_workspace
```

Every plan and every build underneath, in one call. No `PALETTE_HOME`, no
checkout — `find` only reads output directories.

- **`"state": "done"`** on a build — the deck is finished and the agent simply
  never told you. The `pptx` path in that entry is the file.
- **`"state": "running"`** — wait; `elapsed_seconds` says how long.
- **a plan with no build** — it stopped after the confirmation gate.
- **nothing** — nothing was ever started there.

This is also **Step 0** of the skill's own workflow, so the agent asks the
same question before it says anything on a new turn.

## 2. Is that deck real?

The one question worth asking, because an agent can describe a file it never
wrote. **Wait for `status` to say `"done"` first** — `build-deck` re-renders to
the same path two or three times while fixing geometry, so a complete-looking
`deck.pptx` shows up minutes before the build is actually finished.

```bash
D=/tmp/deck                                       # or cuga_workspace/<id>/deck
ls -l $D/deck.pptx                                # >100KB, not 4KB
unzip -l $D/deck.pptx | grep -c "slides/slide"    # slide count
unzip -p $D/deck.pptx ppt/slides/slide1.xml | grep -c "IBM Plex"
```

`slide1.xml` carries **IBM Plex** because Palette's renderer forces it — so a
deck hand-written with `pptxgenjs` defaults could not have it. That grep is the
difference between "a deck exists" and "*Palette* built this deck".

If there is no deck:

```bash
tail -30 $D/build.log
```

---

## Nuking and starting fresh

Four levels, mildest first. Try them in order; you almost never need level 4.

### Level 1 — the output, not the install

Decks and per-session state only. Nothing is reinstalled.

```bash
make clean-state                   # workspace/ and per-session logs
rm -rf /tmp/deck /tmp/palette-decks
```

### Level 2 — a stale skill copy

Symptom: you changed `SKILL.md` or `deck.py` and the agent behaves as before.
**The agent reads its installed copy, not your working tree.** This is the
single most common cause of "my fix did nothing".

Run **Stage 2** of §1 — nuke both copies, reinstall, and diff to confirm.

### Level 3 — the Python environment

Symptom: import errors, a dependency that will not upgrade, or `palette.py`
behaving unlike its source.

```bash
make clean                         # caches, build artifacts
rm -rf .venv
make install
```

**CUGA's sandbox keeps its own venv at `/tmp/.venv`**, which does not rebuild
when yours does. A stale one there once produced a build from code deleted
hours earlier:

```bash
rm -rf /tmp/.venv                  # CUGA recreates it on next start
```

### Level 4 — full rebuild, both repos, from nothing

The complete reset: both virtualenvs, node modules, caches, sandbox state,
installed skills. Roughly **15 minutes** and ~2GB of downloads.

> **Commit first, and check.** `git clean -fdx` deletes untracked files, and
> untracked includes whole directories you have not added yet. Run this in each
> repo and read it:
>
> ```bash
> git status --short          # ?? lines are what you are about to lose
> git clean -ndx | head -40   # DRY RUN — the actual list
> ```
>
> Save `RITS_API_KEY` somewhere too, if it only lives in a `.env`.

```bash
P=~/code/project-palette;  C=~/code/cuga-agent

# 1. stop everything
pkill -f "bin/cuga start"; pkill -f "palette.py build-deck"
for port in 7860 8001; do lsof -ti :$port | xargs -r kill; done

# 2. remove the installed skill copies (they are rebuilt in step 6)
rm -rf $C/.cuga/skills/palette ~/.claude/skills/palette

# 3. the sandbox's own venv — separate from both repos, and a stale one
#    has served code that was deleted hours earlier
rm -rf /tmp/.venv

# 4. Palette: environment and everything untracked
cd $P
git clean -fdx                     # after reading the dry run above
uv venv
make install                       # -> "ok: palette.py" and "ok: the skill's deck.py"

# 5. CUGA: same
cd $C
git clean -fdx -e .env             # KEEP .env — it holds your keys
uv venv --python=3.12
uv sync

# 6. reinstall the skill into both hosts
cd $P
make skill-install CUGA=$C
make skill-install-claude
```

Then verify before trusting anything — §1 Stage 1 and Stage 2:

```bash
(cd $P && .venv/bin/python -m pytest tests/ -q)     # ~180 passed
make -C $P verify CUGA=$C                            # 12 passed
```

`-e .env` on CUGA's clean is not optional: `.env` is untracked, holds
`RITS_API_KEY` and `PALETTE_HOME`, and cannot be rebuilt from source.

### Level 4b — everything not tracked by git, one repo

The nuclear option. Prints what it would delete first.

```bash
git clean -ndx                     # DRY RUN — read this before the next line
git clean -fdx
make install
```

`-x` includes files git is ignoring, so this takes your `.venv`, your
`workspace/`, and any local `.env`. **Save your `RITS_API_KEY` first.**

---

## When it is not your fault

| Symptom | What it usually is |
|---|---|
| Build runs 15+ min, then fails | No route to RITS — connect the VPN. It retries every stage before giving up; 51 minutes was measured. |
| `error: $PALETTE_HOME=... has no palette.py` | Pointed at the skill folder rather than the checkout. They are different roots. |
| Agent says "done", no file on disk | It relayed an exit code. `deck.py status` computes `verified` by stat-ing the file — trust that and nothing else. |
| `status` says `error` seconds after `start`, and the agent starts theorising about missing dependencies | A pre-fix copy of the skill. It probed liveness with `os.kill`, which CUGA's sandbox denies, so it read every live build as dead. Reinstall — level 2 above. A current `deck.py` waits for `.palette-exit`. |
| The agent keeps asking you to approve the plan, however many times you say yes | It has no memory of earlier turns and nothing told it to look for work already in flight, so it restarts at the confirmation gate every turn. Your deck is probably already built — check with the command below. A current `SKILL.md` opens with **Step 0**, which asks the filesystem first. |
| Agent stops mid-build | Prose with no code reads as a finished answer. `demo_palette` sets `cuga_lite_nl_auto_continue=true` to prevent it. |
| Deck exists but looks generic | Check the IBM Plex grep in §2. Something else may have written it. |
