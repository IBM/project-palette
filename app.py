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
import logging
import os
import shutil
import time
from pathlib import Path
from threading import Lock

import uvicorn
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

import config
from intake import craft_plan
from pipeline import generate_deck, retry_slide
from refine import _rerender, apply_nl_edit
from session import SlideSession
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


app = FastAPI(title="Palette", docs_url=None, redoc_url=None)


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse(HTML)


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


async def _run_build(req: BuildReq, session: SlideSession) -> dict:
    """Run one deck build to completion and return the API payload.

    Shared by the blocking POST /build and the background POST /build_async
    so the two can never drift apart. Mutates `session` the same way in both
    cases; raises on failure, leaving the caller to shape the error response.
    """
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
    return payload


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
    _session_id_var.set(req.thread_id)
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
    _session_id_var.set(req.thread_id)
    session = _begin_build(req)
    if isinstance(session, JSONResponse):
        return session

    async def _task() -> None:
        # The contextvar is per-task, so re-bind it here or this build's log
        # records fail every session filter and land nowhere.
        _session_id_var.set(req.thread_id)
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
    _session_id_var.set(thread_id)
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
                     session: SlideSession) -> dict:
    """Run Stage 1 to completion and return the API payload.

    Shared by the blocking POST /draft and the background POST /draft_async.
    Raises on failure, recording the reason on the session first.
    """
    config.apply_models(planner=planner)
    log.info("draft thread=%s sources=%d request=%r",
             session.session_id, len(src_paths), request[:80])
    session.progress = {"stage": "draft", "message": "crafting plan",
                        "current": 0, "total": 0}
    try:
        plan = await asyncio.to_thread(craft_plan, request, src_paths)
    # SystemExit as well as Exception: intake shells out for document parsing,
    # and an escaping SystemExit would kill the event loop from a background
    # task -- the same failure mode that took the server down during a build.
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        log.exception("draft failed")
        session.drafting = False
        session.last_draft_error = str(exc)
        session.progress = {"stage": "error", "message": str(exc),
                            "current": 0, "total": 0}
        raise

    session.drafting = False
    session.last_plan = plan
    session.progress = {"stage": "draft_done", "message": "plan ready",
                        "current": 0, "total": 0}
    return {"plan": plan, "sources": [p.name for p in src_paths]}


@app.post("/draft")
async def draft(request: str = Form(...), thread_id: str = Form("default"),
                planner: str = Form("gpt-oss-120b"),
                files: list[UploadFile] = File(default=[])):
    _session_id_var.set(thread_id)
    request = request.strip()
    if not request:
        return JSONResponse({"error": "empty request"}, status_code=400)
    session = _session(thread_id)
    src_paths = await _stage_sources(session, files)
    try:
        return await _run_draft(request, planner, src_paths, session)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


# Stage 1 in the background. Crafting a plan from reference documents takes
# 60-90s -- longer than the per-step limit an agent sandbox allows -- so the
# same start/poll/collect shape as /build_async applies here. Feature-detected
# via /openapi.json, so older deployments simply keep using blocking /draft.

@app.post("/draft_async")
async def draft_async(request: str = Form(...), thread_id: str = Form("default"),
                      planner: str = Form("gpt-oss-120b"),
                      files: list[UploadFile] = File(default=[])):
    _session_id_var.set(thread_id)
    request = request.strip()
    if not request:
        return JSONResponse({"error": "empty request"}, status_code=400)
    session = _session(thread_id)
    if session.drafting:
        return JSONResponse({"error": "a draft is already running"}, status_code=409)
    # Uploads must be read before returning: the request body is gone once the
    # response is sent, so staging them inside the task would read a closed file.
    src_paths = await _stage_sources(session, files)
    session.drafting = True
    session.last_plan = None
    session.last_draft_error = None

    async def _task() -> None:
        _session_id_var.set(thread_id)
        try:
            await _run_draft(request, planner, src_paths, session)
        except (Exception, SystemExit):  # noqa: BLE001 — recorded on the session
            pass

    asyncio.create_task(_task())
    log.info("draft_async thread=%s accepted (%d source(s))", thread_id, len(src_paths))
    return {"started": True, "thread_id": thread_id, "sources": [p.name for p in src_paths]}


