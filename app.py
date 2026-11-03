"""Palette — FastAPI app.

POST /draft turns a request into an editable plan; POST /build runs that plan
through designer -> coder -> render -> repair; /edit applies per-slide edits.
Builds run in a worker thread; the UI polls /progress. A settings bar picks
the model for each stage (see config.apply_models).

Run:  RITS_API_KEY=... python app.py [--port 18814]
"""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import csv
import hmac
import io
import logging
from contextlib import asynccontextmanager
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

import config
import llm
import usage
from intake import craft_plan
from intake import edit_plan as intake_edit_plan
from pipeline import generate_deck, retry_slide
from refine import _rerender, apply_nl_edit
from session import SlideSession
from stats_ui import STATS_HTML
from ui import HTML

# Per-session logging
# -------------------
# Each session gets its own `workspace/<thread_id>/session.log`. There is no
# global log file. Routing works through a contextvar set at every endpoint
# entry; each session's FileHandler keeps only records emitted while that
# contextvar equals its own target. Stage-3 fan-outs (ThreadPoolExecutor
# inside pipeline/refine/render) copy this context explicitly so worker
# threads route to the right log -- see contextvars.copy_context().run.

_session_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "palette_session_id", default=None)


def _bind_session(thread_id: str) -> None:
    """Route this request's logs to its session, and tag its usage record."""
    _session_id_var.set(thread_id)
    usage.note_thread(thread_id)


class _SessionLogFilter(logging.Filter):
    """Keep records emitted while _session_id_var equals this target."""
    def __init__(self, target_session_id: str) -> None:
        super().__init__()
        self.target = target_session_id

    def filter(self, record: logging.LogRecord) -> bool:
        return _session_id_var.get() == self.target


_LOG_FORMAT = logging.Formatter(
    "%(asctime)s %(levelname)-5s [%(name)-8s] %(message)s",
    datefmt="%H:%M:%S")

# Stream handler only at startup -- server boot / shutdown / static-asset
# requests just go to stderr. Per-session FileHandlers are installed lazily
# when a session is first created (see _session()).
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s [%(name)-8s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler()],
)
# `llm.responses` is for full request/response bodies -- verbose, NEVER goes
# to stderr. Per-session handlers added by _session() attach to this logger
# too so bodies land in the session log file.
_body_log = logging.getLogger("llm.responses")
_body_log.propagate = False
_body_log.setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("app")

_registry: dict[str, SlideSession] = {}
# Usage-tracking links, per thread: the latest plan's id (a build points at
# the plan it came from) and the latest build's id (edits/retries point at it,
# and refresh its archived deck). See usage.py.
_plan_ids: dict[str, int | None] = {}
_build_ids: dict[str, int | None] = {}
_registry_lock = Lock()


def _install_session_log(s: SlideSession) -> None:
    """Attach a FileHandler writing to <session.root>/session.log, scoped to
    records emitted under this session's contextvar."""
    log_path = s.root / "session.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    handler.setFormatter(_LOG_FORMAT)
    handler.addFilter(_SessionLogFilter(s.session_id))
    logging.getLogger().addHandler(handler)
    _body_log.addHandler(handler)
    s.log_handler = handler


def _remove_session_log(s: SlideSession) -> None:
    """Detach and close a session's FileHandler. Idempotent."""
    handler = s.log_handler
    if handler is None:
        return
    logging.getLogger().removeHandler(handler)
    _body_log.removeHandler(handler)
    try:
        handler.close()
    except Exception:  # noqa: BLE001
        pass
    s.log_handler = None


def _session(thread_id: str) -> SlideSession:
    with _registry_lock:
        s = _registry.get(thread_id)
        if s is None:
            s = SlideSession.create(config.WORKSPACE, thread_id)
            _install_session_log(s)
            _registry[thread_id] = s
            log.info("new session: %s -> %s", thread_id,
                     s.root / "session.log")
        return s


class BuildReq(BaseModel):
    plan: str
    thread_id: str = "default"
    palette_family: str = "ibm_watsonx"
    planner: str = "gpt-oss-120b"
    designer_coder: str = "palette-lora"
    critic: str = "gpt-oss-120b"


# --- keep-warm ----------------------------------------------------------------
# RITS scales an idle deployment down and the next request waits ~5 min for a
# cold start. While the server runs, ping the designer/coder model with a
# 1-token request every KEEPWARM_MINUTES — any request resets RITS's idle
# timer, so there is no need to generate a real slide. Pinging the adapter's
# payload_model (not the base) also keeps the LoRA weights loaded.
_keepwarm: dict = {"enabled": config.KEEPWARM_MINUTES > 0,
                   "interval_min": config.KEEPWARM_MINUTES,
                   "last_ping_at": None, "last_ping_s": None, "last_error": None}
