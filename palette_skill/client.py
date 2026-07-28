"""HTTP client for the Palette deck-building service.

Every request is built from :mod:`palette_skill.contract`, so the client and
the documentation cannot describe different APIs.

Two ways to run a build:

``build_and_wait(plan)``
    One call, blocks until the deck is rendered. Fine from a shell or a
    notebook; too slow for hosts that cap a single code block's wall clock.

``start_build(plan)`` then ``wait(thread_id, max_seconds=25)`` in a loop
    Returns immediately, then each bounded wait reports progress and gives
    control back. This is what agent sandboxes want — CUGA, for instance,
    kills a code block at ``sandbox_execution_timeout`` (30s by default) while
    a deck takes two to four minutes.

``start_build`` needs the server's ``/build_async`` route. Deployments that
predate it are detected via ``/openapi.json`` and fall back to the blocking
path with a clear message rather than a 404.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

import httpx

from palette_skill import contract
from palette_skill.contract import Endpoint


class PaletteError(RuntimeError):
    """Base class for every failure this client raises."""


class PaletteUnavailable(PaletteError):
    """The server could not be reached at all (DNS, refused, TLS)."""


class PaletteTimeout(PaletteError):
    """The server accepted the request but did not answer in time."""


class PaletteHTTPError(PaletteError):
    """The server answered with a non-2xx status."""

    def __init__(self, status_code: int, message: str, url: str) -> None:
        super().__init__(f"HTTP {status_code} from {url}: {message}")
        self.status_code = status_code
        self.message = message
        self.url = url


@dataclass(frozen=True)
class Progress:
    """A snapshot of ``/progress``."""

    stage: str
    message: str = ""
    current: int = 0
    total: int = 0

    @property
    def terminal(self) -> bool:
        return self.stage in contract.TERMINAL_STAGES

    @property
    def failed(self) -> bool:
        return self.stage in {"error", "aborted"}

    def __str__(self) -> str:
        counter = f" [{self.current}/{self.total}]" if self.total else ""
        detail = f" — {self.message}" if self.message else ""
        return f"{self.stage}{counter}{detail}"


@dataclass(frozen=True)
class BuildOutcome:
    """The result of a finished build.

    ``raw`` is the server payload verbatim, so a new field on the server side
    reaches callers without a client release.
    """

    thread_id: str
    slide_count: int = 0
    title: str = ""
    elapsed: float = 0.0
    lint: Any = None
    unrepaired: Sequence[Any] = field(default_factory=tuple)
    geometry: Mapping[str, Any] = field(default_factory=dict)
    retries: Mapping[str, Any] = field(default_factory=dict)
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, thread_id: str, payload: Mapping[str, Any]) -> "BuildOutcome":
        return cls(
            thread_id=thread_id,
            slide_count=int(payload.get("slide_count") or 0),
            title=str(payload.get("title") or ""),
            elapsed=float(payload.get("elapsed") or 0.0),
            lint=payload.get("lint"),
            unrepaired=tuple(payload.get("unrepaired") or ()),
            geometry=dict(payload.get("geometry") or {}),
            retries=dict(payload.get("retries") or {}),
            raw=dict(payload),
        )

    def __str__(self) -> str:
        title = self.title or "(untitled)"
        return f"{title} — {self.slide_count} slides in {self.elapsed:.0f}s"


def new_thread_id() -> str:
    """A fresh session id. Palette namespaces its workspace by this value."""
    return f"skill-{uuid.uuid4().hex[:12]}"


class PaletteClient:
    """Talks to one Palette deployment.

    Args:
        base_url: Server root. Defaults to ``$PALETTE_URL``, then
            :data:`contract.DEFAULT_BASE_URL`.
        token: Bearer token, if the deployment sits behind auth. Defaults to
            ``$PALETTE_TOKEN``. Code Engine apps are public by default, so
            this is normally unset.
        timeout: Per-request ceiling for short calls. Long calls (draft,
            build, edit, retry) use their own budget from the contract.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        token: str | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        resolved = base_url or os.environ.get(contract.BASE_URL_ENV) or contract.DEFAULT_BASE_URL
        self.base_url = resolved.rstrip("/")
        self.token = token if token is not None else os.environ.get(contract.TOKEN_ENV)
        self.timeout = timeout
        # No connection is held between calls. Deck builds are minutes apart,
        # so pooling buys nothing — and a client that owns only strings
        # survives hosts that serialise variables between steps.
        self._transport = transport
        self._capabilities: frozenset[str] | None = None

    # -- plumbing ----------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _send(self, method: str, url: str, *, timeout: float, **kwargs: Any) -> httpx.Response:
        with httpx.Client(transport=self._transport, timeout=timeout) as http:
            return http.request(method, url, headers=self._headers(), **kwargs)

    def _url(self, endpoint: Endpoint, **params: Any) -> str:
        path = endpoint.path
        for key, value in params.items():
            path = path.replace("{" + key + "}", str(value))
        if "{" in path:
            missing = path[path.index("{") + 1 : path.index("}")]
            raise PaletteError(f"{endpoint.name}: missing path parameter {missing!r}")
        return f"{self.base_url}{path}"

    def _request(
        self,
        endpoint: Endpoint,
        *,
        path_params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        data: Mapping[str, Any] | None = None,
        files: Sequence[tuple[str, tuple[str, bytes]]] | None = None,
        timeout: float | None = None,
    ) -> httpx.Response:
        url = self._url(endpoint, **(path_params or {}))
        budget = timeout if timeout is not None else max(self.timeout, endpoint.budget_seconds)
        try:
            response = self._send(
                endpoint.method,
                url,
                json=dict(json) if json is not None else None,
                data=dict(data) if data is not None else None,
                files=list(files) if files else None,
                timeout=budget,
            )
        except httpx.TimeoutException as exc:
            raise PaletteTimeout(
                f"{endpoint.name} did not answer within {budget:.0f}s ({url}). "
                "For builds, prefer start_build() + wait() over a blocking call."
            ) from exc
        except httpx.TransportError as exc:
            raise PaletteUnavailable(
                f"Cannot reach Palette at {self.base_url} ({type(exc).__name__}: {exc}). "
                f"Check the server is running and ${contract.BASE_URL_ENV} points at it."
            ) from exc

        if response.status_code >= 400:
            raise PaletteHTTPError(response.status_code, _error_text(response), url)
        return response

    def _json(self, endpoint: Endpoint, **kwargs: Any) -> dict[str, Any]:
        payload = self._request(endpoint, **kwargs).json()
        if not isinstance(payload, dict):
            raise PaletteError(f"{endpoint.name}: expected a JSON object, got {type(payload).__name__}")
        return payload

    # -- capability detection ---------------------------------------------

    def capabilities(self, *, refresh: bool = False) -> frozenset[str]:
        """Route templates the live server advertises, read from /openapi.json.

        Cached for the client's lifetime. A server that does not serve an
        OpenAPI document is treated as baseline-only, which is the safe
        assumption: every baseline route predates the skill.
        """
        if self._capabilities is not None and not refresh:
            return self._capabilities
        try:
            response = self._send("GET", f"{self.base_url}/openapi.json", timeout=self.timeout)
            paths = response.json().get("paths", {}) if response.status_code < 400 else {}
            found = frozenset(str(p) for p in paths)
        except (httpx.HTTPError, ValueError):
            found = frozenset()
        if not found:
            found = frozenset(e.path for e in contract.BASELINE)
        self._capabilities = found
        return found

    def supports(self, endpoint_name: str) -> bool:
        """Whether the live server exposes a given contract endpoint."""
        endpoint = contract.BY_NAME[endpoint_name]
        if endpoint.baseline:
            return True
        return endpoint.path in self.capabilities()

    # -- read-only ---------------------------------------------------------

    def health(self) -> dict[str, Any]:
        """Liveness, active model roster, and whether the server has a RITS key."""
        return self._json(contract.HEALTH)

    def examples(self) -> list[dict[str, str]]:
        """Curated starter plans: ``[{"file": ..., "label": ...}, ...]``."""
        return list(self._json(contract.EXAMPLES).get("examples") or [])

    def example(self, name: str) -> str:
        """Markdown of one curated example plan."""
        return str(self._json(contract.EXAMPLE, path_params={"name": name}).get("content") or "")

    def deck(self, thread_id: str) -> dict[str, Any]:
        """Slide count, title, and whether a build is in flight."""
        return self._json(contract.DECK, path_params={"thread_id": thread_id})

    def progress(self, thread_id: str) -> Progress:
        """Current stage. Cheap and safe to poll while a build runs."""
        payload = self._json(contract.PROGRESS, path_params={"thread_id": thread_id})
        return Progress(
            stage=str(payload.get("stage") or "idle"),
            message=str(payload.get("message") or ""),
            current=int(payload.get("current") or 0),
            total=int(payload.get("total") or 0),
        )

    # -- stage 1 -----------------------------------------------------------

    def draft(
        self,
        request: str,
        *,
        thread_id: str | None = None,
        planner: str = "gpt-oss-120b",
        files: Iterable[str | Path] = (),
    ) -> str:
        """Turn a plain-English request into an editable markdown plan.

        ``files`` are reference documents (PDF, DOCX, PPTX, Markdown) that
        shape the plan. Returns the plan markdown.
        """
        data, uploads = self._draft_form(request, thread_id or new_thread_id(), planner, files)
        payload = self._json(contract.DRAFT, data=data, files=uploads or None)
        plan = str(payload.get("plan") or "")
        if not plan.strip():
            raise PaletteError("draft: server returned an empty plan")
        return plan

    def _draft_form(
        self, request: str, thread_id: str, planner: str, files: Iterable[str | Path]
    ) -> tuple[dict[str, Any], list[tuple[str, tuple[str, bytes]]]]:
        if not request.strip():
            raise PaletteError("draft: request is empty")
        uploads: list[tuple[str, tuple[str, bytes]]] = []
        for item in files:
            path = Path(item)
            if not path.is_file():
                raise PaletteError(f"draft: reference file not found: {path}")
            uploads.append(("files", (path.name, path.read_bytes())))
        return {"request": request, "thread_id": thread_id, "planner": planner}, uploads

    def start_draft(
        self,
        request: str,
        *,
        thread_id: str | None = None,
        planner: str = "gpt-oss-120b",
        files: Iterable[str | Path] = (),
    ) -> str:
        """Kick off Stage 1 in the background and return its ``thread_id``.

        Drafting from reference documents runs 60-90s — past the per-step limit
        of most agent sandboxes — so poll :meth:`wait_draft` and then read
        :meth:`draft_result`.
        """
        if not self.supports("draft_async"):
            raise PaletteError(
                f"{self.base_url} has no /draft_async route, so drafting cannot be backgrounded. "
                "Either redeploy Palette with the current app.py, or call draft() and give it "
                "room to block for a couple of minutes."
            )
        tid = thread_id or new_thread_id()
        data, uploads = self._draft_form(request, tid, planner, files)
        self._json(contract.DRAFT_ASYNC, data=data, files=uploads or None)
        return tid

    def draft_result(self, thread_id: str) -> str:
        """Plan markdown from a finished background draft."""
        if not self.supports("draft_result"):
            raise PaletteError(f"{self.base_url} has no /draft_result route.")
        payload = self._json(contract.DRAFT_RESULT, path_params={"thread_id": thread_id})
        stage = str(payload.get("stage") or "")
        if stage == "error":
            raise PaletteError(f"draft failed: {payload.get('error') or 'unknown error'}")
        if stage != "done":
            raise PaletteError(f"draft is not finished yet (stage={stage!r}); keep polling.")
        plan = str(payload.get("plan") or "")
        if not plan.strip():
            raise PaletteError("draft finished but returned an empty plan")
        return plan

    def wait_draft(
        self,
        thread_id: str,
        *,
        max_seconds: float = 25.0,
        poll_interval: float = 2.0,
        on_progress: Callable[[Progress], None] | None = None,
    ) -> Progress:
        """Bounded poll for a background draft, mirroring :meth:`wait`.

        Draft terminal stages differ from a build's — ``draft_done`` means a
        plan is ready, not a rendered deck.
        """
        deadline = time.monotonic() + max_seconds
        snapshot = self.progress(thread_id)
        if on_progress:
            on_progress(snapshot)
        while snapshot.stage not in contract.DRAFT_TERMINAL_STAGES and time.monotonic() < deadline:
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
            latest = self.progress(thread_id)
            if on_progress and latest != snapshot:
                on_progress(latest)
            snapshot = latest
        return snapshot

    def draft_and_wait(
        self,
        request: str,
        *,
        thread_id: str | None = None,
        planner: str = "gpt-oss-120b",
        files: Iterable[str | Path] = (),
        on_progress: Callable[[Progress], None] | None = None,
        max_seconds: float = float(contract.DRAFT.budget_seconds),
    ) -> tuple[str, str]:
        """Draft a plan and block until it is ready. Returns ``(plan, thread_id)``.

        Uses the background route when the deployment has one, so progress is
        reported as it happens; falls back to the blocking POST otherwise.
        """
        tid = thread_id or new_thread_id()
        if self.supports("draft_async"):
            self.start_draft(request, thread_id=tid, planner=planner, files=files)
            deadline = time.monotonic() + max_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PaletteTimeout(f"draft did not finish within {max_seconds:.0f}s (thread_id={tid}).")
                snapshot = self.wait_draft(
                    tid, max_seconds=min(30.0, remaining), on_progress=on_progress
                )
                if snapshot.stage in contract.DRAFT_TERMINAL_STAGES:
                    break
            if snapshot.failed:
                raise PaletteError(f"draft failed: {snapshot.message or snapshot.stage}")
            return self.draft_result(tid), tid
        return self.draft(request, thread_id=tid, planner=planner, files=files), tid

    # -- stages 2 and 3 ----------------------------------------------------

    def _build_payload(
        self,
        plan: str,
        thread_id: str,
        palette_family: str,
        planner: str,
        designer_coder: str,
        critic: str,
    ) -> dict[str, Any]:
        if not plan.strip():
            raise PaletteError("build: plan is empty")
        return {
            "plan": plan,
            "thread_id": thread_id,
            "palette_family": palette_family,
            "planner": planner,
            "designer_coder": designer_coder,
            "critic": critic,
        }

    def start_build(
        self,
        plan: str,
        *,
        thread_id: str | None = None,
        palette_family: str = "ibm_watsonx",
        planner: str = "gpt-oss-120b",
        designer_coder: str = "palette-lora",
        critic: str = "gpt-oss-120b",
    ) -> str:
        """Kick off a build in the background and return its ``thread_id``.

        Poll :meth:`progress` or :meth:`wait`, then read :meth:`result`.
        Raises if the deployment has no ``/build_async`` route — use
        :meth:`build_and_wait` there instead.
        """
        if not self.supports("build_async"):
            raise PaletteError(
                f"{self.base_url} has no /build_async route, so a build cannot be backgrounded. "
                "Either redeploy Palette with the current app.py, or call build_and_wait() "
                "and give it room to block for several minutes."
            )
        tid = thread_id or new_thread_id()
        self._json(
            contract.BUILD_ASYNC,
            json=self._build_payload(plan, tid, palette_family, planner, designer_coder, critic),
        )
        return tid

    def result(self, thread_id: str) -> BuildOutcome:
        """Outcome of a finished background build.

        Raises :class:`PaletteError` if the build is still running or failed.
        """
        if not self.supports("result"):
            raise PaletteError(f"{self.base_url} has no /result route.")
        payload = self._json(contract.RESULT, path_params={"thread_id": thread_id})
        stage = str(payload.get("stage") or "")
        if stage == "error":
            raise PaletteError(f"build failed: {payload.get('error') or 'unknown error'}")
        if stage != "done":
            raise PaletteError(f"build is not finished yet (stage={stage!r}); keep polling progress().")
        return BuildOutcome.from_payload(thread_id, payload.get("result") or {})

    def wait(
        self,
        thread_id: str,
        *,
        max_seconds: float = 25.0,
        poll_interval: float = 2.0,
        on_progress: Callable[[Progress], None] | None = None,
    ) -> Progress:
        """Poll until the build ends **or** ``max_seconds`` elapses.

        Returns the last snapshot either way — check :attr:`Progress.terminal`
        to decide whether to call again. The bounded form is what makes this
        usable inside a host that caps how long one code block may run: call
        it repeatedly with ``max_seconds`` under that cap.
        """
        deadline = time.monotonic() + max_seconds
        snapshot = self.progress(thread_id)
        if on_progress:
            on_progress(snapshot)
        while not snapshot.terminal and time.monotonic() < deadline:
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
            latest = self.progress(thread_id)
            if on_progress and (latest.stage, latest.message, latest.current) != (
                snapshot.stage,
                snapshot.message,
                snapshot.current,
            ):
                on_progress(latest)
            snapshot = latest
        return snapshot

    def build_and_wait(
        self,
        plan: str,
        *,
        thread_id: str | None = None,
        palette_family: str = "ibm_watsonx",
        planner: str = "gpt-oss-120b",
        designer_coder: str = "palette-lora",
        critic: str = "gpt-oss-120b",
        on_progress: Callable[[Progress], None] | None = None,
        max_seconds: float = float(contract.BUILD.budget_seconds),
    ) -> BuildOutcome:
        """Build a deck and block until it is rendered.

        Uses the background route when the deployment has one (so progress can
        be reported as it happens) and a single blocking POST otherwise.
        """
        tid = thread_id or new_thread_id()
        payload = self._build_payload(plan, tid, palette_family, planner, designer_coder, critic)

        if self.supports("build_async"):
            self._json(contract.BUILD_ASYNC, json=payload)
            deadline = time.monotonic() + max_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PaletteTimeout(
                        f"build did not finish within {max_seconds:.0f}s (thread_id={tid}). "
                        "It may still be running server-side — poll progress()."
                    )
                snapshot = self.wait(
                    tid,
                    max_seconds=min(30.0, remaining),
                    on_progress=on_progress,
                )
                if snapshot.terminal:
                    break
            if snapshot.failed:
                raise PaletteError(f"build failed: {snapshot.message or snapshot.stage}")
            return self.result(tid)

        outcome = self._json(contract.BUILD, json=payload, timeout=max_seconds)
        return BuildOutcome.from_payload(tid, outcome)

    def edit(self, thread_id: str, slide_n: int, instruction: str) -> dict[str, Any]:
        """Apply a natural-language instruction to one slide and re-render."""
        if not instruction.strip():
            raise PaletteError("edit: instruction is empty")
        return self._json(
            contract.EDIT,
            json={"thread_id": thread_id, "slide_n": slide_n, "instruction": instruction},
        )

    def retry(self, thread_id: str, slide_n: int) -> dict[str, Any]:
        """Re-roll one slide, then re-run the geometry pass on it."""
        return self._json(
            contract.RETRY,
            path_params={"thread_id": thread_id, "slide_n": slide_n},
        )

    # -- artefacts ---------------------------------------------------------

    def download(self, thread_id: str, dest: str | Path = "deck.pptx") -> Path:
        """Save the finished ``.pptx`` and return its path."""
        target = Path(dest)
        if target.is_dir():
            target = target / "deck.pptx"
        response = self._request(contract.DOWNLOAD, path_params={"thread_id": thread_id})
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
        return target

    def preview(self, thread_id: str, idx: int, dest: str | Path | None = None) -> Path:
        """Save one slide's PNG preview (1-indexed) and return its path."""
        target = Path(dest) if dest is not None else Path(f"slide-{idx:02d}.png")
        if target.is_dir():
            target = target / f"slide-{idx:02d}.png"
        response = self._request(contract.PREVIEW, path_params={"thread_id": thread_id, "idx": idx})
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
        return target

    def previews(self, thread_id: str, dest_dir: str | Path = ".") -> list[Path]:
        """Save every rendered slide preview into ``dest_dir``."""
        count = int(self.deck(thread_id).get("slide_count") or 0)
        directory = Path(dest_dir)
        directory.mkdir(parents=True, exist_ok=True)
        return [self.preview(thread_id, i, directory / f"slide-{i:02d}.png") for i in range(1, count + 1)]

    # -- session lifecycle -------------------------------------------------

    def clear(self, thread_id: str) -> bool:
        """Drop a session and delete its server-side workspace."""
        return bool(self._json(contract.CLEAR, path_params={"thread_id": thread_id}).get("cleared"))

    def abort(self, thread_id: str) -> bool:
        """Mark a session not-building. Server-side work keeps running."""
        return bool(self._json(contract.ABORT, path_params={"thread_id": thread_id}).get("aborted"))


