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
make skill-test                                 # ~40 fast tests, no models
make skill-install CUGA=~/code/cuga-agent       # -> .cuga/skills/palette
cd ~/code/cuga-agent
PALETTE_HOME=~/code/project-palette cuga start demo_palette
# then, in the chat: "Build me a 5-slide deck about RAG"
```

## 1. Test without an agent, without a model

The fastest signal that the skill is not broken. No network, no key.

```bash
make skill-test
```

It reads `palette.py`'s argparse and fails if `SKILL.md` or `deck.py` names a
command or flag that does not exist. This is the test that catches the skill
drifting away from the CLI it drives.

## 2. Test the skill by hand, with a model

Skips the agent entirely — is it Palette or is it the agent?

```bash
S=skills/palette/scripts/deck.py
python $S plan --request "5 slides on RAG" --out /tmp/p.md --wait   # blocks 40-180s
python $S start  --plan /tmp/p.md --out-dir /tmp/deck
python $S status --out-dir /tmp/deck                                # repeat
```

`--wait` is the convenience for a terminal. **Agents must not use it** — they
run `plan` bare, which returns at once, and collect it with `plan-status`,
because a host that cuts a step short turns a working call into a silent
failure. `status` is instant; repeat until `"done": true`. The build takes
**3-10 minutes**.

## 3. Is that deck real?

The one question worth asking, because an agent can describe a file it never
wrote. **Wait for `status` to say `"done"` first** — `build-deck` re-renders to
the same path two or three times while fixing geometry, so a complete-looking
`deck.pptx` shows up minutes before the build is actually finished.

```bash
ls -l /tmp/deck/deck.pptx                  # >100KB, not 4KB
unzip -l /tmp/deck/deck.pptx | grep -c "slides/slide"
unzip -p /tmp/deck/deck.pptx ppt/slides/slide1.xml | grep -c "IBM Plex"
```

`slide1.xml` carries **IBM Plex** because Palette's renderer forces it — so a
deck hand-written with `pptxgenjs` defaults could not have it. That grep is the
difference between "a deck exists" and "*Palette* built this deck".

If there is no deck:

```bash
tail -30 /tmp/deck/build.log
```

## 4. Test from CUGA

```bash
make skill-install CUGA=~/code/cuga-agent
cd ~/code/cuga-agent
PALETTE_HOME=~/code/project-palette cuga start demo_palette
```

Ask for a deck. You should get **a plan first** and a question — approving it is
turn two. Then verify from your shell, not from the chat:

```bash
ls -l cuga_workspace/*/deck/deck.pptx
cat  cuga_workspace/*/deck/.palette-build.json     # "state": "done"
```

## 5. Test from Claude Code

Same skill, no CUGA:

```bash
make skill-install-claude          # -> ~/.claude/skills/palette
```

Then in Claude Code, with `PALETTE_HOME` and `RITS_API_KEY` in the environment,
ask for a deck. Claude Code's Bash tool allows ten minutes, so it may run
`build-deck` in one call rather than polling — both paths are supported.

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
**The agent reads its installed copy, not your working tree.**

```bash
rm -rf ~/code/cuga-agent/.cuga/skills/palette
make skill-install CUGA=~/code/cuga-agent

# confirm the copy matches your tree
diff -r ~/code/cuga-agent/.cuga/skills/palette skills/palette
```

Do the same for Claude Code with `rm -rf ~/.claude/skills/palette && make
skill-install-claude`. This is the single most common cause of "my fix did
nothing".

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

### Level 4 — everything not tracked by git

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
| Agent stops mid-build | Prose with no code reads as a finished answer. `demo_palette` sets `cuga_lite_nl_auto_continue=true` to prevent it. |
| Deck exists but looks generic | Check the IBM Plex grep in §3. Something else may have written it. |
