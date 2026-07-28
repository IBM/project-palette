"""The Palette HTTP contract — the one place it is written down.

Three consumers read this module, which is what keeps the skill from drifting
away from the server:

  1. ``palette_skill.client`` — builds every request from these entries.
  2. ``palette_skill.build_skill`` — renders the endpoint table into SKILL.md.
  3. ``tests/test_skill_contract.py`` — walks the live ``app.app`` routes and
     fails if any entry here no longer matches reality.

So a change to ``app.py`` that this file does not know about breaks the test
suite, not a user's deck build. Add or amend the entry, run ``make skill``,
and the SKILL.md, the client, and the docs move together.

Nothing in here imports the server. The client must stay installable with
httpx alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Method = Literal["GET", "POST"]


@dataclass(frozen=True)
class Endpoint:
    """One route on the Palette server.

    ``path`` uses ``{brace}`` placeholders matching the server's own route
    template, so the contract test can compare strings directly against
    ``starlette.routing.Route.path`` with no normalisation.
    """

    name: str
    method: Method
    path: str
    summary: str
    #: JSON body / form fields the client sends. ``required`` drives both the
    #: client's validation and the contract test's model-field assertions.
    fields: tuple[str, ...] = ()
    required_fields: tuple[str, ...] = ()
    #: Keys the client reads off a successful response. The contract test does
    #: not verify these (that needs a live build), but SKILL.md documents them
    #: and the client fails loudly on a KeyError rather than silently.
    returns: tuple[str, ...] = ()
    #: Content type of a successful response.
    response: Literal["json", "file", "html"] = "json"
    #: False for routes added after the initial Code Engine deployment. The
    #: client feature-detects these against /openapi.json instead of assuming
    #: they exist — see PaletteClient.capabilities().
    baseline: bool = True
    #: Rough upper bound on server-side work, in seconds. Drives client
    #: timeouts and the guidance in SKILL.md about CUGA's 30s block limit.
    budget_seconds: int = 30


#: Set by app.py's own default. Every deployment overrides the host/port.
DEFAULT_PORT = 18814

#: Where the skill points when PALETTE_URL is unset. The IBM Code Engine
#: deployment is the intended default (see deployment/DEPLOYMENT.md); until
#: that URL is pinned here, local dev is the fallback so nothing silently
#: talks to the wrong host.
DEFAULT_BASE_URL = f"http://127.0.0.1:{DEFAULT_PORT}"

#: Env var that overrides DEFAULT_BASE_URL, read by PaletteClient.
BASE_URL_ENV = "PALETTE_URL"

#: Env var holding a bearer token, if the deployment sits behind auth.
#: Code Engine apps are public by default, so this is normally unset.
TOKEN_ENV = "PALETTE_TOKEN"


HEALTH = Endpoint(
    name="health",
    method="GET",
    path="/health",
    summary="Liveness plus the active model roster and whether RITS_API_KEY is set.",
    returns=("status", "roster", "icons", "rits_key_set"),
)

EXAMPLES = Endpoint(
    name="examples",
    method="GET",
    path="/examples",
    summary="Curated starter plans the user can build from without drafting one.",
    returns=("examples",),
)

EXAMPLE = Endpoint(
    name="example",
    method="GET",
    path="/example/{name}",
    summary="Fetch one curated example plan as markdown.",
    returns=("name", "content"),
)

DRAFT = Endpoint(
    name="draft",
    method="POST",
    path="/draft",
    summary="Stage 1 — turn a plain-English request (plus optional reference docs) into an editable markdown plan.",
    fields=("request", "thread_id", "planner", "files"),
    required_fields=("request",),
    returns=("plan", "sources"),
    budget_seconds=180,
)

DRAFT_ASYNC = Endpoint(
    name="draft_async",
    method="POST",
    path="/draft_async",
    summary="Same drafting, started in the background. Returns at once; poll /progress then read /draft_result.",
    fields=("request", "thread_id", "planner", "files"),
    required_fields=("request",),
    returns=("started", "thread_id", "sources"),
    baseline=False,
    budget_seconds=10,
)

DRAFT_RESULT = Endpoint(
    name="draft_result",
    method="GET",
    path="/draft_result/{thread_id}",
    summary="Terminal outcome of a background draft: the plan markdown, or the error that ended it.",
    returns=("stage", "plan", "error"),
    baseline=False,
)

BUILD = Endpoint(
    name="build",
    method="POST",
    path="/build",
    summary="Stages 2+3 — plan markdown to a rendered .pptx. Blocks for the whole build.",
    fields=("plan", "thread_id", "palette_family", "planner", "designer_coder", "critic"),
    required_fields=("plan",),
    returns=("slide_count", "title", "elapsed", "lint", "unrepaired", "geometry", "retries"),
    budget_seconds=600,
)

BUILD_ASYNC = Endpoint(
    name="build_async",
    method="POST",
    path="/build_async",
    summary="Same build, started in the background. Returns immediately; poll /progress then read /result.",
    fields=("plan", "thread_id", "palette_family", "planner", "designer_coder", "critic"),
    required_fields=("plan",),
    returns=("started", "thread_id"),
    baseline=False,
    budget_seconds=5,
)

RESULT = Endpoint(
    name="result",
    method="GET",
    path="/result/{thread_id}",
    summary="Terminal outcome of a background build: the same payload /build returns, or the error that ended it.",
    returns=("stage", "result", "error"),
    baseline=False,
)

PROGRESS = Endpoint(
    name="progress",
    method="GET",
    path="/progress/{thread_id}",
    summary="Current build stage for a session. Safe to poll while a build runs.",
    returns=("stage", "message", "current", "total"),
)

DECK = Endpoint(
    name="deck",
    method="GET",
    path="/deck/{thread_id}",
    summary="Slide count, deck title, and whether a build is in flight.",
    returns=("slide_count", "title", "building"),
)

EDIT = Endpoint(
    name="edit",
    method="POST",
    path="/edit",
    summary="Stage 3 — apply a natural-language instruction to one slide and re-render.",
    fields=("thread_id", "slide_n", "instruction"),
    required_fields=("thread_id", "slide_n", "instruction"),
    returns=("slide_count", "edited"),
    budget_seconds=180,
)

RETRY = Endpoint(
    name="retry",
    method="POST",
    path="/retry/{thread_id}/{slide_n}",
    summary="Re-roll one slide at a small temperature bump, then re-run the geometry pass on it.",
    returns=("slide_count", "retried", "geometry"),
    budget_seconds=180,
)

PREVIEW = Endpoint(
    name="preview",
    method="GET",
    path="/preview/{thread_id}/{idx}",
    summary="PNG render of one slide (1-indexed).",
    response="file",
)

DOWNLOAD = Endpoint(
    name="download",
    method="GET",
    path="/download/{thread_id}",
    summary="The finished .pptx.",
    response="file",
)

CLEAR = Endpoint(
    name="clear",
    method="POST",
    path="/clear/{thread_id}",
    summary="Drop a session and delete its workspace.",
    returns=("cleared",),
)

ABORT = Endpoint(
    name="abort",
    method="POST",
    path="/abort/{thread_id}",
    summary="Mark a session not-building so the caller can move on. Does not stop server-side work.",
    returns=("aborted",),
)


ENDPOINTS: tuple[Endpoint, ...] = (
    HEALTH,
    EXAMPLES,
    EXAMPLE,
    DRAFT,
    DRAFT_ASYNC,
    DRAFT_RESULT,
    BUILD,
    BUILD_ASYNC,
    RESULT,
    PROGRESS,
    DECK,
    EDIT,
    RETRY,
    PREVIEW,
    DOWNLOAD,
    CLEAR,
    ABORT,
)

BY_NAME: dict[str, Endpoint] = {e.name: e for e in ENDPOINTS}

#: Routes that predate the skill. The contract test requires every one of these
#: to exist on the live app; anything else is feature-detected at runtime.
BASELINE: tuple[Endpoint, ...] = tuple(e for e in ENDPOINTS if e.baseline)

#: Terminal values of the ``stage`` field returned by /progress. The client
#: stops polling on these; SKILL.md tells the agent the same thing.
TERMINAL_STAGES: frozenset[str] = frozenset({"done", "error", "aborted"})

#: Terminal values of ``stage`` on /draft_result. Distinct from a build's:
#: ``draft_done`` means a plan is ready, not a rendered deck.
DRAFT_TERMINAL_STAGES: frozenset[str] = frozenset({"draft_done", "error", "aborted"})


@dataclass(frozen=True)
class ModelRole:
    """One editable slot in Palette's per-role model roster (config.ROSTER)."""

    role: str
    field: str
    summary: str
    #: Populated at generation time from config.py so SKILL.md never invents
    #: a model name the server would reject.
    choices: tuple[str, ...] = field(default_factory=tuple)


#: The three roles the HTTP API exposes. `field` is the request field name;
#: `role` is the ROSTER key config.apply_models() writes.
#:
#: Note the asymmetry, which is a genuine trap for anyone reading app.py
#: quickly: BuildReq's `critic` field is passed to apply_models() as the
#: `correction` argument, so it selects the *editor* (geometry-repair) model,
#: not the Qwen-VL visual critic. Documented here so the skill says the true
#: thing. Verified against app.py's `config.apply_models(req.planner,
#: req.designer_coder, req.critic)` and config.apply_models's signature
#: `(planner, designer_coder, correction)`.
MODEL_ROLES: tuple[ModelRole, ...] = (
    ModelRole(
        role="crafter",
        field="planner",
        summary="Stage 1 planner that drafts plan.md from the request.",
    ),
    ModelRole(
        role="designer+coder",
        field="designer_coder",
        summary="Stage 2 fine-tuned adapter that turns the plan into per-slide pptxgenjs code.",
    ),
    ModelRole(
        role="editor",
        field="critic",
        summary=(
            "Stage 3 geometry-repair model. Named `critic` in the request body for "
            "backward compatibility, but it routes to the editor role."
        ),
    ),
)
