# Palette skill benchmark — CUGA and Claude Code

Thirty-three scripted conversations against the palette skill, on two hosts,
scored by one judge that reads the filesystem rather than the transcript.

```
benchmark/
├── inputs/           13 real documents — the dataset
├── corpus.py         reads them
├── cases.py          33 conversations
├── run.py            CUGA, via the agent SDK (automated)
├── claude_run.py     Claude Code (prepare → you paste → collect)
├── show.py           read a run back
└── runs/<timestamp>/
    ├── cuga/<case>/{input, output, cuga_workspace}
    └── claude/<case>/{input, output, utterance.txt}
```

---

## The dataset

`benchmark/inputs/` holds thirteen documents from real Palette use. They are
already in Palette's plan format — `# Title`, `Audience:`, `Preferences:`,
`## sections` — which is how a user actually arrives: with a document, not a
sentence.

| Document | Sections | What it stresses |
|---|---|---|
| `all_hands.md` | 10 | many short sections |
| `all_hands_v2.md` | 13 | the same deck at 11KB — a large paste |
| `benchmark_jan2026_condensed.md` | 14 | figures and tables that must survive |
| `credit_exception_agent.md` | 1 | one slide, with an explicit layout instruction |
| `cuga_hackathon_kickoff.md` | 14 | agenda, logistics, calls to action |
| `example.md` | 7 | consumer subject, no IBM vocabulary to pattern-match |
| `ibm_competitive_strategy.md` | 13 | named competitors, opinionated content |
| `ibm_platform_architecture.md` | 13 | diagram-shaped, hardest to render |
| `ibm_q3_review.md` | 14 | revenue, targets, RAG status |
| `meta_deck.md` / `meta_deck_orig.md` | 10 | near-duplicate inputs |
| `palette.md` | 9 | declares its own length |
| `palette_demo.md` | 5 | short status readout |

Drop your own `.md` in and add a case referencing it — that is the whole
extension path. Nothing is generated.

## The conversations

**13 corpus cases** — one per document. Paste it, say yes, get a deck. These
are the data points: thirteen decks from real material.

**20 interaction cases** — the ones that catch behaviour rather than output:

| Group | Covers |
|---|---|
| `approval` | "yes" is not the only approval — "looks good", "ship it" |
| `context` | pasted material must reach `--context`, not be retyped |
| `edit` | a change must call `edit-plan`, not re-plan from scratch |
| `trap` | `conditional_yes`, `retry`, `fix_it`, `question_then_approve`, `never_approves` |
| `shape` | 12 slides, 2 slides, and no stated count |

The traps are where agents actually fail. `conditional_yes` sends *"yes, but
drop the last slide"* — an approval carrying a condition, which is an **edit**;
building on it hands back the deck the user just corrected. `never_approves`
expects **no deck at all**, so the suite cannot be passed by always building.

---

## Running it

Nothing to export — the Makefile passes `PALETTE_HOME`, and `RITS_API_KEY` is
read from CUGA's `.env`.

```bash
cd project-palette
C=~/code/cuga-agent

make bench-check CUGA=$C            # verify setup, run nothing  ← always first
make bench CUGA=$C CASES=core       # 5 cases, ~30 min
make bench CUGA=$C CASES=corpus     # 13 decks from the documents, ~2 h
make bench CUGA=$C                  # all 33
```

`bench-check` refuses to start if the installed skill has drifted from your
checkout, or if CUGA cannot **discover** it. That second check exists because
its absence cost an evening: undiscovered means the model is never offered the
skill, writes a deck by hand, and every case fails in a way that looks like the
skill's fault.

### Claude Code

Claude Code has no headless CLI here, so its half is prepare-and-collect:

```bash
make bench-claude CUGA=$C CASES=corpus     # writes directories, prints the run sheet
# … open Claude Code in each directory, paste the utterance, send each reply
make bench-collect CUGA=$C                 # harvests the decks and judges them
```

The run sheet gives you, per case, the directory to work in and the exact text
to paste. The working directory matters: `collect` looks for the deck there.

If a `claude` binary ever appears on PATH, `prepare --auto` drives it and the
manual step disappears. It is detected, never assumed.

---

## How a case is scored

**The judge never trusts the agent.** Everything is read from disk:

- a `.pptx` exists and is over 20KB — a failed render can leave a valid stub
- `ppt/slides/slide1.xml` carries **IBM Plex**. Palette's renderer forces it, so
  a deck hand-written with `pptxgenjs` cannot have it. This is the check that
  separates *"a deck exists"* from *"Palette made this"*
- the slide count matches, when the user was specific
- `edit` cases: `edit-plan` appears in the call trace — **and no `plan` after
  it**, because re-planning throws away the revision the user approved
- `context` cases: something was actually passed as `--context`
- `never_approves`: **no deck at all**
- any case: **12 polls or fewer.** Each poll is a model round trip out of a
  finite step budget; a run that spends forty of them was one step limit away
  from failing, and the deck it produced tells you nothing about how close

Both hosts import the same `judge()`. Two hosts, one corpus, one scoring
function — otherwise the comparison is theatre.

## What a run gives you

```
runs/20260811-144029/
├── cuga/q3_review/
│   ├── input/          request.txt · context.md · replies.txt
│   ├── output/         q3_review.pptx · slide-*.png · plan.md
│   └── cuga_workspace/ palette-calls.jsonl · the agent's working tree
└── claude/q3_review/
    ├── input/          the same three files
    ├── utterance.txt   exactly what to paste
    └── output/         the deck, once collected
```

Inputs are saved beside outputs on purpose: a number you cannot reproduce the
question for is not a measurement.

### The call trace is the point

`palette-calls.jsonl` records every `deck.py` invocation — command, arguments,
stdout, duration, exit code. When a run produces the wrong deck the question is
always *"what did the agent actually ask Palette for"*, and the agent's own
account of that is a paraphrase at best.

```
find           0.0s {'root': '.'}
plan          90.3s {'request': 'Build a deck about prompt caching', 'out': 'plan.md'}
edit          90.4s {'instruction': 'make it 3 slides', 'plan': 'plan.md'}
start          0.0s {'plan': 'plan.md', 'out_dir': './deck'}
status        60.2s {'out_dir': './deck'}
```

Tracing is off unless `$PALETTE_TRACE` is set; the harness sets it per case.

```bash
python benchmark/show.py              # newest run
python benchmark/show.py --failures   # only what broke
python benchmark/show.py --verbose    # + the conversation
```

---

## Reading a failure

```
edit_slide_count   FAIL       5    412s  find plan start status
                   ↳ asked for 3 slides, got 5
                   ↳ the user asked for a change but edit-plan was never called
```

Two failures, one cause: the agent read *"make it 3 slides"* as approval and
built the original plan. The call list confirms it — no `edit` between `plan`
and `start`. That is a **skill** problem, fixed in `SKILL.md`, not a Palette
problem.

That is the division this benchmark is for. Palette either renders or it does
not; what varies between hosts and models is whether the agent drives it
correctly.

## What it has already found

Every one of these came from a trace, not from a transcript:

| Found | Fix |
|---|---|
| agent asked the user for `PALETTE_HOME` / `RITS_API_KEY` and stopped, with both set | SKILL.md: never ask, just run — the command names the missing one |
| agent re-ran `plan`/`edit` instead of collecting → two writers, wrong slide count | `plan` guards against a second run, as `start` always has |
| 43 instant polls in one run | `plan-status` and `status` hold up to 60s — 53 calls became 10 |
| agent polled `--out ./skills/palette/SKILL.md` 23 times | a wrong path now answers and names the plans that exist |
| agent wrote the outline itself and never ran a command | SKILL.md: *you do not write the plan* — `build-deck` renders `plan.md` |

## Before you trust a number

- **Reinstall after any skill change.** The agent reads its installed copy;
  `bench-check` refuses if it has drifted.
- **Run `core` before the full set.** Each case is 5–20 minutes, and a skill
  change invalidates the previous measurement. A two-hour run you then redo is
  the expensive mistake.
- **`benchmark/runs/` is gitignored.** Reproducible, large, per-machine — the
  cases and the corpus are the source of truth.
- 64 offline tests cover the harness itself, including that a case exists which
  *forbids* a deck. A benchmark that passes a case it should fail is worse than
  none: it turns an unnoticed regression into evidence there isn't one.