def _error_text(response: httpx.Response) -> str:
    """Palette reports failures as ``{"error": "..."}``; fall back to the body."""
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()[:500] or response.reason_phrase
    if isinstance(payload, dict):
        for key in ("error", "detail", "message"):
            if payload.get(key):
                return str(payload[key])
    return str(payload)[:500]


# --------------------------------------------------------------------------
# One resumable operation, owned by the tool rather than the agent
# --------------------------------------------------------------------------

DECK_STATE = ".palette-deck.json"


def _state_path(dest: Path) -> Path:
    return Path(dest) / DECK_STATE


def _load_state(dest: Path) -> dict[str, Any]:
    path = _state_path(dest)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save_state(dest: Path, state: Mapping[str, Any]) -> None:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    _state_path(dest).write_text(json.dumps(dict(state), indent=2), encoding="utf-8")


def _verify(dest: Path) -> dict[str, Any]:
    """Stat what is actually on disk. This is the only thing allowed to say 'done'.

    Every prose instruction telling an agent to check its own work has been
    ignored at least once. So the claim of completion is computed here, from
    the filesystem, and the agent's job is only to relay it.
    """
    dest = Path(dest)
    pptx = dest / "deck.pptx"
    slides = sorted(dest.glob("slide-*.png"))
    pptx_bytes = pptx.stat().st_size if pptx.is_file() else 0
    return {
        "pptx": str(pptx) if pptx.is_file() else None,
        "pptx_bytes": pptx_bytes,
        "slides": [str(p) for p in slides],
        "slide_count": len(slides),
        # A deck that "succeeded" at 12 KB or with no previews did not succeed.
        "verified": bool(pptx.is_file() and pptx_bytes > 20_000 and slides),
    }


