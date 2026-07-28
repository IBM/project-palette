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

## Builds are slow — never block on one

A 10–15 slide deck takes **two to four minutes** — far longer than a single
step is usually allowed to run. So start the build, then poll it across several
short steps, one command at a time:

```bash
# Step 1 — start it. Returns immediately with a thread id.
palette-skill start-build --plan-file ./plan.md
```

```bash
# Step 2 — repeat until "terminal": true. Each call is bounded, so it
# always returns promptly whether or not the build has finished.
palette-skill wait --thread-id <TID> --max-seconds 25
```

```bash
# Step 3 — once terminal and not failed.
palette-skill result --thread-id <TID>
```

Report each `wait` snapshot to the user as it arrives — a silent three-minute
gap reads as a hang. A typical build needs six to ten `wait` calls; that is
normal, not a stall. Keep going until `terminal` is true.

Never wrap the whole build in one long call. `palette-skill build` blocks for
the entire two-to-four minutes and will be killed mid-flight, leaving the deck
rendering server-side while you see only an error. Use it only if the host has
no step limit.

If `start-build` reports no `/build_async` route, that deployment predates
background builds. Say so and stop; either the step limit must be raised past
600s or the server needs redeploying. Do not silently retry.

## Workflow

**1. Draft a plan.** Never skip straight to a build from a one-line request —
the plan is the artefact the user edits, and it is far cheaper to fix than a
rendered deck.

Drafting takes **60–90 seconds**, so it has the same start/poll/collect shape as
a build. One command per block:

```bash
# Step 1 — start it. Returns immediately with a thread id.
palette-skill start-draft \
  --request "Deck explaining vector databases to backend engineers"
```

```bash
# Step 2 — repeat until "terminal": true.
palette-skill wait-draft --thread-id <TID> --max-seconds 25
```

```bash
# Step 3 — collect the plan into a file, not into your context.
palette-skill draft-result --thread-id <TID> --dest ./plan.md
```

Read `./plan.md` back and show it to the user.

Reference documents (PDF, DOCX, PPTX, Markdown) shape the plan — pass paths that
already exist in the workspace, repeating `--file` per document:

```bash
palette-skill start-draft --request "Turn this into a 12-slide readout" \
  --file ./uploads/q3.pdf --file ./uploads/notes.md
```

Reuse the **same thread id** for the build that follows — one session carries the
plan and the deck together.

`palette-skill draft` is the blocking equivalent. It will be killed by a step
limit; use it only where there is none. If `start-draft` reports no
`/draft_async` route, that deployment predates background drafting — say so, and
offer an example plan instead.

**2. Show the plan and get agreement.** Show it as markdown and ask whether to
build or adjust. Apply their changes to `./plan.md` — it is just a text file, so revising it
needs no server round-trip.

**3. Build it** with the start / wait / result loop above, reading the plan from
`./plan.md`.

**4. Show the result.** Save the previews and the deck into the workspace so the
user can open them:

```bash
palette-skill previews --thread-id <TID> --dest-dir ./deck
palette-skill download --thread-id <TID> --dest ./deck/deck.pptx
```

Then actually look at a few preview PNGs before declaring success — the geometry
pass fixes measurable defects, not bad content.

**5. Iterate.** Two different tools, and picking the wrong one wastes a minute:

| The user says | Use | Why |
|---|---|---|
| "slide 5 is ugly / it came out wrong" | `retry --thread-id <TID> --slide 5` | Re-rolls that slide at a small temperature bump. No new instruction. |
| "make slide 5 a table", "swap bullets 2 and 3" | `edit --thread-id <TID> --slide 5 --instruction "..."` | Applies a specific instruction to that slide. |
| "restructure the deck", "add a section" | edit `./plan.md`, then build again | Whole-deck changes belong in the plan. |

Both `retry` and `edit` re-render server-side and can take a couple of minutes,
so they may hit a step limit — if one does, poll `deck --thread-id <TID>` until
`building` is false rather than reissuing the command. Download again afterwards
to pick up the change, and reuse the same thread id throughout: it is the
session key for the whole deck.

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
| `GET /deck/{thread_id}` | `deck` | `pal.deck(tid)` | ~30s | Slide count, deck title, and whether a build is in flight. |
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

The failure this skill guards against hardest: reporting a finished deck when
nothing was built. It has happened — an agent started a draft, answered
*"The deck has been built and saved to ./deck.pptx"* eighteen seconds later,
and never called `start-build` at all. The user went looking for a file that
was never written.

So, before you say anything about a finished deck, **run this and read the
output**:

```bash
ls -l ./deck/deck.pptx ./deck/slide-*.png
```

If that errors, or the `.pptx` is under 20 KB, **there is no deck**. Say what
actually happened — which stage you reached, and what the last `wait` reported —
and do not describe files you have not seen listed.

Three rules that follow from it:

- **A plan is not a deck.** Finishing Stage 1 means you have `./plan.md` and
  nothing else. The build has not started.
- **Starting a build is not finishing one.** `start-build` returns in
  milliseconds and tells you nothing about the outcome. Only `result` does, and
  only after `wait` reports `"terminal": true`.
- **Never infer an outcome from a step that was cut short.** A timed-out or
  truncated step means you do not know the state — poll again.

## Before you call it done

Required, not optional:

0. You have listed the output files and seen their sizes (above).
1. The `.pptx` is downloaded into the workspace and you have stated its path.
2. You have looked at the preview images — not just the slide count.
3. `unrepaired` and `lint` from the build result are reported if non-empty.
4. Slide count matches what the plan asked for. If it does not, say so.
