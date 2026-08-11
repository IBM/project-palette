# A LangGraph ReAct host for the Palette skill

A third host for `benchmark/`, beside CUGA and Claude Code. It is the thinnest
scaffold that can use a skill at all — a model, a shell, and a loader — and it
runs **watsonx `openai/gpt-oss-120b`** by default.

```
agents/
├── palette_react/
│   ├── skill.py     reads SKILL.md where it lives. Read-only.
│   ├── model.py     watsonx openai/gpt-oss-120b
│   ├── tools.py     load_skill + a shell pinned to the workspace
│   ├── agent.py     create_react_agent, lazy skill loading
│   ├── cli.py       drive it by hand ← start here
│   └── bench.py     run the benchmark's cases, scored by the shared judge
├── tests/           67 offline tests
└── requirements.txt
```

## Nothing outside this folder changed

Not `skills/`, not `benchmark/`, not the `Makefile`, not `pyproject.toml`. The
skill is read where it already sits and the benchmark is imported, not edited —
`bench.py` pulls `judge()` out of `benchmark/run.py` exactly as `claude_run.py`
already does.

`tests/test_isolation.py` enforces this: it fails if `skills/` is dirty, if any
module here imports Palette's internals instead of going through `deck.py`, or
if a run leaves a single byte of `skills/` or `benchmark/` different.

## Why this host exists

The two existing hosts differ in **both** scaffold and model — CUGA runs
gpt-oss-120b, Claude Code runs Claude — so no difference between their columns
can be attributed to either. This host makes the model a flag. Run it on the
same 120B CUGA uses and the scaffold is the only variable.

It is also deliberately dumb: no planner, no todo list, no subagents. If it
passes a case the others fail, the instructions were never the problem.

## Run it standalone

Any interpreter with `langgraph` and `langchain-ibm` works. CUGA's venv already
has both, which is also what the benchmark's other runners use:

```bash
cd project-palette-july25
export PALETTE_HOME=$PWD
PY=~/Documents/GitHub/cuga-agent-july25/.venv/bin/python
ENV=~/Documents/GitHub/cuga-agent-july25/.env      # holds the WATSONX_* vars

# 1. is it wired up? runs nothing.
$PY agents/palette_react/cli.py --check --env-file $ENV

# 2. one deck, scripted: ask, approve, stop
$PY agents/palette_react/cli.py --env-file $ENV --trace \
    --workspace /tmp/palette-smoke \
    "Build a 3-slide deck explaining prompt caching to backend engineers" \
    --reply yes

# 3. the same, but you type the replies
$PY agents/palette_react/cli.py --env-file $ENV "Build a deck about on-call"
```

Or install into a venv of its own: `pip install -r agents/requirements.txt`.

At the end it prints what actually landed on disk, including whether the `.pptx`
carries IBM Plex — the check that separates *a deck exists* from *Palette made
this*, since the renderer forces the font and a hand-written deck cannot have
it. With `--trace` you also get every `deck.py` call and its arguments.

## Run the benchmark

```bash
$PY agents/palette_react/bench.py --check --env-file $ENV
$PY agents/palette_react/bench.py --env-file $ENV --cases core     # 5 cases
$PY agents/palette_react/bench.py --env-file $ENV --cases corpus   # your 13 documents
```

Results land in `benchmark/runs/<timestamp>/react/`, in the same shape as
`cuga/` and `claude/`: `input/` beside `output/`, plus `palette-calls.jsonl`.

## The one design decision worth knowing

`create_react_agent` has no concept of a skill. Its whole surface is `model`,
`tools`, `prompt` and a checkpointer — so the loader is written here, and it
does what a real one does:

1. the frontmatter `name` and `description` go in the system prompt
2. `load_skill(name)` returns the SKILL.md body **verbatim** when the agent asks
3. a `bash` tool, pinned to the case directory, runs `deck.py`

Step 2 is lazy on purpose. Inlining the body up front (`--eager`) answers the
routing question before the model sees the request, which quietly retires
`no_slide_count` — the case that checks whether an agent recognises a deck
request containing no deck vocabulary. Both modes are available and the report
records which ran, so the eager-minus-lazy delta measures what routing costs.

SKILL.md refers to its own script as `skills/palette/scripts/deck.py`, a path
relative to a skills root it cannot know. This host supplies that root **in its
own system prompt**, never by editing the skill. `tests/test_skill.py` pins the
body against the file byte-for-byte, and `tests/test_agent.py` checks the host
prompt contains no Palette vocabulary — location is the host's job, behaviour
is the skill's.

## Two knobs that will bite if you leave them alone

- **`--recursion-limit`** defaults to **120**, not LangGraph's 25. Twenty-five
  is roughly twelve model turns — less than one deck, given the judge alone
  tolerates twelve status polls. Hitting the ceiling looks exactly like the
  agent giving up, so it is set high and printed with every run.
- **`--timeout`** (per shell command) defaults to **300s**. `plan` holds its
  call open for up to 90s and `status` for 60s by design. A 30s default would
  fail every case and look like Palette breaking.

## Tests

```bash
PALETTE_HOME=$PWD $PY -m pytest agents/tests -q      # 67, all offline
```

Nothing reaches watsonx and nothing renders a deck — those cost minutes and
money, and a suite you will not run catches nothing. What is covered is the
wiring: the skill arrives unmodified, the shell stays in the case directory and
inherits `$PALETTE_TRACE`, a timeout is reported as *not* a failure (the skill
is explicit that a cut-short step proves nothing), state survives across turns,
and `bench.py` scores with the same `judge` object as `run.py`.

## Known deprecation

LangGraph 1.0 deprecates `create_react_agent` in favour of
`langchain.agents.create_agent` (same shape; `prompt` becomes `system_prompt`),
with removal in 2.0. It is pinned rather than shimmed: a host that silently
swaps implementations depending on what is installed produces numbers that
cannot be compared across machines. Change it deliberately and re-run the suite.
