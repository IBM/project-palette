"""The conversations the benchmark runs.

A deck is never one turn. The user asks, the agent proposes a plan, and then the
user says something — and "yes" is only one of the things they say. These cases
exist to cover the *other* things, because that is where the agent goes wrong.

Each case is a scripted user: an opening request, then a reply for each turn the
agent hands back. The harness sends the next reply whenever the agent stops and
waits, so a case with two replies exercises two decision points.

`expect_deck=False` marks a case that should end without a `.pptx` — the user
never approved. An agent that builds anyway has skipped the confirmation gate,
which is a real failure even though the artifact looks fine.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent))

from corpus import read, sections  # noqa: E402


@dataclass(frozen=True)
class Case:
    name: str
    request: str
    #: Pasted material. Goes to `--context`, not concatenated into the request.
    context: str = ""
    #: Sent in order, one per time the agent yields.
    replies: tuple[str, ...] = ("yes",)
    expect_deck: bool = True
    #: Slide count the user asked for, when they were specific.
    expect_slides: int | None = None
    #: What this case is here to catch.
    covers: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------
# Material the agent is meant to pass through `--context` rather than retype.
# --------------------------------------------------------------------------

Q3_NOTES = """\
Q3 platform review — raw notes

Adoption: 41% of enterprise deck production moved off manual authoring.
Median time-to-first-draft: 4.5 hours -> 11 minutes, across 38 teams.
Brand review passes: 3 rounds -> 1, since the renderer enforces IBM Plex.

Still rough:
- image sourcing is manual and slow
- decks over 20 slides need a geometry repair pass, ~90s
- no template story for regulated business units yet

Asks for Q4: an image service, a template registry, and headcount for one
designer to own the visual system.
"""

INCIDENT_REPORT = """\
# Incident 2026-07-14 — retrieval latency

## Summary
p99 latency on the retrieval path rose from 180ms to 4.2s for 47 minutes.
Caused by an index rebuild that ran without the usual read replica.

## Timeline
- 02:14 index rebuild triggered by the nightly job
- 02:19 p99 crosses 1s; no alert (threshold was 5s)
- 02:41 first customer report
- 03:01 rebuild paused, traffic shifted to the stale replica
- 03:06 latency back under 250ms

## Contributing factors
- The alert threshold was set for the old index, never revised.
- The rebuild job had no concurrency guard.
- Runbook pointed at a dashboard that had been deleted.

## Actions
1. Alert at 800ms, page at 2s.
2. Concurrency guard on the rebuild job.
3. Runbook review, quarterly.
"""

VECTOR_DB_COMPARISON = """\
Vector store evaluation, shortlist of four.

Pinecone — managed only, no self-host. Full hybrid search. Multi-region.
Milvus — Apache-2, self-host, strongest raw throughput. Metadata filters limited.
Weaviate — Apache-2, good hybrid search, replication is active-passive only.
Qdrant — MIT, self-host, strong filtering, cross-cluster sync is partial.