# A healthy ping answers in well under a second; anything slower means it
# caught a cold start (or RITS is struggling).
_KEEPWARM_SLOW_S = 10.0


def _keepwarm_ping() -> None:
    spec = config.ROSTER["designer"]
    if not config.spec_usable(spec):
        return
    t0 = time.monotonic()
    try:
        llm.chat(spec, [{"role": "user", "content": "hi"}],
                 max_tokens=1, temperature=0.0, timeout=120)
    except Exception as exc:  # noqa: BLE001 — logged, retried next interval
        _keepwarm["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
        log.warning("keep-warm: %s did not answer (%s) — cold start or outage",
                    spec.payload_model, _keepwarm["last_error"])
        return
    took = time.monotonic() - t0
    _keepwarm.update(last_ping_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
                     last_ping_s=round(took, 2), last_error=None)
    if took > _KEEPWARM_SLOW_S:
        log.warning("keep-warm: %s answered in %.0fs — it was cold",
                    spec.payload_model, took)
    else:
        log.info("keep-warm: %s ok in %.1fs", spec.payload_model, took)


async def _keepwarm_loop() -> None:
    interval = config.KEEPWARM_MINUTES * 60
    while True:
        model = config.ROSTER["designer"].payload_model
        idle = time.monotonic() - llm.last_success.get(model, float("-inf"))
        if idle >= interval:
            await asyncio.to_thread(_keepwarm_ping)
            await asyncio.sleep(interval)
        else:
            # Real traffic answered recently; check again when it would go stale.
            await asyncio.sleep(interval - idle)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Every asyncio.to_thread call (builds, drafts, edits, usage writes) runs
    # on the loop's default executor; see config.WORKER_THREADS for why the
    # stock size is too small.
    asyncio.get_running_loop().set_default_executor(
        ThreadPoolExecutor(max_workers=config.WORKER_THREADS,
                           thread_name_prefix="palette"))
    usage.init()
    task = None
    if config.KEEPWARM_MINUTES > 0:
        log.info("keep-warm: pinging %s every %g min",
                 config.ROSTER["designer"].payload_model, config.KEEPWARM_MINUTES)
        task = asyncio.create_task(_keepwarm_loop())
    yield
    if task:
        task.cancel()


app = FastAPI(title="Palette", docs_url=None, redoc_url=None, lifespan=_lifespan)


# --- usage tracking -----------------------------------------------------------
# Every POST (the actions: draft, build, edit, retry, …) and every download is
# written to the local SQLite db (usage.py). Polling GETs are not — they would
# drown the signal. The endpoints add the utterance text and build outcomes.

def _tracked(request: Request) -> bool:
    path = request.url.path
    if path.startswith("/stats"):
        return False
    return request.method == "POST" or path.startswith("/download/")


@app.middleware("http")
async def _usage_mw(request: Request, call_next):
    if not _tracked(request):
        return await call_next(request)
    who = usage.identify(request)
    t0 = time.time()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        usage.record_request(who, request.method, request.url.path, status,
                             int((time.time() - t0) * 1000))


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    response = HTMLResponse(HTML)
    # An anonymous per-browser id, so the stats page can tell users apart.
    if not request.cookies.get(usage.COOKIE):
        response.set_cookie(usage.COOKIE, usage.new_uid(), max_age=usage.COOKIE_MAX_AGE,
                            httponly=True, samesite="lax")
    return response


# --- /stats: the usage dashboard ------------------------------------------------
# Secret: answers only with ?token=<PALETTE_STATS_TOKEN>. Without the token, or
# with none configured, it is a plain 404, indistinguishable from no route.
# Hidden from /openapi.json for the same reason.

def _stats_ok(request: Request) -> bool:
    want = os.environ.get("PALETTE_STATS_TOKEN", "")
    got = request.query_params.get("token", "")
    return bool(want) and hmac.compare_digest(got.encode(), want.encode())


_NOT_FOUND = JSONResponse({"detail": "Not Found"}, status_code=404)
# The token rides in the URL: never send it on as a Referer.
_STATS_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def _csv_safe(value):
    """Defuse spreadsheet formulas in user-typed text (CSV injection): a cell
    starting with = + - @ would run as a formula when opened in Excel."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


@app.get("/stats", include_in_schema=False)
async def stats_page(request: Request):
    if not _stats_ok(request):
        return _NOT_FOUND
    return HTMLResponse(STATS_HTML, headers=_STATS_HEADERS)


@app.get("/stats/api", include_in_schema=False)
async def stats_api(request: Request, days: int = 30):
    if not _stats_ok(request):
        return _NOT_FOUND
    data = await asyncio.to_thread(usage.summary, max(1, min(days, 365)))
    return JSONResponse(data, headers=_STATS_HEADERS)


@app.get("/stats/deck/{build_id}/{name}", include_in_schema=False)
async def stats_deck(request: Request, build_id: int, name: str):
    """An archived deck's files: deck.pptx, plan.md, slide-NN.png."""
    if not _stats_ok(request):
        return _NOT_FOUND
    path = await asyncio.to_thread(usage.deck_file, build_id, name)
    if path is None:
        return _NOT_FOUND
    if name == "deck.pptx":
        return FileResponse(path, filename=f"palette-build-{build_id}.pptx", media_type=(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"))
    media = "image/png" if name.endswith(".png") else "text/markdown; charset=utf-8"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "private, max-age=300"})


@app.get("/stats/utterances.csv", include_in_schema=False)
async def stats_csv(request: Request):
    if not _stats_ok(request):
        return _NOT_FOUND
    rows = await asyncio.to_thread(usage.query,
        "SELECT ts, kind, text, slide_n, sources, thread_id, uid, visitor, client "
        "FROM utterances ORDER BY ts DESC")
    buf = io.StringIO()
    out = csv.writer(buf)
    out.writerow(["time_utc", "kind", "text", "slide_n", "sources",
                  "thread_id", "uid", "visitor", "client"])
    for r in rows:
        out.writerow([_csv_safe(v) for v in (
            time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(r["ts"])), r["kind"],
            r["text"], r["slide_n"], r["sources"], r["thread_id"],
            r["uid"], r["visitor"], r["client"])])
    return Response(buf.getvalue(), media_type="text/csv", headers={
        "Content-Disposition": 'attachment; filename="palette-utterances.csv"',
        **_STATS_HEADERS})


