# Palette skill benchmark — CUGA, Claude Code, and a LangGraph ReAct agent

[← Palette README](../README.md) · [CHEATSHEET](../CHEATSHEET.md) ·
[the ReAct host](../agents/README.md) · [the skill itself](../skills/palette/SKILL.md)

Thirty-three scripted conversations against the palette skill, on three hosts,
scored by one judge that reads the filesystem rather than the transcript.

```
benchmark/
├── cases.py          33 conversations
├── corpus.py         reads the documents at $PALETTE_BENCH_INPUTS
├── verdict.py        judge() — the one definition of a pass
├── run.py            host: CUGA, via the agent SDK (automated)
├── claude_run.py     host: Claude Code (prepare → you paste → collect)
├── react_run.py      host: LangGraph ReAct on watsonx (automated)
├── show.py           read one run in depth
├── compare.py        put the hosts side by side
└── runs/<timestamp>/
    ├── cuga/<case>/{input, output, cuga_workspace}
    ├── claude/<case>/{input, output, utterance.txt}
    └── react/<case>/{input, output, palette-calls.jsonl}
```

## Before anything: one config file

Everything the benchmark needs lives in **`~/.config/palette/env`** — the file
`make serve-init` already creates, mode 600, and the documented home for the
RITS key. The Makefile loads it for every `bench-*` target, so nothing needs
exporting in your shell:

```bash
RITS_API_KEY=…              # CUGA's model calls
WATSONX_APIKEY=…            # the ReAct host
WATSONX_URL=…
WATSONX_PROJECT_ID=…
PALETTE_BENCH_INPUTS=…      # the corpus documents
```

`PALETTE_ENV=<path>` points at a different file. `PALETTE_HOME` is not needed —
the Makefile passes `$(PWD)`, which cannot go stale the way a written-down path
can.

Two things worth knowing about how it loads:

- **The file beats your shell.** `set -a; . file` assigns unconditionally, so
  `FOO=x make bench-cuga` loses to a `FOO` in the file. Edit the file, or point
  `PALETTE_ENV` elsewhere.
- **CUGA's `.env` is read but never sourced.** It contains values a shell would
  try to execute (`channels:read` on one line is a Slack scope, not a command).
  The ReAct runner parses it via `--env-file` instead, and skips anything
  already set — so it is a fallback for `WATSONX_*`, not a second source of
  truth. Put those in the config file and it stops being consulted.

**The corpus documents are internal material and are not checked in.**
`$PALETTE_BENCH_INPUTS` names the directory. Unset, every command here stops
rather than starting: there is deliberately no default and no in-repo fallback,
because a corpus that silently resolves to an empty directory produces thirteen
cases over nothing and reports it as a result.

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

`$PALETTE_BENCH_INPUTS` holds thirteen documents from real Palette use. They
are already in Palette's plan format — `# Title`, `Audience:`, `Preferences:`,
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

One target per host, and one that runs them all. `PALETTE_HOME` and
`RITS_API_KEY` are handled by the Makefile; `$PALETTE_BENCH_INPUTS` is yours.

```bash
cd project-palette
export PALETTE_BENCH_INPUTS=~/palette-benchmark-inputs
C=~/code/cuga-agent

make bench-setup CUGA=$C            # once: install the skill where hosts read it
make bench-check CUGA=$C            # verify every host, run nothing  ← always first

make bench-all   CUGA=$C CASES=core # both automated hosts, then the comparison
make bench-cuga  CUGA=$C CASES=core # one host at a time
make bench-react CUGA=$C CASES=core
make bench-claude CUGA=$C CASES=core # writes the run sheet; you paste
make bench-collect CUGA=$C           # …then harvest what Claude built

make bench-compare                  # side by side, newest run per host
make bench-show FAILURES=1          # newest run, in depth
make bench-clean                    # delete every recorded run
```

Leave `CASES=` off for all 33. `CASES=core` is 5 and takes ~30 min per host;
`CASES=corpus` is the 13 documents and takes ~2 h per host.

**`bench-all` is sequential, not parallel.** Both automated hosts render decks,
and two builds competing for the machine measures contention rather than the
skill. It also keeps going if one host fails, so a broken CUGA still leaves you
the react column and the comparison.

Every target refuses to start unless `$PALETTE_BENCH_INPUTS` names a directory
with documents in it, and unless `CUGA=` points at a checkout with a usable
interpreter. `bench-check` additionally refuses if the installed skill has
drifted from your checkout, or if CUGA cannot **discover** it. That last check
exists because its absence cost an evening: undiscovered means the model is
never offered the skill, writes a deck by hand, and every case fails in a way
that looks like the skill's fault.

### Reading the result

`show.py` answers *what did this host do* — every call, every argument, and with
`--verbose` the conversation. `compare.py` answers *where do the hosts
disagree*:

