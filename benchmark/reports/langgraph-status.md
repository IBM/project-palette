# LangGraph ReAct host — status

**Working. 30/33 on a full sweep. All three failures diagnosed, two fixed at the root, all three passing on retest.**

## Effort

| | |
|---|---|
| Cases | 33 (20 interaction + 13 real documents) |
| Wall clock | 3 hours, unattended |
| Median case | 248s |
| Decks produced | 30, all carrying IBM Plex |

IBM Plex matters: Palette's renderer forces it, so a hand-written deck cannot have it. 30/30 means every deck was genuinely rendered by Palette.

## Behaviour

| | |
|---|---|
| Edits routed to `edit-plan` | 8/8 |
| Revisions discarded by re-planning | 0 |
| Worst status-poll count | 11 of a 12 ceiling |
| Cases at the ceiling | 0 |
| Approval gate | held, including the case that must produce no deck |

## Issues found

| Issue | Status |
|---|---|
| Pasted documents landing in `--request` instead of `--context` — grounding silently lost, deck still looks fine | **Fixed in the skill.** `deck.py` now refuses and names the correction; agents self-correct and pass |
| Guard missed a short document (310 chars over 18 lines) | **Fixed.** Rule now leads on line count rather than size |
| Agent abandoned two running builds after a single poll; both decks completed 5–7 min later, unwatched | **Open.** Did not reproduce on retest, so no fix applied |
| Benchmark rejected `--source`, a legitimate grounding route | **Fixed.** Cost three false failures before it was caught |

`--context` reads 13/17 in the sweep. That is the pre-fix figure; the four misses are the guard cases, all passing now.

## Assessment

Cheapest and most reliable of the three hosts: fastest, nothing to install, fully headless. Its real value is diagnostic — it is the thinnest scaffold in the set, so a case it passes was passed by the *instructions*, not by the harness around them. Two skill defects surfaced here that CUGA had been hitting silently.

## Caveat

One run per case, no repeats. 30/33 is the measurement; the retest is a second sample, not a before-and-after. 33/33 would overstate it.

## Next

CUGA over the same 33, then Claude Code. react and CUGA share a model (watsonx `gpt-oss-120b`), so that pair isolates the scaffold; Claude Code differs in model as well and answers a different question.
