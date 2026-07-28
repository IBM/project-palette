# Palette client — full reference

Companion to [SKILL.md](SKILL.md). Read this when you need an exact signature,
an error type, or the raw HTTP shape behind a call.

## Constructing a client

```python
from palette_skill import PaletteClient

pal = PaletteClient("https://palette.example.cloud")     # explicit — preferred
pal = PaletteClient()                                    # $PALETTE_URL, else the default
pal = PaletteClient(url, token="...", timeout=30.0)      # bearer auth, short-call ceiling
```

`timeout` bounds short calls only. Long calls (draft, build, edit, retry) use
their own budget from the contract, so a 30s default does not truncate a draft.

<!-- BEGIN GENERATED: defaults -->
- **Base URL** — `$PALETTE_URL` if set, otherwise `http://127.0.0.1:18814`.
- **Auth** — `$PALETTE_TOKEN` is sent as a bearer token when set. Usually unset.
- **Terminal build stages** — `aborted`, `done`, `error`. Anything else means the build is still running.
<!-- END GENERATED: defaults -->

## Sessions

Every deck lives under a `thread_id`. Palette namespaces its server-side
workspace by that value, so reuse one id for the whole life of a deck — draft,
build, retry, edit, download.

```python
from palette_skill.client import new_thread_id
tid = new_thread_id()                  # "skill-3f9c2a1b8e04"
```

Passing `thread_id=None` to `draft` or `start_build` mints one for you;
`start_build` returns it, so capture the return value.

## Methods

### Read-only

| Call | Returns |
|---|---|
| `pal.health()` | `dict` — `status`, `roster`, `icons`, `rits_key_set` |
| `pal.capabilities()` | `frozenset[str]` — route templates the live server advertises |
| `pal.supports("build_async")` | `bool` |
| `pal.examples()` | `list[dict]` — `[{"file": ..., "label": ...}]` |
| `pal.example(name)` | `str` — plan markdown |
| `pal.deck(tid)` | `dict` — `slide_count`, `title`, `building` |
| `pal.progress(tid)` | `Progress` |

### The whole deck, resumably

`run_deck` is the one you want unless you have a reason not to. It composes
Stage 1, Stages 2–3 and the download into a single resumable step, owning the
thread id, the plan file and the polling so a retry resumes instead of starting
over. Everything below it is the machinery it drives.

```python
from palette_skill import PaletteClient, run_deck

pal = PaletteClient()
state = run_deck(
    pal,
    dest="./deck",              # deck.pptx, slide-NN.png, plan.md and state land here
    request="Q3 sales review",  # or plan_file=... to skip drafting
    max_seconds=100.0,          # how long one call may block; fit it to your step budget
    pause_after_plan=False,     # stop at plan-ready so a human can approve
    approve=False,              # release a paused plan into the build
)
while not state["done"]:
    state = run_deck(pal, dest="./deck", max_seconds=100.0)
```

Progress is kept in `<dest>/.palette-deck.json`, so calls may be minutes apart,
in different processes, or after a crash. Stages: `new` → `drafting` →
(`plan-ready`) → `building` → `done`.

The returned dict always carries `stage` and `done`. A finished deck also
carries the numbers behind the claim:

```python
{"stage": "done", "done": True, "verified": True,
 "pptx": "deck/deck.pptx", "pptx_bytes": 489236,
 "slides": [...], "slide_count": 9}
```

**`verified` is computed by stat-ing the files** — the `.pptx` exists, is over
20 KB, and the previews are on disk. A stored `done` whose files have since
gone re-verifies to `False`, so the state file cannot vouch for itself. Treat
`verified` as the only evidence a deck exists.

Raises `PaletteError` if the draft returns fewer than `MIN_PLAN_CHARS` (200) —
the crafter can return an empty document on an otherwise successful draft, and
building it costs minutes to produce nothing.

### Stage 1

```python
plan: str = pal.draft(
    request,                       # required, non-empty
    thread_id=None,
    planner="gpt-oss-120b",
    files=(),                      # paths to PDF / DOCX / PPTX / MD references
)
```

Raises `PaletteError` if `request` is blank, a reference path does not exist,
or the server returns an empty plan.

### Stages 2 and 3

```python
tid: str = pal.start_build(plan, thread_id=None, palette_family="ibm_watsonx",
                           planner=..., designer_coder=..., critic=...)

snapshot: Progress = pal.wait(tid, max_seconds=25.0, poll_interval=2.0,
                              on_progress=None)

outcome: BuildOutcome = pal.result(tid)
```

`wait` returns when the build reaches a terminal stage **or** `max_seconds`
elapses — whichever comes first. Check `snapshot.terminal` to decide whether to
call it again. That bound is the whole point: it lets a host that kills long
steps still follow a multi-minute build.

`result` raises if the build is still running, and raises with the server's
message if it failed.

```python
outcome: BuildOutcome = pal.build_and_wait(plan, ..., on_progress=None,
                                           max_seconds=600.0)
```

