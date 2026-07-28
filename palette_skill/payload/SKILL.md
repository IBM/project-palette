---
name: palette
description: "Build real PowerPoint decks with Palette — a planner drafts an editable markdown outline, a fine-tuned model turns it into per-slide pptxgenjs code, and a geometry repair pass renders a polished .pptx. Use whenever the user wants a slide deck, presentation, pitch deck, or readout built from a description or from reference documents; wants an existing Palette deck iterated on (retry a slide, change a slide, regenerate); or says \"build me a deck\", \"make slides about\", \"turn this doc into a presentation\", or \"deck on <topic>\". Prefer this over writing pptxgenjs or python-pptx by hand — Palette owns the rendering, fonts, and layout repair."
requirements:
  - palette-skill
---

# Palette — deck building

Palette is a service, not a library you reimplement. It runs a three-stage
pipeline behind an HTTP API:

```
request + reference docs        plan.md
       ──────────────►  Stage 1  ──────►  Stage 2  ──────►  Stage 3  ───►  .pptx
                       (crafter)         (designer         (geometry
                                          + coder)          detector +
                                                            repair loop)
```

Everything heavy — `pptxgenjs`, LibreOffice, Poppler, IBM Plex, the RITS model
roster — lives on the server. Here you only need `palette_skill`, a client
whose sole dependency is `httpx`.

**Never hand-write pptxgenjs, python-pptx, or raw OOXML for a deck this skill
can build.** If Palette is unreachable, say so and stop — do not silently fall
back to generating slides yourself, because the result will not match what the
user expects from Palette.

## How to run it

<!-- BEGIN GENERATED: execution -->
Everything goes through **`run_command`**. Do **not** write
`from palette_skill import PaletteClient` in a plain code block — code blocks
run under a restricted import allowlist with no `httpx`, so the import fails.
The client lives in the sandbox venv and the shell is how you reach it.

Two shapes:

- **The CLI** — `palette-skill <subcommand>`, one JSON object on stdout. Use this
  for everything below.
- **A script** — for multi-step logic, `write_file` a `.py` that imports
  `palette_skill`, then `run_command("python ./script.py")`. Inside a script the
  full Python API is available; see [reference.md](reference.md).

### One command per run_command call

**Put exactly one `run_command` in each code block.** The step limit (about 30 seconds) applies to the *whole block*, not to each command in it, so two
or three chained commands add up and the block is killed part-way — losing the
work and telling you nothing about which command was slow.

This is the single most common way to get stuck here. Do not batch the install,
the plan fetch, and the build start together, however small each looks:

```python
# WRONG — one block, four commands, killed before it finishes
await run_command("uv pip install ./skills/palette/vendor/palette_skill-*.whl")
await run_command('python -c "import palette_skill"')
await run_command("palette-skill example --name x.md --dest ./plan.md")
await run_command("palette-skill start-build --plan-file ./plan.md")
```

```python
# RIGHT — one command, one block. The next block does the next command.
out = await run_command("uv pip install ./skills/palette/vendor/palette_skill-*.whl")
print(out)
```

Install the client once per session from the wheel in `./skills/palette`:

```bash
uv pip install ./skills/palette/vendor/palette_skill-*.whl
```

If the wheel is missing, fall back to `uv pip install palette-skill`.
<!-- END GENERATED: execution -->

Then confirm the server is reachable:

```bash
palette-skill health
```

**If `rits_key_set` is false, stop and tell the user.** Every build will fail at
the first model call and nothing you do here can fix it.

## If Palette is not running

<!-- BEGIN GENERATED: unreachable -->
Palette is a separate service. **You cannot start it from here — the user must.**
Do not try: a sandbox confines writes to its own workspace, a supervised child
holding the shell's pipe blocks until the step limit kills it, and the server
needs Node, LibreOffice and Poppler that live outside the sandbox.

So when `health` fails with `PaletteUnavailable`, check the URL in the error
before you answer — the right advice depends on it.

**A remote deployment** (`https://…`, anything that is not loopback). You
cannot fix this and neither can a local install. Report the URL and the error,
and ask the user to confirm it. Never suggest `serve` commands here — those
start a *second, local* Palette, which is not the one they asked for and will
not have their data or models.