Our constraints: must run in our own VPC (rules out Pinecone), needs metadata
filtering at query time, and we want an open licence.
"""


CASES: tuple[Case, ...] = (
    # ---------------------------------------------------------------- happy
    Case(
        name="plain_request",
        request="Build me a 5-slide deck explaining retrieval-augmented generation to backend engineers",
        replies=("yes",),
        expect_slides=5,
        covers="the straight path: request, plan, approve, build",
        tags=("core",),
    ),
    Case(
        name="approve_casually",
        request="Make a short deck on why vector databases are not always the right answer",
        replies=("looks good, go ahead",),
        covers="approval that is not the word 'yes'",
        tags=("core", "approval"),
    ),
    Case(
        name="approve_tersely",
        request="Build a 4-slide deck introducing our team's on-call rotation",
        replies=("ship it",),
        expect_slides=4,
        covers="two-word approval; agents sometimes read this as a new instruction",
        tags=("approval",),
    ),

    # ------------------------------------------------------------- with context
    Case(
        name="pasted_notes",
        request="Turn these Q3 notes into an exec deck",
        context=Q3_NOTES,
        replies=("yes",),
        covers="pasted material must reach --context, not be retyped into --request",
        tags=("core", "context"),
    ),
    Case(
        name="pasted_incident_report",
        request="Make a 6-slide postmortem deck from this incident report",
        context=INCIDENT_REPORT,
        replies=("yes",),
        expect_slides=6,
        covers="markdown context with headings and lists",
        tags=("context",),
    ),
    Case(
        name="pasted_comparison",
        request="Build a deck helping us choose a vector store, based on this",
        context=VECTOR_DB_COMPARISON,
        replies=("yes",),
        covers="context that implies a recommendation, not just a summary",
        tags=("context",),
    ),

    # ------------------------------------------------------------------- edits
    Case(
        name="edit_slide_count",
        request="Build a deck about prompt caching",
        replies=("make it 3 slides", "yes"),
        expect_slides=3,
        covers="the commonest edit; must call edit-plan, not re-plan from scratch",
        tags=("core", "edit"),
    ),
    Case(
        name="edit_tone",
        request="Build a 5-slide deck on our API rate limits",
        replies=("can you make the tone more casual?", "yes"),
        covers="a tone edit phrased as a question — still an edit, not a question about the plan",
        tags=("edit",),
    ),
    Case(
        name="edit_add_slide",
        request="Build a 4-slide deck on model evaluation",
        replies=("add a slide at the end about cost", "yes"),
        expect_slides=5,
        covers="an edit that changes the slide count implicitly",
        tags=("edit",),
    ),
    Case(
        name="edit_then_edit_again",
        request="Build a deck about feature flags",
        replies=("make it shorter", "now make the tone more formal", "yes"),
        covers="two edits in a row; the second must edit the revised plan, not the original",
        tags=("edit", "multi"),
    ),
    Case(
        name="conditional_yes",
        request="Build a 6-slide deck on our incident response process",
        replies=("yes, but drop the last slide", "yes"),
        covers="an approval carrying a condition is an EDIT — building here gives them the deck they just corrected",
        tags=("core", "edit", "trap"),
    ),

    # ------------------------------------------------- rejection and recovery
    Case(
        name="reject_and_redirect",
        request="Build a deck about Kubernetes operators",
        replies=("no, that's not what I meant — focus on the reconciliation loop only", "yes"),
        covers="outright rejection with a redirect; must edit or re-plan, never build",
        tags=("edit", "reject"),
    ),
    Case(
        name="retry",
        request="Build a 3-slide deck on our SLO targets",
        replies=("retry", "yes"),
        expect_slides=3,
        covers="a bare 'retry' — ambiguous; must not be read as approval",
        tags=("reject", "trap"),
    ),
    Case(
        name="fix_it",
        request="Build a 5-slide deck about our data retention policy",
        replies=("fix the second slide, it's too vague", "yes"),
        covers="'fix ...' is an edit instruction and must be passed verbatim",
        tags=("edit", "trap"),
    ),
    Case(
        name="question_then_approve",
        request="Build a 4-slide deck on our release process",
        replies=("why is there no slide about rollbacks?", "ok that makes sense, build it"),
        covers="a question about the plan must be answered, not built and not edited",
        tags=("question", "trap"),
    ),
    Case(
        name="never_approves",
        request="Build a deck about our authentication flow",
        replies=("actually, hold off for now",),
        expect_deck=False,
        covers="the confirmation gate: an agent that builds here ignored the user",
        tags=("gate", "trap"),
    ),

    # ------------------------------------------------------------ shape stress
    Case(
        name="long_deck",
        request="Build a 12-slide deck covering our whole ML platform: ingestion, training, serving, and monitoring",
        replies=("yes",),
        expect_slides=12,
        covers="a long deck; geometry repair runs more passes",
        tags=("shape",),
    ),
    Case(
        name="very_short_deck",
        request="Build a 2-slide deck: what changed this week, and what is next",
        replies=("yes",),
        expect_slides=2,
        covers="the floor; a cover plus one body slide",
        tags=("shape",),
    ),
    Case(
        name="no_slide_count",
        request="Put together something explaining embeddings to a non-technical audience",
        replies=("yes",),
        covers="no count and no deck vocabulary — routing has to fire on intent alone",
        tags=("shape", "routing"),
    ),
    Case(
        name="context_plus_edit",
        request="Make a deck from these notes",
        context=Q3_NOTES,
        replies=("make it 4 slides and lead with the adoption number", "yes"),
        expect_slides=4,
        covers="context and an edit together — the edit must not lose the grounding",
        tags=("context", "edit", "multi"),
    ),

    # ---------------------------------------------------------------- corpus
    # One per document in benchmark/inputs/. These are the data points: real
    # material, pasted the way a user pastes it, one deck each. The slide count
    # is the agent's call, so it is not asserted -- what is asserted is that a
    # real Palette deck came out and the document reached --context.
    Case(
        name="all_hands",
        request="Turn this into a deck",
        context=read("all_hands.md"),
        replies=("yes",),
        covers="an all-hands deck; long, many short sections",
        tags=("corpus", "context"),
    ),
    Case(
        name="all_hands_v2",
        request="Turn this into a deck",
        context=read("all_hands_v2.md"),
        replies=("yes",),
        covers="the same deck rewritten longer — 11KB of pasted text",
        tags=("corpus", "context"),
    ),
    Case(
        name="benchmark_results",
        request="Turn this into a deck",
        context=read("benchmark_jan2026_condensed.md"),
        replies=("yes",),
        covers="benchmark numbers; tables and figures that must survive",
        tags=("corpus", "context"),
    ),
    Case(
        name="credit_exception",
        request="Turn this into a deck",
        context=read("credit_exception_agent.md"),
        replies=("yes",),
        covers="a one-slide brief with an explicit layout instruction",
        tags=("corpus", "context"),
    ),
    Case(
        name="hackathon_kickoff",
        request="Turn this into a deck",
        context=read("cuga_hackathon_kickoff.md"),
        replies=("yes",),
        covers="an event deck: agenda, logistics, calls to action",
        tags=("corpus", "context"),
    ),
    Case(
        name="sleep_science",
        request="Turn this into a deck",
        context=read("example.md"),
        replies=("yes",),
        covers="consumer subject, no IBM vocabulary — nothing to pattern-match on",
        tags=("corpus", "context"),
    ),
    Case(
        name="competitive",
        request="Turn this into a deck",
        context=read("ibm_competitive_strategy.md"),
        replies=("yes",),
        covers="positioning against named competitors; opinionated content",
        tags=("corpus", "context"),
    ),
    Case(
        name="architecture",
        request="Turn this into a deck",
        context=read("ibm_platform_architecture.md"),
        replies=("yes",),
        covers="a reference architecture — diagram-shaped, hard to render",
        tags=("corpus", "context"),
    ),
    Case(
        name="q3_review",
        request="Turn this into a deck",
        context=read("ibm_q3_review.md"),
        replies=("yes",),
        covers="a business review: revenue, targets, RAG status",
        tags=("corpus", "context"),
    ),
    Case(
        name="meta_deck",
        request="Turn this into a deck",
        context=read("meta_deck.md"),
        replies=("yes",),
        covers="Palette describing itself",
        tags=("corpus", "context"),
    ),
    Case(
        name="meta_deck_orig",
        request="Turn this into a deck",
        context=read("meta_deck_orig.md"),
        replies=("yes",),
        covers="an earlier cut of the same deck — a near-duplicate input",
        tags=("corpus", "context"),
    ),
    Case(
        name="palette_overview",
        request="Turn this into a deck",
        context=read("palette.md"),
        replies=("yes",),
        covers="the full Palette overview, 9 declared slides",
        tags=("corpus", "context"),
    ),
    Case(
        name="palette_update",
        request="Turn this into a deck",
        context=read("palette_demo.md"),
        replies=("yes",),
        covers="a short status readout, 5 declared slides",
        tags=("corpus", "context"),
    ),
)


def by_tag(*tags: str) -> tuple[Case, ...]:
    """Cases carrying any of *tags*; all of them when none is given."""
    if not tags:
        return CASES
    wanted = set(tags)
    return tuple(c for c in CASES if wanted & set(c.tags))