One call, blocks throughout. Uses the background route when available (so
`on_progress` fires as work happens) and a single blocking POST otherwise.

### Refinement

```python
pal.edit(tid, slide_n, instruction)    # -> {"slide_count": ..., "edited": n}
pal.retry(tid, slide_n)                # -> {"slide_count": ..., "retried": n, "geometry": {...}}
```

Both re-render the whole deck server-side, so re-download afterwards.

### Artefacts

```python
pal.download(tid, "./deck/deck.pptx")     # -> Path
pal.preview(tid, 3, "./deck/s3.png")      # -> Path, 1-indexed
pal.previews(tid, "./deck")               # -> list[Path], every slide
```

Parent directories are created for you. Passing a directory to `download` or
`preview` picks a sensible filename inside it.

### Lifecycle

```python
pal.clear(tid)    # -> bool; deletes the server-side workspace
pal.abort(tid)    # -> bool; unblocks the session, does not stop the work
```

`abort` is a UI convenience on the server — the in-flight build keeps running
to completion. It does not free the model calls.

## Types

### `Progress`

| Attribute | Meaning |
|---|---|
| `stage` | `idle`, `build`, `done`, `error`, `aborted` |
| `message` | Human-readable detail from the pipeline |
| `current` / `total` | Slide counter while coding, `0` otherwise |
| `terminal` | `stage` is a terminal stage |
| `failed` | `stage` is `error` or `aborted` |

`str(progress)` renders as `build [7/12] — coding slides`.

### `BuildOutcome`

| Attribute | Meaning |
|---|---|
| `thread_id` | Session key — reuse for download, edit, retry |
| `slide_count` | Slides actually rendered |
| `title` | Deck title the designer chose |
| `elapsed` | Server-side seconds |
| `lint` | Deterministic lint findings |
| `unrepaired` | Slides the geometry pass could not fix — report these |
| `geometry` | Detector/repair statistics |
| `retries` | Per-slide retry counts from the build |
| `raw` | The server payload verbatim, so new fields arrive without a client release |

`str(outcome)` renders as `Vector Databases — 12 slides in 143s`.

## Errors

All inherit `PaletteError`, so one `except PaletteError` catches everything.

| Type | Cause |
|---|---|
| `PaletteUnavailable` | Could not reach the server — DNS, refused, TLS |
| `PaletteTimeout` | Reached it; no answer within the budget |
| `PaletteHTTPError` | Non-2xx. Carries `status_code`, `message`, `url` |
| `PaletteError` | Client-side validation, or a build asked about too early |

`PaletteHTTPError.message` is Palette's own `{"error": ...}` text when present,
so it is safe to show the user verbatim.

Server statuses worth handling: **400** empty plan or instruction, **404**
unknown session or an artefact that is not rendered yet, **409** a build is
already running on that `thread_id`, **500** the pipeline raised.

## Raw HTTP

The client builds every request from `palette_skill.contract`, which is also
what generates this table and what the contract test checks against the live
app. If a row here is wrong, the test suite is broken, not just the docs.

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

## Shell equivalent

Where Python is not available, the same surface is a CLI. Every subcommand
prints one JSON object to stdout.

Making a deck is one subcommand, called until it says it is done:

```bash
palette-skill deck --request "Deck on RAG" --dest ./deck --max-seconds 100
palette-skill deck --dest ./deck --max-seconds 100          # repeat until "done": true
```

With a checkpoint for approval:

```bash
palette-skill deck --request "Deck on RAG" --dest ./deck --pause-after-plan
palette-skill deck --dest ./deck                            # -> "stage": "plan-ready"
# show the user <dest>/plan.md; edit it if they want changes
palette-skill deck --dest ./deck --approve                  # builds the file as it now stands
```

The pieces underneath, for anything `deck` does not cover — retrying a slide,
editing one, inspecting a session:

```bash
palette-skill health
palette-skill draft --request "Deck on RAG" --dest ./plan.md
palette-skill start-build --plan-file ./plan.md
palette-skill wait --thread-id skill-ab12cd34 --max-seconds 100
palette-skill result --thread-id skill-ab12cd34
palette-skill previews --thread-id skill-ab12cd34 --dest-dir ./deck
palette-skill download --thread-id skill-ab12cd34 --dest ./deck/deck.pptx
palette-skill deck-status --thread-id skill-ab12cd34        # slide count, title, build state
```

`build`, `start-build`, `wait`, and `result` mirror the Python methods exactly,
including the bounded-wait behaviour. Do not assemble them into a deck by hand
— that is what `deck` is for, and driving the sequence yourself is how sessions
get orphaned.

`--max-seconds` should fill your host's step budget. Too small and a ten-minute
build costs twenty-odd calls; too large and each call is killed mid-poll.

None of these take `--base-url`. The client resolves the server itself — an
explicit argument, then `$PALETTE_URL`, then the built-in default. Pass
`--base-url` only when the user names a specific server:

```bash
palette-skill --base-url https://other-palette.example.cloud health
```