```
case              cuga              react
approve_casually  pass  6sl   386s  pass  7sl   200s
conditional_yes   pass  5sl   207s  pass  5sl   333s
edit_slide_count  pass  3sl   302s  pass  3sl   616s
pasted_notes      FAIL  7sl   372s  pass  7sl   172s
                    cuga: pasted material was never passed as --context
plain_request     pass  5sl   323s  pass  5sl   299s
```

It prints the model each host ran and warns when they differ, because a table
comparing scaffolds is only about scaffolds when the model is held fixed.

### Claude Code

Claude Code has no headless CLI here, so its half is prepare-and-collect:

```bash
make bench-claude CUGA=$C CASES=corpus     # writes directories, prints the run sheet
# … for each case:  cd <dir> && source env.sh, then open Claude Code and paste
make bench-collect CUGA=$C                 # harvests the decks and judges them
```

**Two steps per case, and both matter.**

`cd` into the case directory, because `collect` looks for the deck there.
`source env.sh`, because it sets `$PALETTE_TRACE` — and `deck.py` writes its
call trace only when that is set. The judge reads the trace to see whether
`edit-plan` was called and whether pasted material reached `--context`, which
**24 of the 33 cases depend on**. Skip it and they all fail on a missing trace
while the decks sit on disk looking perfectly correct. `env.sh` is written per
case; the run sheet prints the `cd … && source env.sh` line for you.

Then paste `utterance.txt` and send each reply as the agent hands back.

#### Making it headless

`prepare --auto` drives the CLI if one is on PATH — detected, never assumed. To
get there you need:

1. **The CLI installed** — `npm i -g @anthropic-ai/claude-code`. There was none
   on the machine this was written on, so **the `--auto` path has never run**.
   Treat the first attempt as a shakedown, not a measurement.
2. **Credentials in the environment Claude Code inherits** — `PALETTE_HOME` and
   `RITS_API_KEY`. `_drive` sets `PALETTE_TRACE` and `PALETTE_HOME` itself.
3. **A permission mode that does not stop to ask.** Claude Code prompts before
   running shell commands; a headless run that blocks on a prompt looks like a
   hang. Whichever flag your version uses, it has to be settled before a
   33-case run.

One thing `_drive` now gets right that it did not: `claude -p` is **one-shot**.
Sending each reply as its own invocation starts a fresh session, so `"yes"`
arrives with no plan to approve and every multi-turn case — the traps, the
edits, the whole reason the suite exists — measures nothing. Replies after the
first now pass `--continue`, which resumes that directory's conversation.

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

Every host imports the same `judge()` from **`verdict.py`**. Three hosts, one
corpus, one scoring function — otherwise the comparison is theatre.
`tests/test_benchmark.py` asserts it per runner: that each imports from
`verdict`, that the imported object is the same function, and that none defines
a `judge` of its own. A local one would shadow the import silently and the run
would still print a number.

It lived inside `run.py` until there were three hosts, which meant the Claude
and ReAct runners took their verdict *from the CUGA runner* — working, but
inverted, and one stray module-level import in `run.py` from breaking both.

## Adding a host

A host is four things. Satisfy them and `judge()` scores it like any other:

1. **Write `input/` beside `output/`** — `request.txt`, `context.md`,
   `replies.txt`. A number whose question you cannot reproduce is not a
   measurement.
2. **Set `$PALETTE_TRACE` per case**, and clear it afterwards. `deck.py` writes
   the call trace only when it is set; a leaked path appends the next case's
   calls to the previous case's file.
3. **Drive the opening message, then one reply each time the agent yields.** The
   opening is `request` plus `context` pasted together — splitting them into
   `--request` and `--context` is the agent's job, and one of the things scored.
4. **Call `judge(case, result)`** from `verdict.py`. Not a local equivalent.

Then add the filename to `RUNNERS` in `tests/test_benchmark.py` and a
`bench-<host>` target to the Makefile. The tests hold it to the rules from
there — a test fails if the Makefile has no target for it.

Write results to `runs/<timestamp>/<host>/`, and record **what actually ran**:
the model, and any budget that could have ended the run early. `react_run.py`
reports `model`, `provider`, `skill_loading` and `recursion_limit` for that
reason — a run that exhausted its step budget looks exactly like an agent that
gave up, and only the report can tell them apart.

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

---

## Where to go next

| | |
|---|---|
| a case failed and you want the raw calls | `make bench-show FAILURES=1`, then the run's `palette-calls.jsonl` |
| the hosts disagree and you want to see where | `make bench-compare` |
| something is broken and you want to reset it | [`CHEATSHEET.md`](../CHEATSHEET.md) |
| you want to drive the ReAct host by hand | [`agents/README.md`](../agents/README.md) |
| you want to change what the agent is told | [`skills/palette/SKILL.md`](../skills/palette/SKILL.md) — and re-install before measuring again |
| you want Palette itself | [the README](../README.md) |