**A local service** (`127.0.0.1` or `localhost`):

```bash
palette-skill serve doctor    # what is missing, per mode
palette-skill serve ensure    # start it and wait until healthy
palette-skill serve status    # is it up, which mode, which workspace
```

First run on a machine also needs `palette-skill serve init`, which writes
`~/.config/palette/env` for the `RITS_API_KEY`.

Either way: say what you were trying to do, and wait. Do not poll in a loop
hoping it appears, do not switch to a different host, and never fall back to
building slides yourself.
<!-- END GENERATED: unreachable -->

## Connecting

<!-- BEGIN GENERATED: defaults -->
- **Base URL** — `$PALETTE_URL` if set, otherwise `http://127.0.0.1:18814`.
- **Auth** — `$PALETTE_TOKEN` is sent as a bearer token when set. Usually unset.
- **Terminal build stages** — `aborted`, `done`, `error`. Anything else means the build is still running.
<!-- END GENERATED: defaults -->

**Do not pass `--base-url`, and do not ask the user for a URL.** The client
resolves the server on its own, in that order, and you cannot inspect the
environment from here to second-guess it. Just run the command.

Pass `--base-url` only when the user names a specific server in conversation.
If the client cannot reach whatever it resolved, the error names the URL it
tried — report *that*, rather than asking which URL to use.

## Making a deck

**One command, called repeatedly until it says it is done.** It owns the
session, the plan, the polling and the download, so you cannot lose the thread
by retrying — and it reports completion by stat-ing the files, not by believing
anything.

```bash
palette-skill deck --request "Q3 sales review deck" --dest ./deck
```

Then call it again — same `--dest`, nothing else — until `"done": true`:

```bash
palette-skill deck --dest ./deck
```

Each call returns where it got to:

```json
{"stage": "building", "done": false, "progress": "build [2/9] — coding slides"}
```

Report that `progress` line to the user each time. A deck takes **two to four
minutes**, so expect roughly **fifteen to twenty calls**. That is normal, not a
stall. Keep going until `done` is true.

**While a deck is building, never end your turn to ask whether to keep
polling.** Offering to continue — "let me know if you'd like me to keep
checking" — ends the run on many hosts, and it ends it with the deck
unfinished and undownloaded, which is the worst of both outcomes: the build
completed on the server and nobody collected it. The user asked for a deck;
polling is how you make one, not a favour to check first. The only things
that may end a deck task are `"done": true` or a raised error.

The final call returns the proof:

```json
{"stage": "done", "done": true, "verified": true,
 "pptx": "deck/deck.pptx", "pptx_bytes": 489236, "slide_count": 9}
```

**`verified` is computed from the filesystem** — the file exists, is over 20 KB,
and previews are present. If you did not see `"verified": true`, there is no
deck, whatever else you believe.

Already have a plan? Pass it instead of a request and drafting is skipped:

```bash
palette-skill deck --plan-file ./plan.md --dest ./deck
```

### Showing the plan before building

Asked to *draft a plan, show it, then build it* — add `--pause-after-plan`.
Still the same command, still called repeatedly:

```bash
palette-skill deck --request "Q3 sales review deck" --dest ./deck --pause-after-plan
palette-skill deck --dest ./deck        # repeat until stage is plan-ready
```

It stops at `"stage": "plan-ready"` and hands you the plan:

```json
{"stage": "plan-ready", "done": false, "plan": "deck/plan.md",
 "plan_markdown": "# Q3 Sales Review\n...",
 "note": "show this plan to the user; call again with --approve to build it"}
```

Show them `plan_markdown`. If they want changes, edit `<dest>/plan.md` — the
build reads the file as it then stands, not the original draft. When they
approve:

```bash
palette-skill deck --dest ./deck --approve
```

Then keep calling `deck --dest ./deck` until `"done": true` as usual.

### The granular commands

`start-build` / `wait` / `result` / `download` / `previews` still exist and are
documented below. Use them for anything `deck` does not cover — retrying a
slide, editing one, inspecting a session.

