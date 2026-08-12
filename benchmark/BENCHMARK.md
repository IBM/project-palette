# Palette skill benchmark — CUGA, Claude Code, and a LangGraph ReAct agent

Thirty-three scripted conversations against the palette skill, on three hosts,
scored by one judge that reads the filesystem rather than the transcript.

```
benchmark/
├── inputs/           13 real documents — the dataset
├── corpus.py         reads them
├── cases.py          33 conversations
├── run.py            CUGA, via the agent SDK (automated)
├── claude_run.py     Claude Code (prepare → you paste → collect)
├── react_run.py      LangGraph ReAct on watsonx (automated)
├── show.py           read a run back
└── runs/<timestamp>/
    ├── cuga/<case>/{input, output, cuga_workspace}
    ├── claude/<case>/{input, output, utterance.txt}
    └── react/<case>/{input, output, palette-calls.jsonl}
```

The ReAct host's agent lives in [`agents/palette_react/`](../agents/README.md);
`react_run.py` is only the runner. The other two hosts are products you install
the skill into; that one is a scaffold this repo builds, which is the point of
having it.

---

## The three hosts, and why there are three

|  | Scaffold | Model | Automated |
|---|---|---|---|
| `cuga` | CUGA agent SDK | `openai/gpt-oss-120b` (RITS) | yes |
| `claude` | Claude Code | Claude | no — you paste |
| `react` | LangGraph `create_react_agent` | `openai/gpt-oss-120b` (watsonx) | yes |

The first two differ in **both** scaffold and model, so no difference between
their columns can be attributed to either. The third fixes that: it runs the
same 120B CUGA runs, so the scaffold is the only variable. Point `--model`
elsewhere and you get the other comparison instead.

It is also deliberately the thinnest scaffold of the three — a model, a shell,
and a loader that hands over `SKILL.md` when asked. No planner, no todo list,
no subagents. **A case it passes was passed by the instructions**, not by the
harness around them, which is what makes it a useful floor for the other two.

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

### The ReAct host

Fully automated, and the only host where the model is a flag:

```bash
make bench-react-check CUGA=$C            # verify, run nothing  ← always first
make bench-react CUGA=$C CASES=core       # 5 cases
make bench-react CUGA=$C CASES=corpus     # the 13 documents
make bench-react CUGA=$C EAGER=1          # inline the skill instead of offering it
make bench-react-test                     # its own tests: offline, no model, ~3s
```

`CUGA=` is needed for two things only — an interpreter that has `langgraph` and
`langchain-ibm`, and the `.env` holding the `WATSONX_*` credentials. This host
does not drive CUGA, and **the skill does not need to be installed anywhere**:
it reads `skills/palette` straight out of the checkout. That is why there is no
drift check for it — there is nothing to drift from.

To drive it by hand instead, see [`agents/README.md`](../agents/README.md);
`agents/palette_react/cli.py` builds one deck and prints every command as it
runs.

**`EAGER=1` measures something different.** By default the system prompt offers
only the skill's name and description, and the body arrives through a
`load_skill` tool — so whether the agent recognises that a request needs the
skill is still a real decision, and `no_slide_count` (tagged `routing`) still
means something. `--eager` inlines the body and answers that question in
advance. Both are legitimate; the report records which ran, and the difference
between them is what routing costs.

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

Every host imports the same `judge()` from `run.py`. Three hosts, one corpus,
one scoring function — otherwise the comparison is theatre. `tests/
test_benchmark.py` asserts it per runner, including that none of them defines a
`judge` of its own: a local one would shadow the import silently, and the run
would still print a number.

Adding a fourth host means adding its filename to `RUNNERS` in that file. The
tests then hold it to the same rules.

## What a run gives you

```
runs/20260811-144029/
├── cuga/q3_review/
│   ├── input/          request.txt · context.md · replies.txt
│   ├── output/         q3_review.pptx · slide-*.png · plan.md
│   └── cuga_workspace/ palette-calls.jsonl · the agent's working tree
├── claude/q3_review/
│   ├── input/          the same three files
│   ├── utterance.txt   exactly what to paste
│   └── output/         the deck, once collected
└── react/
    ├── report.json     + model, skill_loading mode, recursion_limit
    └── q3_review/
        ├── input/            the same three files
        ├── output/           the deck, previews, plan.md
        └── palette-calls.jsonl
```

The react report records **which model answered, which skill-loading mode ran,
and what the step budget was**. The first because the model is that host's whole
reason for existing; the last because a run that exhausted its recursion limit
looks exactly like an agent that gave up.

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