@app.get("/asset/{name}")
async def asset(name: str):
    """Serve static assets (logo, favicon, …) from deck_forge/assets/.
    Restricted to that directory — no path traversal."""
    target = (config.ROOT / "assets" / name).resolve()
    if (target.parent != (config.ROOT / "assets").resolve()
            or not target.is_file()):
        return JSONResponse({"error": "not found"}, status_code=404)
    media = "image/png" if target.suffix == ".png" else "application/octet-stream"
    return FileResponse(target, media_type=media,
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "roster": {role: spec.slug for role, spec in config.ROSTER.items()},
        "icons": len(config.available_icons()),
        "rits_key_set": bool(os.environ.get("RITS_API_KEY")),
        "data": usage.locations(),
        # false when every build role is on the CE fleet / watsonx
        "rits_needed": bool(config.rits_roles()),
        "backends": {role: config.spec_backend(spec)
                     for role, spec in config.ROSTER.items()},
        # where designer/coder actually go — the roster slug above stays the
        # RITS one even when PALETTE_CE_BASE_URL redirects them
        "designer_backend": config.PALETTE_ADAPTER.base_url or "rits",
        "gpt_oss_120b_backend": (config.WATSONX_URL
                                 if config.GPT_OSS_120B.provider == "watsonx"
                                 else "rits"),
        "available_models": config.available_models(),
        "keepwarm": _keepwarm,
        "builds": _builds.stats(),
    }


@app.get("/examples")
async def examples() -> dict:
    """User-facing example plans surfaced by the 'Start from an example plan'
    button. Curated list in config.USER_FACING_EXAMPLES so we control exactly
    what users see — reference_plans/ also holds crafter-only exemplars that
    we deliberately don't expose."""
    return {"examples": [
        {"file": fname, "label": label}
        for fname, label in config.USER_FACING_EXAMPLES
        if (config.REFERENCE_PLANS / fname).is_file()
    ]}


@app.get("/example/{name}")
async def example(name: str):
    target = (config.REFERENCE_PLANS / name).resolve()
    if (target.parent != config.REFERENCE_PLANS.resolve()
            or target.suffix != ".md" or not target.is_file()):
        return JSONResponse({"error": "not found"}, status_code=404)
    # Only allow files in the curated list — keeps crafter exemplars out of
    # the user-facing surface even if someone guesses a filename.
    allowed = {f for f, _ in config.USER_FACING_EXAMPLES}
    if name not in allowed:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"name": name, "content": target.read_text()}


# --- Build admission -----------------------------------------------------------
# At most config.MAX_CONCURRENT_BUILDS builds run at once; the rest wait in
# arrival order. A waiting session's /progress reads "queued — #N in line", so
# the UI (which shows progress messages as-is) explains the wait instead of
# sitting on "designing deck". Single event loop, so no lock is needed.

