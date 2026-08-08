---
name: palette
description: >-
  Create, revise, and render slide decks (PowerPoint .pptx) from a
  natural-language request or pasted material. Use whenever the user wants to
  build a presentation / deck / slides, turn notes or a document into slides,
  or change a deck they are working on. Works in two reviewable steps — a
  markdown plan the user approves, then a rendered .pptx.
---

# Palette — slide deck builder

Palette turns a request (or pasted material) into a rendered slide deck in two
reviewable steps:

1. **Plan** — a markdown outline of the deck, which the user reviews and approves.
2. **Deck** — a rendered PowerPoint `.pptx`, built from the approved plan.

**Always let the user approve the plan before building the deck.** The plan is
the cheap place to iterate; the deck build renders every slide and takes
minutes, so you do not want to build from an unapproved plan.

## Setup

Every command goes through one script, `scripts/deck.py`, which lives beside
this file. It finds the Palette checkout, runs `palette.py` from there, and
puts the output where you asked — so you never change directory and never need
a path into the checkout.

It needs to know where Palette is:

- `$PALETTE_HOME` — the checkout containing `palette.py`.
- `$RITS_API_KEY` — model access. Every command needs it.

If either is missing the first command says so exactly. Relay that to the user
and stop; you cannot fix it from here.

Call the script by its path inside this skill folder. On most hosts that is:

```bash
python skills/palette/scripts/deck.py --help
```

**Do not run `palette.py` yourself**, and do not `cd` anywhere. `palette.py`
lives in the checkout, not in this skill folder, and only runs with its own
directory as the working directory — `deck.py` handles that.

## Commands

### 1. Create a plan

```bash
python skills/palette/scripts/deck.py plan --request "<the user's request>" --out plan.md
```

- Pass the user's request through **as-is** — do not reformat or restructure it.
- Takes about 40 seconds; it is one model call.
- If the user pasted **material for the deck** (notes, content, data, an
  excerpt), ground the plan in it with `--context`:

  ```bash
  python skills/palette/scripts/deck.py plan --request "<request>" \
      --context "<the pasted material>" --out plan.md
  ```

- Grounding files on disk instead: `--source <path>` (repeatable; `.md .txt
  .pdf .docx .pptx`).
- Writes the plan to `--out` and prints it.

### 2. Revise a plan

```bash
python skills/palette/scripts/deck.py edit --instruction "<the change>" --plan plan.md
```

- Use for **any** change: slide count, tone, wording, content, adding or
  removing a slide, changing one specific slide.
- Pass the requested change through as-is.
- Overwrites `--plan` unless you pass `--out`.

### 3. Build the deck

**Three to ten minutes** — longer than a single step may run on some hosts, so
this one does not block. Start it, then poll:

```bash
python skills/palette/scripts/deck.py start  --plan plan.md --out-dir ./deck
python skills/palette/scripts/deck.py status --out-dir ./deck    # repeat until done
```

`start` returns immediately. `status` returns one JSON object; keep calling it
until `"done": true`:

```json
{"state": "done", "done": true, "verified": true,
 "pptx": "/abs/path/to/deck/deck.pptx", "pptx_bytes": 486213, "slide_previews": 9}
```

While it runs you get `"state": "running"` with `elapsed_seconds` and a `note`
saying how that compares to a typical build. **Report that in the same turn as
the next `status` call, never on its own** — a turn of prose with no command in
it reads as a finished answer on some hosts and ends the run mid-build.

There is usually no per-stage `progress` line: `build-deck` prints only when it
finishes, so elapsed time is the honest signal. Do not wait for detail that is
not coming.

`status` also returns `elapsed_seconds`. **Past about fifteen minutes, say so
rather than polling on in silence.** A build that cannot reach the models
retries every stage before giving up — measured at 51 minutes to fail with
nothing but connection timeouts in the log — and to a poller that is
indistinguishable from a slow render. Tell the user it has run long, and offer
the log:

```bash
tail -20 ./deck/build.log
```

`start` accepts `--palette-family <style>` for a specific visual style
(default `ibm_watsonx`). Only pass it if the user asks.

## Workflow

The loop is **build-plan → confirm → [edit-plan → confirm] × N → build-deck**.

1. **User asks for a deck** → run **build-plan** with their request (add
   `--context` if they pasted material). Save the plan.
2. **Confirmation gate (required).** Present the plan and explicitly ask them to
   confirm before anything is built:

   > Here is the plan for your deck. Does this look right? Reply **yes** to
   > build the deck, or tell me what you'd like to change.

   **Never call build-deck until the user has confirmed this plan.**
3. **Branch on the reply:**
   - **Confirms** ("yes", "looks good", "go ahead", "build it") → build the
     deck, then give the user the `.pptx` path.
   - **Asks for changes** → run **edit-plan** with their instruction, then
     **return to step 2** — present the revised plan and ask again.
4. Repeat until confirmed, then build.

## Reporting the result

- **Give the absolute path** to the `.pptx`. A relative path names a working
  directory the user may never have seen — on a sandboxed host it is a
  per-conversation scratch directory, and "saved to `deck/deck.pptx`" sends
  them looking in the wrong place. `deck.py status` returns the absolute path;
  `build-deck --json` prints one too.
- **Say how many slides.** It is the one number that says how much deck they got.
- If the host shows workspace files to the user, mention they can open or
  download it there without touching a terminal.

## Never report a deck that does not exist

A build that exits without writing a usable `.pptx` has failed, however
reasonable its output looked. Rendering can fail after the plan is perfect.

- **`deck.py status` computes `verified` by stat-ing the file** — it is true
  only when `deck.pptx` exists and is large enough to be real. If you did not
  see `"verified": true`, there is no deck.
- Calling `build-deck` directly? Check before you speak:

  ```bash
  ls -l ./deck/deck.pptx
  ```

- A step that timed out tells you *nothing* about the outcome. Poll again
  rather than assuming either way.
- If it failed, relay the `error:` line or the `log_tail` from `status` — that
  text is the actual reason, and paraphrasing it loses what the user needs.

## Guidance

- **Keep your job simple.** Pass the user's words straight through to
  `build-plan` / `edit-plan`. The tools do the parsing, grounding and
  formatting — do not pre-process the request yourself.
- **The confirmation gate is mandatory.** Never jump from request to
  `build-deck`, and never build a plan the user has not seen.
- **Presentation context, not upload.** This host may not support file uploads.
  If the user pastes material into the chat, pass it via `--context`; it is
  used as source material, shaped into slides rather than copied verbatim.
- **Do not hand-write slides.** If Palette cannot run, say so and stop — do not
  fall back to `python-pptx` or `pptxgenjs`, because the result will not match
  what the user expects from Palette.
- **For programmatic parsing**, add `--json` to any `palette.py` command for a
  `{"ok": true, ...}` envelope. `deck.py` always prints JSON.