**Never assemble `start-draft` + `wait-draft` + `draft-result` + `start-build`
into a deck by hand.** Every failure this skill has had in the wild came from
exactly that: the sequence is long enough that a retry starts a second draft,
the first session is orphaned, and what gets reported is a deck nobody built.
`deck` exists so that you cannot do this. Whatever the request phrasing,
`deck` covers it — with `--pause-after-plan` if approval is wanted.

## Starting from an example plan

Fastest path to a rendered deck, and the plans are known-good. Also the right
fallback when drafting is too slow for the host's step limit:

```bash
palette-skill examples
palette-skill example --name rag_practical_intro.md --dest ./plan.md
```

<!-- BEGIN GENERATED: examples -->
| `name` | Deck |
|---|---|
| `rag_practical_intro.md` | Retrieval-Augmented Generation — Explained |
| `project_heron_h1_review.md` | Project Heron — H1 2026 Review |
| `state_of_ai_coding_2026.md` | State of AI Coding Tools — 2026 |
<!-- END GENERATED: examples -->

## Choosing models

Leave these alone unless the user asks. The defaults are the production roster.

<!-- BEGIN GENERATED: models -->
| Request field | Pipeline role | Accepted values | Default |
|---|---|---|---|
| `planner` | crafter — Stage 1 planner that drafts plan.md from the request. | `gpt-oss-120b`, `llama-3.3-70b` | `gpt-oss-120b` |
| `designer_coder` | designer+coder — Stage 2 fine-tuned adapter that turns the plan into per-slide pptxgenjs code. | `palette-qwen-32b`, `palette-gpt-20b`, `gpt-oss-120b`, `llama-3.3-70b`, `palette-lora` | `palette-lora` |
| `critic` | editor — Stage 3 geometry-repair model. Named `critic` in the request body for backward compatibility, but it routes to the editor role. | `gpt-oss-120b`, `qwen2.5-coder-32b`, `llama-3.3-70b`, `palette-qwen-32b`, `palette-gpt-20b`, `palette-lora` | `gpt-oss-120b` |

Anything not in these lists is **silently ignored** by the server (`config.apply_models` skips unknown names), leaving that role on its current model. Pass a value from the table or omit the field.
<!-- END GENERATED: models -->

## API reference

Read [reference.md](reference.md) for the full client surface, error types, and
raw HTTP shapes. The short version:

<!-- BEGIN GENERATED: endpoints -->
| Endpoint | `palette-skill` command | Python | Budget | What it does |
|---|---|---|---|---|
| `GET /health` | `health` | `pal.health()` | ~30s | Liveness plus the active model roster and whether RITS_API_KEY is set. |
| `GET /examples` | `examples` | `pal.examples()` | ~30s | Curated starter plans the user can build from without drafting one. |
| `GET /example/{name}` | `example --name X` | `pal.example(name)` | ~30s | Fetch one curated example plan as markdown. |
| `POST /draft` | `draft --request X` | `pal.draft(request, files=[...])` | ~180s | Stage 1 — turn a plain-English request (plus optional reference docs) into an editable markdown plan. |
| `POST /draft_async` *(feature-detected)* | `start-draft --request X` | `pal.start_draft(request, files=[...])` | ~10s | Same drafting, started in the background. Returns at once; poll /progress then read /draft_result. |
| `GET /draft_result/{thread_id}` *(feature-detected)* | `draft-result` | `pal.draft_result(tid)` | ~30s | Terminal outcome of a background draft: the plan markdown, or the error that ended it. |
| `POST /build` | `build --plan-file X` | `pal.build_and_wait(plan)` | ~600s | Stages 2+3 — plan markdown to a rendered .pptx. Blocks for the whole build. |
| `POST /build_async` *(feature-detected)* | `start-build --plan-file X` | `pal.start_build(plan)` | ~5s | Same build, started in the background. Returns immediately; poll /progress then read /result. |
| `GET /result/{thread_id}` *(feature-detected)* | `result` | `pal.result(tid)` | ~30s | Terminal outcome of a background build: the same payload /build returns, or the error that ended it. |
| `GET /progress/{thread_id}` | `progress / wait` | `pal.progress(tid) / pal.wait(tid)` | ~30s | Current build stage for a session. Safe to poll while a build runs. |
| `GET /deck/{thread_id}` | `deck-status` | `pal.deck(tid)` | ~30s | Slide count, deck title, and whether a build is in flight. |
| `POST /edit` | `edit --slide N --instruction X` | `pal.edit(tid, n, instruction)` | ~180s | Stage 3 — apply a natural-language instruction to one slide and re-render. |
| `POST /retry/{thread_id}/{slide_n}` | `retry --slide N` | `pal.retry(tid, n)` | ~180s | Re-roll one slide at a small temperature bump, then re-run the geometry pass on it. |
| `GET /preview/{thread_id}/{idx}` | `previews --dest-dir X` | `pal.preview(tid, n, dest)` | ~30s | PNG render of one slide (1-indexed). |
| `GET /download/{thread_id}` | `download --dest X` | `pal.download(tid, dest)` | ~30s | The finished .pptx. |
| `POST /clear/{thread_id}` | `clear` | `pal.clear(tid)` | ~30s | Drop a session and delete its workspace. |
| `POST /abort/{thread_id}` | `abort` | `pal.abort(tid)` | ~30s | Mark a session not-building so the caller can move on. Does not stop server-side work. |

