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


@app.post("/build")
async def build(req: BuildReq):
    _session_id_var.set(req.thread_id)
    plan = req.plan.strip()
    if not plan:
        return JSONResponse({"error": "empty plan"}, status_code=400)

    session = _session(req.thread_id)
    if session.building:
        return JSONResponse({"error": "a build is already running"},
                            status_code=409)
    session.reset()
    session.building = True
    t0 = time.time()

    def _progress(message: str, current: int, total: int) -> None:
        session.progress = {"stage": "build", "message": message,
                            "current": current, "total": total}

    def _run() -> dict:
        return generate_deck(
            plan, session.out_dir,
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
    except Exception as exc:  # noqa: BLE001 — surface any failure to the UI
        log.exception("build failed")
        session.building = False
        session.progress = {"stage": "error", "message": str(exc),
                            "current": 0, "total": 0}
        return JSONResponse({"error": str(exc)}, status_code=500)

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
    return {
        "slide_count": len(session.previews),
        "title": result["deck"].get("deck_title", ""),
        "elapsed": round(elapsed, 1),
        "lint": result["lint"],
        "unrepaired": result.get("repair_remaining", []),
        "geometry": result.get("geometry", {}),
        "retries": result.get("retries", {}),
    }


# --- Stage 1 — intake ------------------------------------------------------

@app.post("/draft")
async def draft(request: str = Form(...), thread_id: str = Form("default"),
                planner: str = Form("gpt-oss-120b"),
                files: list[UploadFile] = File(default=[])):
    _session_id_var.set(thread_id)
    request = request.strip()
    if not request:
        return JSONResponse({"error": "empty request"}, status_code=400)
    config.apply_models(planner=planner)
    session = _session(thread_id)

    src_paths: list[Path] = []
    if files:
        srcdir = session.root / "sources"
        srcdir.mkdir(exist_ok=True)
        for f in files:
            if not f.filename:
                continue
            dest = srcdir / Path(f.filename).name
            dest.write_bytes(await f.read())
            src_paths.append(dest)

    log.info("draft thread=%s sources=%d request=%r",
             thread_id, len(src_paths), request[:80])
    try:
        plan = await asyncio.to_thread(craft_plan, request, src_paths)
    except Exception as exc:  # noqa: BLE001
        log.exception("draft failed")
        return JSONResponse({"error": str(exc)}, status_code=500)
    return {"plan": plan, "sources": [p.name for p in src_paths]}


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
    """Re-run the coder for one slide at temp 0.3. Same brief, same plan —
    just a different sampling. Used when a slide looks like a bad roll."""
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
            retry_slide(s.deck, s.out_dir, slide_n)
            return _rerender(s.out_dir)
        pptx, previews = await asyncio.to_thread(_run)
    except Exception as exc:  # noqa: BLE001
        s.building = False
        log.exception("retry failed")
        return JSONResponse({"error": str(exc)}, status_code=500)
    s.pptx_path = pptx
    s.previews = previews
    s.building = False
    return {"slide_count": len(s.previews), "retried": slide_n}


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

    config.WORKSPACE.mkdir(exist_ok=True)
    if not os.environ.get("RITS_API_KEY"):
        log.warning("RITS_API_KEY not set — builds will fail until exported.")
    log.info("icons: %d   roster: %s", len(config.available_icons()),
             {role: spec.slug for role, spec in config.ROSTER.items()})
    print(f"\n  Palette  ->  http://127.0.0.1:{port}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