class BuildAborted(Exception):
    """A queued build was dropped (/abort or /clear) before it started."""


class BuildQueue:
    def __init__(self, limit: int) -> None:
        self.limit = max(1, limit)
        self.running = 0
        self.waiting: list[tuple[SlideSession, asyncio.Future]] = []

    async def acquire(self, session: SlideSession) -> None:
        if self.running < self.limit and not self.waiting:
            self.running += 1
            return
        fut = asyncio.get_running_loop().create_future()
        self.waiting.append((session, fut))
        self._announce()
        try:
            await fut
        except asyncio.CancelledError:
            self._forget(fut)
            raise

    def release(self) -> None:
        # Hand the slot straight to the next in line, so `running` is unchanged
        # and a newcomer can never jump the queue between release and wake-up.
        while self.waiting:
            _, fut = self.waiting.pop(0)
            if not fut.done():
                fut.set_result(None)
                self._announce()
                return
        self.running -= 1

    def drop(self, session: SlideSession) -> bool:
        """Remove `session` from the queue; its acquire() raises BuildAborted."""
        for entry in self.waiting:
            if entry[0] is session:
                self._forget(entry[1])
                entry[1].set_exception(BuildAborted("build was cancelled while queued"))
                return True
        return False

    def stats(self) -> dict:
        return {"running": self.running, "queued": len(self.waiting),
                "limit": self.limit}

    def _forget(self, fut: asyncio.Future) -> None:
        self.waiting = [e for e in self.waiting if e[1] is not fut]
        self._announce()

    def _announce(self) -> None:
        for pos, (s, _) in enumerate(self.waiting, 1):
            s.progress = {"stage": "queued",
                          "message": f"queued — #{pos} in line "
                                     f"({self.running} decks building)",
                          "current": 0, "total": 0}


_builds = BuildQueue(config.MAX_CONCURRENT_BUILDS)


async def _run_build(req: BuildReq, session: SlideSession) -> dict:
    """Run one deck build to completion and return the API payload.

    Shared by the blocking POST /build and the background POST /build_async
    so the two can never drift apart. Mutates `session` the same way in both
    cases; raises on failure, leaving the caller to shape the error response.
    Waits for a slot in _builds first; raises BuildAborted if dropped meanwhile.
    """
    await _builds.acquire(session)
    try:
        return await _run_admitted_build(req, session)
    finally:
        _builds.release()


async def _run_admitted_build(req: BuildReq, session: SlideSession) -> dict:
    t0 = time.time()

    def _progress(message: str, current: int, total: int) -> None:
        session.progress = {"stage": "build", "message": message,
                            "current": current, "total": total}

    def _run() -> dict:
        return generate_deck(
            req.plan.strip(), session.out_dir,
            deck_id=session.session_id[:12],
            palette_family=req.palette_family,
            progress=_progress,
        )

    config.apply_models(req.planner, req.designer_coder, req.critic)
    build_id = _build_ids[req.thread_id] = usage.start_build(
        req.thread_id, req.plan, req.palette_family, started_at=t0,
        plan_id=_plan_ids.get(req.thread_id), models=_models(req))
    log.info("=" * 60)
    log.info("build thread=%s palette=%s  models=[plan %s | design %s | "
             "critic %s]", req.thread_id, req.palette_family, req.planner,
             req.designer_coder, req.critic)
    try:
        result = await asyncio.to_thread(_run)
    # SystemExit, not just Exception: render.py signals missing prerequisites
    # (node, pptxgenjs, LibreOffice) with SystemExit, which derives from
    # BaseException. Escaping a background build task, it tears down the
    # uvicorn event loop and takes the whole server with it -- one bad build
    # killing the service for every session. Catch it here and report it as a
    # failed build. KeyboardInterrupt/CancelledError are deliberately NOT
    # swallowed: those mean the process really is shutting down.
    except (Exception, SystemExit) as exc:  # noqa: BLE001 — surfaced to the caller
        log.exception("build failed")
        usage.finish_build(build_id, ok=False, started_at=t0, error=str(exc))
        session.building = False
        session.last_error = str(exc)
        session.progress = {"stage": "error", "message": str(exc),
                            "current": 0, "total": 0}
        raise

    session.deck = result["deck"]
    session.pptx_path = result["pptx"]
    session.previews = result["previews"]
    session.lint = result["lint"]
    session.building = False
    session.progress = {"stage": "done", "message": "deck ready",
                        "current": 0, "total": 0}
    elapsed = time.time() - t0
    log.info("build thread=%s done: %d slides in %.1fs",
             req.thread_id, len(session.previews), elapsed)
    payload = {
        "slide_count": len(session.previews),
        "title": result["deck"].get("deck_title", ""),
        "elapsed": round(elapsed, 1),
        "lint": result["lint"],
        "unrepaired": result.get("repair_remaining", []),
        "geometry": result.get("geometry", {}),
        "retries": result.get("retries", {}),
    }
    session.last_result = payload
    # Also archives the deck to data/decks/ — the workspace copy does not
    # survive "New" or a container rebuild.
    await asyncio.to_thread(
        usage.finish_build, build_id, ok=True, started_at=t0,
        slides=payload["slide_count"], title=payload["title"],
        details={k: payload[k] for k in ("lint", "unrepaired", "geometry", "retries")},
        out_dir=session.out_dir, plan=req.plan)
    return payload


