# Deploying the skill to a host

[← Palette README](../README.md) · [CHEATSHEET](../CHEATSHEET.md) ·
[the benchmark](../benchmark/BENCHMARK.md) · [the skill](../skills/palette/SKILL.md)

For putting Palette into an agent that is **not** CUGA or Claude Code. If it is
one of those two, the README's "Try it — start here" is shorter and enough.

Everything below came from installing this skill into three hosts and watching
what broke. The install was minutes each time; getting it *right* was not, and
none of the failures were the skill.

## What ships

```
skills/palette/SKILL.md            the instructions the agent reads
skills/palette/scripts/deck.py     one script — stdlib only
```

92K on disk, 16K packaged. `deck.py` imports nothing outside the standard
library, so there is no `pip install`, no build step, and nothing generated.
Installing is a directory copy.

## What the host must provide

In the order these actually bit.

**1. A shell tool.** Non-negotiable. The skill is prose plus a CLI; an agent
that cannot run a command cannot use it.

**2. Tolerance for a 90-second command.** `plan` holds its call open for up to
90s and `status` for 60s, by design — the alternative is a poll per round trip
out of a finite step budget. A host that caps commands at 30s fails every case,
and the failure looks like Palette being broken.

**3. A step budget of ~40 turns.** A deck is roughly seven Palette calls plus
polls. LangGraph's default recursion limit of 25 was less than one deck, and
hitting the ceiling is indistinguishable from the agent giving up.

**4. Conversation state across turns.** The workflow is plan → the user
approves → build. Claude Code's `-p` is one-shot: without `--continue`, "yes"
arrives in a fresh session with no plan to approve, and every multi-turn case
silently measures nothing.

**5. A writable working directory — and the deck must land where you look.**
CUGA's macOS sandbox confines writes to `<cwd>/cuga_workspace`. When the policy
went stale the agent hit "Operation not permitted", fell back to `/private/tmp`,
and built a perfectly good deck somewhere nobody was looking.

**6. `$PALETTE_HOME` and `$RITS_API_KEY` in the environment the shell
inherits.** Not in the skill folder — the skill is byte-identical on every
machine, which is what lets a catalog verify it by hash.

**7. Python 3 on the path inside that shell.**

## Three routes

### The host has a skills folder

```bash
make skill-package                       # dist/palette-skill.tar.gz
tar xzf dist/palette-skill.tar.gz -C <skills-root>/
```

That is the whole install — the same shape `npx skills add` uses. The host is
expected to read `SKILL.md`'s frontmatter (`name`, `description`) for routing
and hand the body to the model when the skill applies.

Verify it landed and matches your checkout:

```bash
make verify CUGA=<path>            # byte-compares every installed copy
```

### The host has no skill mechanism

**This works, and it is not a workaround.** `agents/palette_react/` is a
LangGraph agent with no skill support at all; about 150 lines do what a loader
does, and it scores 30/33 on the benchmark.

What that glue has to do:

1. Read `SKILL.md`, split the YAML frontmatter from the body.
2. Put `name` and `description` in the system prompt so the model knows the
   skill exists and when it applies.
3. Return the body **verbatim** when the model asks for it — a `load_skill`
   tool, or simply inline it.
4. Give the model a shell pinned to the working directory.
5. Tell the model where the skill folder is, since `SKILL.md` refers to its own
   script by a path relative to a skills root it cannot know. The ReAct host
   stages a copy of the skill into the working directory so the path is simply
   correct; a note in the system prompt works too.

See [`agents/README.md`](../agents/README.md). Copy that package and swap the
model factory if the target host is a framework rather than a product.

### The host cannot run shell commands

Then it cannot use the skill, and no packaging changes that. Palette's HTTP
service is the other integration path — see the README's service section.

## Proving it works, cheapest first

**1. Can it run the script at all?** No model call, no key, instant:

```bash
python skills/palette/scripts/deck.py find --root .
```

JSON back means requirements 1, 5 and 7 hold. An error names which is missing.

**2. Can it reach the models?** ~90 seconds, needs `$RITS_API_KEY`:

```bash
python skills/palette/scripts/deck.py plan --request "3 slides on caching" --out /tmp/p.md
```

**3. Can it build a deck?** 3–10 minutes:

```bash
python skills/palette/scripts/deck.py start  --plan /tmp/p.md --out-dir /tmp/deck
python skills/palette/scripts/deck.py status --out-dir /tmp/deck   # repeat
```

**4. Is the deck real?** The check that separates *a deck exists* from *Palette
made this* — the renderer forces IBM Plex, so a hand-written deck cannot have
it:

```bash
unzip -p /tmp/deck/deck.pptx ppt/slides/slide1.xml | grep -c "IBM Plex"
```

**5. Does the agent drive it correctly?** That is what the benchmark is for.
Add a runner and you get 33 scripted conversations scored by the same judge as
every other host — see [BENCHMARK.md](../benchmark/BENCHMARK.md), "Adding a
host".

## What went wrong on each host

Worth reading before assuming your host is different. Every one of these looked
like a Palette bug and was not.

| Host | Symptom | Cause |
|---|---|---|
| CUGA | "Operation not permitted", deck built into `/private/tmp` | sandbox policy generated once per process, stale after the harness changed directory |
| CUGA | grounding silently lost; deck looked fine | pasted document retyped into `--request`, `--context` empty. `deck.py` now refuses this |
| Claude Code | every multi-turn case meaningless | `-p` is one-shot; needed `--continue` |
| Claude Code | 24 of 33 cases unscoreable | `$PALETTE_TRACE` never set, so no call trace was written |
| LangGraph | first command of every run failed | `SKILL.md`'s relative script path had no skills root to resolve against |
| LangGraph | run looked hung for two minutes | the host printed nothing while `status` held; it was working |

## Turning off tracing

`deck.py` writes a JSONL record of every call when `$PALETTE_TRACE` is set, and
nothing when it is not. Set it while bringing a host up — it is the only honest
account of what the agent asked Palette for, and an agent's own description of
that is a paraphrase at best.
