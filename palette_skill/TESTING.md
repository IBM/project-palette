# Testing the Palette skill

Everything here is runnable. The tiers are ordered by what they need, so you
can stop wherever your environment stops — each one is useful on its own.

| Tier | Needs | Time | Answers |
|---|---|---|---|
| [1](#tier-1--the-skill-matches-the-code) | nothing | ~1s | Does the skill still describe this server? |
| [2](#tier-2--prove-the-drift-gates-fire) | nothing | ~1min | Do the guards actually catch a change? |
| [3](#tier-3--the-local-service) | Node, LibreOffice, Poppler | ~1min | Can Palette run here? |
| [4](#tier-4--the-skill-reaches-cuga) | a CUGA checkout | ~30s | Does CUGA discover and load it? |
| [5](#tier-5--round-trip-to-a-live-palette) | + Palette running | ~30s | Does the sandbox reach the server? |
| [6](#tier-6--a-real-model-chooses-the-skill) | + LLM credentials | ~1min | Does a model pick it unprompted? |
| [7](#tier-7--markdown-in-real-decks-out) | + RITS key | ~12min | Does markdown become a real `.pptx`? |
| [8](#tier-8--the-agent-itself) | all of the above | manual | Does it work for a person? |
| [9](#tier-9--the-release-is-usable-by-a-stranger) | nothing | ~1min | Can someone with no checkout install it? |

Paths below assume the two repos are siblings. Adjust `CUGA=` if not.

---

## Tier 0 — setup, once per machine

```bash
cd ~/Documents/GitHub/project-palette-july25
uv venv && uv pip install -e '.[dev]'      # server + client + pytest
npm install                                 # pptxgenjs — the renderer needs it
make hooks                                  # pre-commit guard (see Tier 2)
palette-skill serve init                    # writes ~/.config/palette/env
```

`[dev]` is the one you want on a machine where you run the tests — it pulls in
`[server]` *and* pytest. `[server]` alone is for a box that only runs the
service; installing that and then running `make skill-test` fails on a missing
pytest, and adding pytest to `[project.dependencies]` to fix it trips
`test_client_runtime_is_httpx_only` (correctly — that wheel ships into every
agent sandbox).

Then put your key in `~/.config/palette/env`:

```
RITS_API_KEY=...
```

Do this even if the key is already in CUGA's `.env`. Palette's supervisor reads
its own env file, so without it `serve doctor` reports `can_build: false` and a
restarted service comes up keyless — builds then fail at the first model call.

---

## Tier 1 — the skill matches the code

The one to run after every Palette change. No dependencies, about a second.

```bash
make skill
```

Expect a clean run. It checks two things: the generated regions of `SKILL.md`
are current with `contract.py` and `config.py`, and the contract still matches
`app.py`'s actual routes and request models.

Individually:

```bash
make skill-check    # only: is the generated markdown stale?
make skill-test     # only: does the contract still match the server?
```

---

## Tier 2 — prove the drift gates fire

Worth doing once so you trust the guard rather than taking its word.

**A route the skill does not know about:**

```bash
cat >> app.py <<'EOF'

@app.get("/thing/{name}")
async def thing(name: str) -> dict:
    return {"thing": name}
EOF

make skill-test          # FAILED test_every_route_is_in_the_contract
git checkout app.py
```

**A model added to the roster:**

```bash
# add a key to PLANNER_MODELS in config.py, then
make skill-check         # "stale generated content"
make skill-build         # regenerates — the new model appears in SKILL.md
git checkout config.py palette_skill/payload
```

**The pre-commit guard:**

```bash
echo "_SCRATCH = 1" >> config.py
git add config.py && git commit -m "test"    # refused, prints the fix
git restore --staged config.py && git checkout config.py
```

The hook only runs when `app.py`, `config.py`, `session.py`,
`requirements.txt`, `pyproject.toml`, or `palette_skill/` is staged.

---

## Tier 3 — the local service

```bash
palette-skill serve doctor    # per-mode readiness and what blocks each
palette-skill serve ensure    # start it, wait until /health answers
palette-skill serve status    # running? which mode? which workspace?
palette-skill serve logs
palette-skill serve stop
```

`doctor` reports `ready` per mode. `process` and `launchd` need Node,
pptxgenjs, LibreOffice and Poppler; `container` needs a built image
(`make docker-build`). `can_build` is separate — it is only true when a RITS
key is visible, because the server starts happily without one and fails later.

---

## Tier 4 — the skill reaches CUGA

```bash
make skill-install CUGA=../cuga-agent-july25
make skill-status  CUGA=../cuga-agent-july25    # → "in sync"

cd ../cuga-agent-july25
uv run pytest tests/e2e/skills/test_palette_skill_invocation.py -q   # all green
uv run pytest tests/unit/test_demo_palette_preset.py -q             # all green
uv run pytest tests/e2e/skills/test_skills_e2e.py -q                # all green
```

The first runs the real CUGA graph against the real installed skill with a
scripted model: the skill reaches the system prompt, `load_skill("palette")`
returns the real playbook, and the agent acts on it. No LLM, no server.

To confirm discovery directly:

```bash
DYNACONF_SKILLS__ENABLED=true DYNACONF_ADVANCED_FEATURES__ENABLE_SHELL_TOOL=true \
  uv run python -c "
from cuga.backend.skills import discover_skills
print([e.name for e in discover_skills('.cuga')])"
```

---

## Tier 5 — round trip to a live Palette

```bash
palette-skill serve ensure
cd ../cuga-agent-july25
uv run pytest tests/e2e/skills/test_palette_skill_invocation.py -m manual -q
```

Still a scripted model, but every other component is real: graph, skill,
sandbox, CLI, server. Skips cleanly when Palette is down.

---

## Tier 6 — a real model chooses the skill

```bash
uv run pytest tests/e2e/skills/test_palette_skill_invocation.py -m e2e -q
```

Nothing scripted. A plain deck request must route to `load_skill("palette")`
on the model's own judgement.

**If this fails, the fix is the `description:` in SKILL.md** — that single line
is what the model routes on. Edit it, `make skill-build`, `make skill-install`.
No CUGA change is involved.

---

## Tier 7 — markdown in, real decks out

```bash
palette-skill serve ensure
cd ../cuga-agent-july25
PALETTE_DECK_OUT=~/Desktop/decks \
  uv run pytest tests/e2e/skills/test_palette_deck_e2e.py -m e2e -q -s
```

Three routes, matching how people actually arrive at a deck:

- **Route A** — `fixtures/plan_agent_skills.md`, already in plan format, goes
  straight to Stage 2.
- **Route B** — `fixtures/source_sandbox_notes.md`, raw notes, goes through
  Stage 1 first.
- **Route C** — no fixture at all: *"Draft a plan for a Q3 sales review, show it
  to me, then build it."*

All three run at the 120s step the `demo_palette` preset sets, with the skill
polling at 100s to fit inside it. That pairing is the point: at the 30s default
a ten-minute build is twenty-odd polls and the agent abandons it around step 40,
with the build finishing on the server minutes later and nobody collecting it.

**Route C is the one that finds things.** A and B name the stage, say "poll
until the build finishes", sometimes pass a `--max-seconds` value — under that
much instruction the agent cannot fail the way it fails in the wild. C says what
a person says. Every skill bug found so far came from a prompt shaped like C.

Decks land in `PALETTE_DECK_OUT`. The tests assert on the artifact — valid
OOXML, slide-part count, preview PNGs, minimum file size, and IBM Plex
typefaces as proof it came out of Palette's renderer rather than being
hand-written.

Route C additionally asserts `.palette-deck.json` exists with `"stage": "done"`.
That file is written by `palette-skill deck` and by nothing else, so its absence
means the agent hand-drove the draft/build sequence — the arrangement that
produces a deck announcement with no deck behind it.

**Do not pipe this through `head`.** SIGPIPE kills pytest partway and the run
looks like a failure that never happened.

---

## Tier 8 — the agent itself

Start it and ask for a deck — commands in [CHEATSHEET.md](CHEATSHEET.md) §0.
Skills appear in the right-hand panel because `/api/skills` requires
`skills.enabled` **and** `enable_shell_tool`; the preset sets both.

Then verify with CHEATSHEET §6, which is the same three checks every time.
What this tier adds is knowing *what the failures look like*.

**The agent's `./` is not your shell's.** Each thread gets
`<cwd>/cuga_workspace/<thread_id>/`, so `./deck/deck.pptx` lives under a
directory named by a thread id you did not choose. The id appears in the server
log and in the UI's workspace panel.

**A session directory holding only `session.log`** means a draft ran and no
build followed. A real build leaves `deck.pptx`, `deck.pdf`, `slide-*.png`,
`deck.json` and `output_js/`:

```bash
ls -td ~/.local/state/palette/workspace/*/ | head -1 | xargs ls -l
```

**Read the thread ids, not the line count.** One id carrying both a
`draft_async` and a `build_async` is a healthy run. Several ids with no build is
the signature failure — each retry started a fresh draft and orphaned the
previous session, and the run ends with a confident summary of files that were
never written. That shape has been reproduced deliberately; it looks like this:

```
21:31:09  draft_async thread=skill-1d218b19  accepted
21:31:47  draft_async thread=skill-9ead9bec  accepted     ← new id, previous orphaned
21:32:23  draft_async thread=skill-6d448feb  accepted
21:36:45  draft_async thread=skill-58b00b8d  accepted
          (no build_async anywhere)
```

---

## Tier 9 — the release is usable by a stranger

The failure this catches is quiet: a release that secretly needs the checkout
it was built from. Nothing looks wrong until someone else tries it.

```bash
make release
```

Then install it the way a consumer would — into a directory with no Palette
source anywhere:

```bash
mkdir -p /tmp/consumer/.cuga/skills
tar xzf dist/palette-skill-*-cuga.tar.gz -C /tmp/consumer/.cuga/skills/
find /tmp/consumer -type f          # SKILL.md, reference.md, vendor/*.whl, manifest
```

Confirm an agent would discover it there:

```bash
cd ~/Documents/GitHub/cuga-agent-july25
DYNACONF_SKILLS__ENABLED=true uv run python -c "
import os; os.chdir('/tmp/consumer')
from cuga.backend.skills import discover_skills
print([e.name for e in discover_skills('.cuga')])"
```

The other release shape — `pip install palette-skill` then
`python -m palette_skill.install --host cuga` — has no vendored wheel, and the
generated SKILL.md must say `uv pip install palette-skill` rather than point at
a `vendor/` directory that will not exist. `tests/test_release.py` pins both
shapes, plus per-host vocabulary and the pinned-URL behaviour.

---

## Other hosts

The skill is generated per agent host. Only the execution section differs —
everything else describes a service and is true anywhere.

```bash
make skill-install CUGA=../cuga-agent-july25            # CUGA (default)
python -m palette_skill.install --host claude-code      # ~/.claude/skills/palette
python -m palette_skill.install --host generic --skills-root <dir>
```

Verify a variant got the right vocabulary:

```bash
python -c "
from palette_skill import build_skill, hosts
build_skill._HOST = hosts.get('claude-code')
print(build_skill.render_execution())"
```

`test_shared_prose_names_no_host_specific_tool` fails if a host's tool name
leaks into the shared prose, and `test_claude_code_variant_drops_cuga_vocabulary`
fails if a variant carries another host's words.

### Testing it in Claude Code, end to end

Claude Code discovers `~/.claude/skills/` on its own, so installing is the
whole setup. **Run the installer from the Palette checkout**, not from an
installed copy of the package — outside a checkout there is no wheel to vendor
and you get a skill folder with an empty `vendor/`:

```bash
cd ~/Documents/GitHub/project-palette-july25
.venv/bin/python -m palette_skill.install --host claude-code
palette-skill serve ensure          # the skill talks to a server, wherever it is
```

Then start a Claude Code session anywhere and ask for a deck — *"build me a
deck about vector databases for backend engineers"*. It should reach for the
skill unprompted; that routing decision comes from the `description:` line
alone.

What to look for, in order:

1. **It loads the skill** rather than reaching for `python-pptx`.
2. **It installs the client from the vendored wheel** —
   `uv pip install ~/.claude/skills/palette/vendor/palette_skill-*.whl`.
3. **It polls with `--max-seconds 240`**, not 25. Claude Code's `Bash` allows
   ten minutes, so one call covers most of a build; seeing 25 means the
   installed skill is stale or was rendered for the wrong host.
4. **It reports an absolute `pptx_path`** and does not mention a Files panel —
   there isn't one, and the path is the whole answer on this host.

Verify the artifact the same way as anywhere else:

```bash
cat <dest>/.palette-deck.json                                    # "stage": "done"
unzip -p <dest>/deck.pptx ppt/slides/slide1.xml | grep -c "IBM Plex"
```

> **Installing for one host does not disturb another.** `install` snapshots
> `payload/` and restores it, so a `--host claude-code` install leaves the
> checkout — and any CUGA install made from it — exactly as they were. That was
> not true until it was tested: the first claude-code install left the repo
> rendered for Claude Code and `make skill-check` started failing for no
> visible reason.

### Which server did it actually talk to?

The client resolves `$PALETTE_URL`, then `contract.DEFAULT_BASE_URL`, which is
**`http://127.0.0.1:18814`** — local. There is no ambiguity to reason about;
the server log answers it:

```bash
grep -l <thread-id> ~/.local/state/palette/workspace/*/session.log
```

A thread id that appears there was served locally. One that does not went
somewhere else, and the deck's own `.palette-deck.json` holds the id.

To test against a deployment instead:

```bash
PALETTE_URL=https://palette.example.cloud palette-skill health
```

Everything above is otherwise unchanged — the skill is an HTTP client and only
the URL differs. `deployment/DEPLOYMENT.md` covers standing one up.

---

## What none of this catches

The gates cover the *shape* of the API. They cannot see meaning:

- **Behaviour changes behind a stable signature.** A stage gets slower and
  nothing fails: the build-duration range and the call estimate are rendered
  from `hosts.py`'s `poll_seconds`, but the *range itself* is a hand-written
  constant in `render_polling`, as is `budget_seconds` in `contract.py`. Both
  are measurements someone took once. Retake them when a stage changes.
- **Instructions that are accurate but not followable.** Every skill failure
  seen in the wild so far has been this, not a wrong fact — a section that told
  the agent to drive the primitives by hand, a poll window that made a deck
  cost more steps than an agent will sit through, a "report what happened"
  clause that licensed stopping mid-build. Each read fine and each was found by
  running the thing, never by a test. Tier 9 exists for that reason.
- **New capability that deserves its own workflow section.** The generator will
  not invent one.
- **The `description:` frontmatter.** The routing trigger is entirely
  hand-written. If Palette grows a capability the description does not mention,
  the agent never reaches for it and no test can tell you.

Rule of thumb: **shape changes are caught, meaning changes are not.** After a
behavioural change, reread `## Making a deck` and `## Never claim a deck that
does not exist` yourself — they rot first, and they are the two an agent acts
on most directly.