def _models(req: BuildReq) -> str:
    return (f"plan {config.ROSTER['crafter'].slug} · design {config.ROSTER['designer'].slug}"
            f" · coder {config.ROSTER['coder'].slug}")


def _begin_build(req: BuildReq) -> SlideSession | JSONResponse:
    """Validate the request and put the session into the building state."""
    if not req.plan.strip():
        return JSONResponse({"error": "empty plan"}, status_code=400)
    session = _session(req.thread_id)
    if session.building:
        return JSONResponse({"error": "a build is already running"},
                            status_code=409)
    session.reset()
    session.building = True
    return session


@app.post("/build")
async def build(req: BuildReq):
    _bind_session(req.thread_id)
    session = _begin_build(req)
    if isinstance(session, JSONResponse):
        return session
    try:
        return await _run_build(req, session)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# --- Background build ------------------------------------------------------
# Same pipeline as POST /build, started as a task so the request returns at
# once. Callers that cannot hold a connection open for the two-to-four minutes
# a deck takes -- agent sandboxes that cap how long one step may run, or any
# proxy with a request timeout -- poll /progress and then read /result.
#
# Strictly additive: /build is untouched in behaviour, and a client can detect
# whether a deployment has these routes from /openapi.json.

@app.post("/build_async")
async def build_async(req: BuildReq):
    _bind_session(req.thread_id)
    session = _begin_build(req)
    if isinstance(session, JSONResponse):
        return session

    async def _task() -> None:
        # The contextvar is per-task, so re-bind it here or this build's log
        # records fail every session filter and land nowhere.
        _bind_session(req.thread_id)
        try:
            await _run_build(req, session)
        # Nothing may escape a background task: an unretrieved exception is
        # noisy, and a SystemExit escaping here shuts down the event loop.
        # _run_build has already logged it and recorded it on the session,
        # where /progress and /result will report it.
        except (Exception, SystemExit):  # noqa: BLE001
            pass

    asyncio.create_task(_task())
    log.info("build_async thread=%s accepted", req.thread_id)
    return {"started": True, "thread_id": req.thread_id}


@app.get("/result/{thread_id}")
async def result(thread_id: str):
    """Terminal outcome of a background build.

    `stage` is "running" until the build ends, then "done" (with `result`) or
    "error" (with `error`). Polling /progress gives the live detail.
    """
    _bind_session(thread_id)
    s = _registry.get(thread_id)
    if s is None:
        return JSONResponse({"error": "unknown thread_id"}, status_code=404)
    if s.last_error is not None:
        return {"stage": "error", "result": None, "error": s.last_error}
    if s.last_result is not None:
        return {"stage": "done", "result": s.last_result, "error": None}
    return {"stage": "running" if s.building else str(s.progress.get("stage", "idle")),
            "result": None, "error": None}


# --- Stage 1 — intake ------------------------------------------------------

async def _stage_sources(session: SlideSession, files: list[UploadFile]) -> list[Path]:
    """Persist uploaded reference documents into the session's sources dir."""
    src_paths: list[Path] = []
    if not files:
        return src_paths
    srcdir = session.root / "sources"
    srcdir.mkdir(parents=True, exist_ok=True)
    for f in files:
        if not f.filename:
            continue
        dest = srcdir / Path(f.filename).name
        dest.write_bytes(await f.read())
        src_paths.append(dest)
    return src_paths


