# Benchmark — the palette skill under CUGA

Drives the skill through **CUGA's agent SDK** with a scripted user, one
conversation at a time, and reports what Palette was actually asked to do.

Model under test: **`openai/gpt-oss-120b`** — whatever CUGA is configured with.

```bash
export PALETTE_HOME=$PWD
export RITS_API_KEY=<key>            # needs the VPN
export CUGA_HOME=~/code/cuga-agent

# Run it with CUGA's interpreter — the harness drives CUGA, so it needs
# CUGA's dependencies, not Palette's.
PY=$CUGA_HOME/.venv/bin/python

$PY benchmark/run.py --check         # verify the setup, run nothing
$PY benchmark/run.py --cases core    # a handful (~5 cases)
$PY benchmark/run.py                 # all 20
```

## Why this exists

A deck is never one turn. The user asks, the agent proposes a plan, and then
the user says something — and *"yes"* is only one of the things they say. The
failures that matter live in the other things: building a plan nobody approved,
re-planning when the user wanted an edit, losing pasted material on the second
pass. None of those show up in a single-turn test, and exercising them by hand
does not scale past a couple of tries when each run costs minutes.

So: a scripted user, twenty conversations, and a verdict computed from the
filesystem rather than from what the agent said about itself.

## What a case is

```python
Case(
    name="conditional_yes",
    request="Build a 6-slide deck on our incident response process",
    replies=("yes, but drop the last slide", "yes"),
    covers="an approval carrying a condition is an EDIT",
    tags=("core", "edit", "trap"),
)
```

`replies` are sent in order, one each time the agent finishes a turn and hands
back. A case with two replies exercises two decision points.

`expect_deck=False` marks a conversation that **should not** produce a `.pptx` —
the user never approved. An agent that builds anyway has skipped the
confirmation gate, and that is a failure even though the artifact looks fine.

## Coverage

| Group | Cases | What it is for |
|---|---|---|
| approval | `plain_request`, `approve_casually`, `approve_tersely` | "yes" is not the only approval |
| context | `pasted_notes`, `pasted_incident_report`, `pasted_comparison`, `context_plus_edit` | pasted material must reach `--context`, not get retyped into the request |
| edit | `edit_slide_count`, `edit_tone`, `edit_add_slide`, `edit_then_edit_again` | a change must call `edit-plan`, not re-plan from scratch |
| traps | `conditional_yes`, `retry`, `fix_it`, `question_then_approve`, `never_approves` | the replies that are easy to misread |
| shape | `long_deck`, `very_short_deck`, `no_slide_count` | 12 slides, 2 slides, and no stated count |

## The verdict never trusts the agent

An agent can describe a deck it never built, and has. Every check reads the
filesystem:

- a `.pptx` exists and is over 20KB (a failed render can leave a valid stub)
- `ppt/slides/slide1.xml` carries **IBM Plex** — Palette's renderer forces it,
  so a deck hand-written with `python-pptx` cannot have it
- the slide count matches what the user asked for, when they were specific
- for an `edit` case, `edit-plan` actually appears in the call trace
- for a `context` case, something was actually passed as `--context`
- for `never_approves`, **no deck exists at all**

## What you get back

Everything lands under `benchmark/runs/<timestamp>/<case>/`:

```
palette-calls.jsonl     every deck.py call: command, arguments, output, duration
<thread-id>/deck/       the workspace, including deck.pptx and slide previews
report.json             all cases, machine-readable
```

The call trace is the point of the harness. When a run produces the wrong deck,
the question is always *"what did the agent actually ask Palette for"* — and
the agent's own account of that is a paraphrase at best. This is the real thing:

```json
{"command": "plan",
 "args": {"request": "Turn these Q3 notes into an exec deck",
          "context": "Q3 platform review — raw notes\nAdoption: 41% …"},
 "seconds": 58.2, "exit_code": 0,
 "stdout": "{\"state\": \"done\", \"done\": true, …}"}
```

Tracing is off unless `$PALETTE_TRACE` is set, so it costs nothing in normal
use. The harness sets it per case.

## Reading a failure

```
edit_slide_count   FAIL       5    412s  find plan start status
                   ↳ asked for 3 slides, got 5
                   ↳ the user asked for a change but edit-plan was never called
```

Two failures, one cause: the agent read *"make it 3 slides"* as approval and
built the original plan. The call list confirms it — no `edit` between `plan`
and `start`. That is a skill problem, not a Palette problem, and it is fixed in
`SKILL.md` rather than in the pipeline.

## Options

| Flag | |
|---|---|
| `--check` | verify setup and exit — including that the installed skill matches this checkout |
| `--list` | show the cases and what each covers |
| `--case <name>` | run one (repeatable) |
| `--cases <tag>` | run a group: `core`, `edit`, `context`, `trap`, `shape`, `approval` |
| `--timeout <s>` | per-turn ceiling, default 1500 |
| `--out <dir>` | where runs land, default `benchmark/runs/` |

## Before you trust a run

`--check` refuses to start if the installed skill has drifted from this
checkout. That is deliberate: the agent reads its installed copy, so measuring
against a stale one tells you about instructions you have already changed.
Reinstall with `make skill-install CUGA=$CUGA_HOME` and run again.

Timings for scale: a plan is 40–180s and a deck 3–10 minutes, so a full
twenty-case run is a couple of hours. Start with `--cases core`.