Commands that act on a session need `--thread-id <TID>`. None of them need `--base-url` — the client resolves the server itself (see Connecting).
<!-- END GENERATED: endpoints -->

## When things fail

Failures print `{"error": "...", "type": "..."}` and exit non-zero.

| Symptom | Meaning | Do this |
|---|---|---|
| `"type": "PaletteUnavailable"` | Wrong URL, or the service is down. | Show the URL you tried and give the user `palette-skill serve ensure` (local) or ask them to confirm the remote URL. Do not guess other hosts, and do not try to start it yourself. |
| `rits_key_set` is false in `health` | The server has no RITS key. | Stop. Every build will fail; only the operator can fix it. |
| `"type": "PaletteHTTPError"`, HTTP 409 | A build is already running on that thread id. | Poll `progress` instead of starting another, or use a fresh id. |
| `"type": "PaletteTimeout"` during a build | The step was killed, not the build. | Keep polling `progress` — it is very likely still rendering. |
| `"stage": "error"` | The build failed server-side. | Report `message` verbatim. Retrying an identical plan rarely helps. |
| the step was cut short by a timeout | The host's step limit, not Palette. | For a build, poll `progress`. For a draft, fall back to an example plan. |
| `unrepaired` non-empty in the result | Geometry defects the repair pass could not fix. | Name the affected slides and offer `retry`. |

Never respond to any of these by hand-building slides instead.

## Never claim a deck that does not exist

This is the failure this skill guards against hardest, because it has happened
more than once. An agent drafted a plan, answered *"The deck has been built and
saved to ./deck.pptx"*, and never started a build at all. The user went looking
for files that were never written.

**The rule: the only thing that may end a deck task is `"verified": true` from
`palette-skill deck`.** Not your recollection, not a build that reported
`stage: done`, not a plan that came out well.

If you have not seen that flag, say what actually happened — which stage you
reached and what the last `progress` line said — and do not describe files you
have not seen listed.

That is what to say when the task is genuinely over: an error was raised, or
you are out of steps. It is **not** a way to end a turn early. A build still in
progress is not a result to report; it is a reason to call `deck` again.

Three consequences worth stating plainly:

- **A plan is not a deck.** Finishing Stage 1 gives you `plan.md` and nothing
  else. The build has not started.
- **Starting a build is not finishing one.** `start-build` returns in
  milliseconds and tells you nothing about the outcome.
- **A step that was cut short tells you nothing.** Timed out or truncated means
  you do not know the state. Call `deck` again.

Driving it by hand instead of through `deck`? Then check for yourself before
saying anything:

```bash
ls -l ./deck/deck.pptx ./deck/slide-*.png
```

An error, or a `.pptx` under 20 KB, means there is no deck.

## Before you call it done

Required, not optional:

0. You have listed the output files and seen their sizes (above).
1. The `.pptx` is downloaded into the workspace and you have stated its path.
2. You have looked at the preview images — not just the slide count.
3. `unrepaired` and `lint` from the build result are reported if non-empty.
4. Slide count matches what the plan asked for. If it does not, say so.