async def _run_draft(request: str, planner: str, src_paths: list[Path],
                     session: SlideSession, utterance_id: int | None = None) -> dict:
    """Run Stage 1 to completion and return the API payload.

    Shared by the blocking POST /draft and the background POST /draft_async.
    Raises on failure, recording the reason on the session first.
    """
    config.apply_models(planner=planner)
    log.info("draft thread=%s sources=%d request=%r",
             session.session_id, len(src_paths), request[:80])
    session.progress = {"stage": "draft", "message": "crafting plan",
                        "current": 0, "total": 0}
    t0 = time.time()
    track = dict(sources=[p.name for p in src_paths], model=config.ROSTER["crafter"].slug)
    try:
        plan = await asyncio.to_thread(craft_plan, request, src_paths)
    # SystemExit as well as Exception: intake shells out for document parsing,
    # and an escaping SystemExit would kill the event loop from a background
    # task -- the same failure mode that took the server down during a build.
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        log.exception("draft failed")
        usage.record_plan(utterance_id, session.session_id, "draft", t0,
                          ok=False, error=str(exc), **track)
        session.drafting = False
        session.last_draft_error = str(exc)
        session.progress = {"stage": "error", "message": str(exc),
                            "current": 0, "total": 0}
        raise

    _plan_ids[session.session_id] = usage.record_plan(
        utterance_id, session.session_id, "draft", t0, ok=True, plan=plan, **track)
    session.drafting = False
    session.last_plan = plan
    session.progress = {"stage": "draft_done", "message": "plan ready",
                        "current": 0, "total": 0}
    return {"plan": plan, "sources": [p.name for p in src_paths]}


@app.post("/draft")
async def draft(request: str = Form(...), thread_id: str = Form("default"),
                planner: str = Form("gpt-oss-120b"), origin: str = Form("typed"),
                files: list[UploadFile] = File(default=[])):
    _bind_session(thread_id)
    request = request.strip()
    if not request:
        return JSONResponse({"error": "empty request"}, status_code=400)
    session = _session(thread_id)
    src_paths = await _stage_sources(session, files)
    utt = usage.record_utterance("example" if origin == "example" else "request",
                                 request, thread_id, attachments=[p.name for p in src_paths])
    try:
        return await _run_draft(request, planner, src_paths, session, utt)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# Stage 1 in the background. Crafting a plan from reference documents takes
# 60-90s -- longer than the per-step limit an agent sandbox allows -- so the
# same start/poll/collect shape as /build_async applies here. Feature-detected
# via /openapi.json, so older deployments simply keep using blocking /draft.

@app.post("/draft_async")
async def draft_async(request: str = Form(...), thread_id: str = Form("default"),
                      planner: str = Form("gpt-oss-120b"), origin: str = Form("typed"),
                      files: list[UploadFile] = File(default=[])):
    _bind_session(thread_id)
    request = request.strip()
    if not request:
        return JSONResponse({"error": "empty request"}, status_code=400)
    session = _session(thread_id)
    if session.drafting:
        return JSONResponse({"error": "a draft is already running"}, status_code=409)
    # Uploads must be read before returning: the request body is gone once the
    # response is sent, so staging them inside the task would read a closed file.
    src_paths = await _stage_sources(session, files)
    utt = usage.record_utterance("example" if origin == "example" else "request",
                                 request, thread_id, attachments=[p.name for p in src_paths])
    session.drafting = True
    session.last_plan = None
    session.last_draft_error = None

    async def _task() -> None:
        _bind_session(thread_id)
        try:
            await _run_draft(request, planner, src_paths, session, utt)
        except (Exception, SystemExit):  # noqa: BLE001 — recorded on the session
            pass

    asyncio.create_task(_task())
    log.info("draft_async thread=%s accepted (%d source(s))", thread_id, len(src_paths))
    return {"started": True, "thread_id": thread_id, "sources": [p.name for p in src_paths]}


@app.get("/draft_result/{thread_id}")
async def draft_result(thread_id: str):
    """Terminal outcome of a background draft: the plan, or the error."""
    _bind_session(thread_id)
    s = _registry.get(thread_id)
    if s is None:
        return JSONResponse({"error": "unknown thread_id"}, status_code=404)
    if s.last_draft_error is not None:
        return {"stage": "error", "plan": None, "error": s.last_draft_error}
    if s.last_plan is not None:
        return {"stage": "done", "plan": s.last_plan, "error": None}
    return {"stage": "running" if s.drafting else "idle", "plan": None, "error": None}


class EditPlanReq(BaseModel):
    plan: str
    instruction: str
    thread_id: str = "default"
    planner: str = "gpt-oss-120b"