@app.get("/draft_result/{thread_id}")
async def draft_result(thread_id: str):
    """Terminal outcome of a background draft: the plan, or the error."""
    _session_id_var.set(thread_id)
    s = _registry.get(thread_id)
    if s is None:
        return JSONResponse({"error": "unknown thread_id"}, status_code=404)
    if s.last_draft_error is not None:
        return {"stage": "error", "plan": None, "error": s.last_draft_error}
    if s.last_plan is not None:
        return {"stage": "done", "plan": s.last_plan, "error": None}
    return {"stage": "running" if s.drafting else "idle", "plan": None, "error": None}


# --- Stage 3 — refine ------------------------------------------------------

class EditReq(BaseModel):
    thread_id: str = "default"
    slide_n: int
    instruction: str


@app.post("/edit")
async def edit(req: EditReq):
    _session_id_var.set(req.thread_id)
    s = _registry.get(req.thread_id)
    if s is None or s.deck is None:
        return JSONResponse({"error": "no deck to edit — build one first"},
                            status_code=400)
    if s.building:
        return JSONResponse({"error": "busy"}, status_code=409)
    instruction = req.instruction.strip()
    if not instruction:
        return JSONResponse({"error": "empty instruction"}, status_code=400)

    s.building = True
    log.info("edit thread=%s slide=%d: %r",
             req.thread_id, req.slide_n, instruction[:80])
    try:
        result = await asyncio.to_thread(
            apply_nl_edit, s.out_dir, s.deck, req.slide_n, instruction)
    except Exception as exc:  # noqa: BLE001
        s.building = False
        log.exception("edit failed")
        return JSONResponse({"error": str(exc)}, status_code=500)
    s.previews = result["previews"]
    s.pptx_path = result["pptx"]
    s.building = False
    return {"slide_count": len(s.previews), "edited": req.slide_n}


@app.post("/retry/{thread_id}/{slide_n}")
async def retry(thread_id: str, slide_n: int):
    """Re-run the coder for one slide at temp 0.3, then run a single-slide
    geometry pass (detector + editor + verify gate) so the retry gets the
    same post-processing the full build does. Same brief, same plan — just
    a different sampling, then a correction pass scoped to this slide."""
    _session_id_var.set(thread_id)
    s = _registry.get(thread_id)
    if s is None or s.deck is None:
        return JSONResponse({"error": "no deck — build one first"},
                            status_code=400)
    if s.building:
        return JSONResponse({"error": "busy"}, status_code=409)
    s.building = True
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
        return JSONResponse({"error": str(exc)}, status_code=500)
    s.pptx_path = pptx
    s.previews = previews
    s.building = False
    return {"slide_count": len(s.previews),
            "retried": slide_n,
            "geometry": result.get("geometry", {})}


@app.get("/progress/{thread_id}")
async def progress(thread_id: str) -> dict:
    _session_id_var.set(thread_id)
    s = _registry.get(thread_id)
    return s.progress if s else {"stage": "idle", "message": "",
                                  "current": 0, "total": 0}


@app.get("/deck/{thread_id}")
async def deck(thread_id: str) -> dict:
    _session_id_var.set(thread_id)
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
    _session_id_var.set(thread_id)
    s = _registry.get(thread_id)
    if s is None or not (0 < idx <= len(s.previews)):
        return JSONResponse({"error": "slide not rendered"}, status_code=404)
    return FileResponse(s.previews[idx - 1], media_type="image/png")


@app.get("/download/{thread_id}")
async def download(thread_id: str):
    _session_id_var.set(thread_id)
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
    _session_id_var.set(thread_id)
    with _registry_lock:
        s = _registry.pop(thread_id, None)
    if s is not None:
        # Detach + close the file handler BEFORE deleting the directory the
        # file lives in; otherwise the OS keeps the inode alive and the
        # next session_log opens a fresh inode while this one leaks.
        _remove_session_log(s)
        shutil.rmtree(s.root, ignore_errors=True)
    return {"cleared": s is not None}


@app.post("/abort/{thread_id}")
async def abort(thread_id: str) -> dict:
    """Mark the session not-building so the UI can move on. The in-flight
    build thread (if any) keeps running server-side until it completes — a v2
    would thread cancellation tokens through the pipeline to actually stop it.
    For now this just unblocks the UI."""
    _session_id_var.set(thread_id)
    s = _registry.get(thread_id)
    if s is None:
        return {"aborted": False}
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
    if not os.environ.get("RITS_API_KEY"):
        log.warning("RITS_API_KEY not set — builds will fail until exported.")
    log.info("icons: %d   roster: %s", len(config.available_icons()),
             {role: spec.slug for role, spec in config.ROSTER.items()})
    print(f"\n  Palette  ->  http://127.0.0.1:{port}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