#: A plan this short is not a plan. The crafter can return an empty document
#: on a terminal-but-successful draft, and building from it wastes four
#: minutes to produce nothing — so it is caught here, where it is still cheap.
MIN_PLAN_CHARS = 200


def run_deck(
    pal: "PaletteClient",
    *,
    dest: str | Path,
    request: str | None = None,
    plan_file: str | Path | None = None,
    max_seconds: float = 25.0,
    pause_after_plan: bool = False,
    approve: bool = False,
) -> dict[str, Any]:
    """Advance one deck as far as possible, then return where it got to.

    Idempotent and resumable: call it repeatedly with the same ``dest`` until
    ``done`` is true. It owns the thread id, the plan file, the polling and the
    download, so an agent cannot lose the session by retrying — the previous
    failure mode, where each retry silently started a fresh draft and orphaned
    the last one.

    ``pause_after_plan`` stops at ``plan-ready`` so the plan can be shown and
    approved before the expensive stage runs. This exists because the flow it
    covers — *draft it, show me, then build it* — is how people actually ask,
    and the only alternative was driving the three underlying calls by hand.
    That is precisely the arrangement this function replaced, so leaving it as
    the documented answer for the commonest phrasing put the failure straight
    back. Approval changes who decides, never who tracks the session.

    ``done`` is never asserted from the model's belief. It is computed by
    stat-ing the files.
    """
    dest = Path(dest)
    state = _load_state(dest)
    stage = state.get("stage", "new")

    # Already finished? Re-verify rather than trusting the stored flag.
    if stage == "done":
        return {"stage": "done", "done": True, "thread_id": state.get("thread_id"), **_verify(dest)}

    if stage == "new":
        if plan_file:
            plan = Path(plan_file).read_text(encoding="utf-8")
            thread_id = pal.start_build(plan)
            state = {"stage": "building", "thread_id": thread_id, "source": str(plan_file)}
        else:
            if not request:
                raise PaletteError("run_deck: pass --request or --plan-file")
            thread_id = pal.start_draft(request)
            state = {
                "stage": "drafting",
                "thread_id": thread_id,
                "request": request,
                "pause_after_plan": pause_after_plan,
            }
        _save_state(dest, state)
        return {**state, "done": False, "note": "started; call again to advance"}

    thread_id = state["thread_id"]

    if stage == "drafting":
        snapshot = pal.wait_draft(thread_id, max_seconds=max_seconds)
        if snapshot.stage not in contract.DRAFT_TERMINAL_STAGES:
            return {**state, "done": False, "progress": str(snapshot)}
        if snapshot.failed:
            state["stage"] = "failed"
            _save_state(dest, state)
            raise PaletteError(f"draft failed: {snapshot.message}")

        plan = pal.draft_result(thread_id)
        if len(plan.strip()) < MIN_PLAN_CHARS:
            # Terminal, not failed, and empty. Building this produces nothing.
            state["stage"] = "failed"
            _save_state(dest, state)
            raise PaletteError(
                f"draft returned {len(plan.strip())} characters, which is not a usable plan. "
                f"Delete {dest / DECK_STATE} and try again with a more specific request."
            )
        plan_path = dest / "plan.md"
        dest.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(plan, encoding="utf-8")
        state["plan"] = str(plan_path)

        if state.get("pause_after_plan") and not approve:
            state["stage"] = "plan-ready"
            _save_state(dest, state)
            return {
                **state,
                "done": False,
                "plan_markdown": plan,
                "note": "show this plan to the user; call again with --approve to build it",
            }

        # Same session carries the plan and the deck.
        pal.start_build(plan, thread_id=thread_id)
        state["stage"] = "building"
        _save_state(dest, state)
        return {**state, "done": False, "note": "plan ready, build started"}

    if stage == "plan-ready":
        plan = Path(state["plan"]).read_text(encoding="utf-8")
        if not approve:
            return {
                **state,
                "done": False,
                "plan_markdown": plan,
                "note": "waiting for the user; call again with --approve to build it",
            }
        # Edits the user asked for are made to plan.md, so re-read it above
        # rather than replaying whatever the draft originally returned.
        pal.start_build(plan, thread_id=thread_id)
        state["stage"] = "building"
        _save_state(dest, state)
        return {**state, "done": False, "note": "approved, build started"}

    if stage == "building":
        snapshot = pal.wait(thread_id, max_seconds=max_seconds)
        if not snapshot.terminal:
            return {**state, "done": False, "progress": str(snapshot)}
        if snapshot.failed:
            state["stage"] = "failed"
            _save_state(dest, state)
            raise PaletteError(f"build failed: {snapshot.message}")

        outcome = pal.result(thread_id)
        pal.download(thread_id, dest / "deck.pptx")
        pal.previews(thread_id, dest)
        checked = _verify(dest)
        if not checked["verified"]:
            raise PaletteError(
                f"build reported done but the files are not on disk: {checked}. "
                "Do not report success."
            )
        state.update(stage="done", title=outcome.title, slide_count=outcome.slide_count)
        _save_state(dest, state)
        return {**state, "done": True, "unrepaired": list(outcome.unrepaired), **checked}

    raise PaletteError(f"deck is in state {stage!r}; delete {dest / DECK_STATE} to start over")