@app.post("/edit_plan_async")
async def edit_plan_async(req: EditPlanReq):
    """Revise a plan in the background; collect it from /draft_result.

    The HTTP form of `palette.py edit-plan` (same `intake.edit_plan`), used by
    the skill's remote mode. It records its result exactly where a draft does
    — `last_plan` / `last_draft_error` — so one poll endpoint serves both.
    """
    _bind_session(req.thread_id)
    if not req.plan.strip() or not req.instruction.strip():
        return JSONResponse({"error": "plan and instruction are required"}, status_code=400)
    session = _session(req.thread_id)
    if session.drafting:
        return JSONResponse({"error": "a draft is already running"}, status_code=409)
    utt = usage.record_utterance("plan_edit", req.instruction, req.thread_id)
    session.drafting = True
    session.last_plan = None
    session.last_draft_error = None

    async def _task() -> None:
        _bind_session(req.thread_id)
        config.apply_models(planner=req.planner)
        session.progress = {"stage": "draft", "message": "revising plan",
                            "current": 0, "total": 0}
        t0 = time.time()
        try:
            plan = await asyncio.to_thread(intake_edit_plan, req.plan, req.instruction)
        # Nothing may escape a background task (see build_async).
        except (Exception, SystemExit) as exc:  # noqa: BLE001 — recorded on the session
            log.exception("edit_plan failed")
            usage.record_plan(utt, req.thread_id, "plan_edit", t0, ok=False, error=str(exc),
                              model=config.ROSTER["crafter"].slug)
            session.drafting = False
            session.last_draft_error = str(exc)
            session.progress = {"stage": "error", "message": str(exc),
                                "current": 0, "total": 0}
            return
        _plan_ids[req.thread_id] = usage.record_plan(
            utt, req.thread_id, "plan_edit", t0, ok=True, plan=plan,
            model=config.ROSTER["crafter"].slug)
        session.drafting = False
        session.last_plan = plan
        session.progress = {"stage": "draft_done", "message": "plan ready",
                            "current": 0, "total": 0}

    asyncio.create_task(_task())
    log.info("edit_plan_async thread=%s accepted", req.thread_id)
    return {"started": True, "thread_id": req.thread_id}


# --- Stage 3 — refine ------------------------------------------------------

class EditReq(BaseModel):
    thread_id: str = "default"
    slide_n: int
    instruction: str


@app.post("/edit")
async def edit(req: EditReq):
    _bind_session(req.thread_id)
    s = _registry.get(req.thread_id)
    if s is None or s.deck is None:
        return JSONResponse({"error": "no deck to edit — build one first"},
                            status_code=400)
    if s.building:
        return JSONResponse({"error": "busy"}, status_code=409)
    instruction = req.instruction.strip()
    if not instruction:
        return JSONResponse({"error": "empty instruction"}, status_code=400)

    utt = usage.record_utterance("slide_edit", instruction, req.thread_id, slide_n=req.slide_n)
    t0 = time.time()
    s.building = True
    log.info("edit thread=%s slide=%d: %r",
             req.thread_id, req.slide_n, instruction[:80])
    try:
        result = await asyncio.to_thread(
            apply_nl_edit, s.out_dir, s.deck, req.slide_n, instruction)
    except Exception as exc:  # noqa: BLE001
        s.building = False
        log.exception("edit failed")
        usage.record_edit(_build_ids.get(req.thread_id), req.thread_id, s.out_dir,
                          reason="slide_edit", started_at=t0, ok=False, error=str(exc),
                          utterance_id=utt, slide_n=req.slide_n)
        return JSONResponse({"error": str(exc)}, status_code=500)
    s.previews = result["previews"]
    s.pptx_path = result["pptx"]
    s.building = False
    await asyncio.to_thread(usage.record_edit, _build_ids.get(req.thread_id), req.thread_id,
                            s.out_dir, reason="slide_edit", started_at=t0, ok=True,
                            utterance_id=utt, slides=len(s.previews), slide_n=req.slide_n)
    return {"slide_count": len(s.previews), "edited": req.slide_n}


@app.post("/retry/{thread_id}/{slide_n}")
async def retry(thread_id: str, slide_n: int):
    """Re-run the coder for one slide at temp 0.3, then run a single-slide
    geometry pass (detector + editor + verify gate) so the retry gets the
    same post-processing the full build does. Same brief, same plan — just
    a different sampling, then a correction pass scoped to this slide."""
    _bind_session(thread_id)
    s = _registry.get(thread_id)
    if s is None or s.deck is None:
        return JSONResponse({"error": "no deck — build one first"},
                            status_code=400)
    if s.building:
        return JSONResponse({"error": "busy"}, status_code=409)
    s.building = True
    t0 = time.time()
    log.info("retry thread=%s slide=%d", thread_id, slide_n)
    try:
        def _run():
            result = retry_slide(s.deck, s.out_dir, slide_n)
            pptx, previews = _rerender(s.out_dir)
            return result, pptx, previews
        result, pptx, previews = await asyncio.to_thread(_run)
    except Exception as exc:  # noqa: BLE001
        s.building = False
        log.exception("retry failed")
        usage.record_edit(_build_ids.get(thread_id), thread_id, s.out_dir, reason="retry",
                          started_at=t0, ok=False, error=str(exc), slide_n=slide_n)
        return JSONResponse({"error": str(exc)}, status_code=500)
    s.pptx_path = pptx
    s.previews = previews
    s.building = False
    await asyncio.to_thread(usage.record_edit, _build_ids.get(thread_id), thread_id,
                            s.out_dir, reason="retry", started_at=t0, ok=True,
                            slides=len(s.previews), slide_n=slide_n)
    return {"slide_count": len(s.previews),
            "retried": slide_n,
            "geometry": result.get("geometry", {})}


@app.get("/progress/{thread_id}")
async def progress(thread_id: str) -> dict:
    _bind_session(thread_id)
    s = _registry.get(thread_id)
    return s.progress if s else {"stage": "idle", "message": "",
                                  "current": 0, "total": 0}


@app.get("/deck/{thread_id}")
async def deck(thread_id: str) -> dict:
    _bind_session(thread_id)
    s = _registry.get(thread_id)
    if s is None:
        return {"slide_count": 0, "title": "", "building": False}
    return {
        "slide_count": len(s.previews),
        "title": (s.deck or {}).get("deck_title", ""),
        "building": s.building,
    }


@app.get("/preview/{thread_id}/{idx}")
async def preview(thread_id: str, idx: int):
    _bind_session(thread_id)
    s = _registry.get(thread_id)
    if s is None or not (0 < idx <= len(s.previews)):
        return JSONResponse({"error": "slide not rendered"}, status_code=404)
    return FileResponse(s.previews[idx - 1], media_type="image/png")


@app.get("/download/{thread_id}")
async def download(thread_id: str):
    _bind_session(thread_id)
    s = _registry.get(thread_id)
    if s is None or s.pptx_path is None or not s.pptx_path.exists():
        return JSONResponse({"error": "deck not rendered yet"}, status_code=404)
    return FileResponse(
        s.pptx_path,
        media_type=("application/vnd.openxmlformats-officedocument"
                    ".presentationml.presentation"),
        filename="deck.pptx",
    )


@app.post("/clear/{thread_id}")
async def clear(thread_id: str) -> dict:
    _bind_session(thread_id)
    with _registry_lock:
        s = _registry.pop(thread_id, None)
    if s is not None:
        # Detach + close the file handler BEFORE deleting the directory the
        # file lives in; otherwise the OS keeps the inode alive and the
        # next session_log opens a fresh inode while this one leaks.
        _builds.drop(s)
        _remove_session_log(s)
        shutil.rmtree(s.root, ignore_errors=True)
    return {"cleared": s is not None}


@app.post("/abort/{thread_id}")
async def abort(thread_id: str) -> dict:
    """Mark the session not-building so the UI can move on. The in-flight
    build thread (if any) keeps running server-side until it completes — a v2
    would thread cancellation tokens through the pipeline to actually stop it.
    For now this just unblocks the UI."""
    _bind_session(thread_id)
    s = _registry.get(thread_id)
    if s is None:
        return {"aborted": False}
    # A queued build is removed outright; a running one keeps going (above).
    _builds.drop(s)
    s.building = False
    s.progress = {"stage": "aborted", "message": "stopped",
                  "current": 0, "total": 0}
    log.info("abort thread=%s", thread_id)
    return {"aborted": True}


def main() -> None:
    # PORT env var takes precedence -- that's how IBM Code Engine (and most
    # PaaS) inject the bind port. Fall back to --port, then to 18814 for
    # local dev. host=0.0.0.0 so a container's mapped port is reachable.
    p = argparse.ArgumentParser(description="Palette")
    p.add_argument("--port", type=int, default=None)
    args = p.parse_args()
    port = int(os.environ.get("PORT") or args.port or 18814)

    # parents=True: PALETTE_WORKSPACE may point somewhere nested that does not
    # exist yet (e.g. ~/.local/state/palette/workspace under a service).
    config.WORKSPACE.mkdir(parents=True, exist_ok=True)
    if config.rits_roles() and not os.environ.get("RITS_API_KEY"):
        log.warning("RITS_API_KEY not set — %s route to RITS, so builds will "
                    "fail until it is exported.", ", ".join(config.rits_roles()))
    log.info("icons: %d   roster: %s", len(config.available_icons()),
             {role: spec.slug for role, spec in config.ROSTER.items()})
    print(f"\n  Palette  ->  http://127.0.0.1:{port}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
