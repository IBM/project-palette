from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from threading import Lock

from pydantic import BaseModel

_DIR = Path(__file__).parent
sys.path.insert(0, str(_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s [%(name)-8s] %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("ask")

from agent_core import build_sandbox, run_agent  # noqa: E402
from tools import SlideSession, make_tools  # noqa: E402
from ui import HTML  # noqa: E402


class AskReq(BaseModel):
    question: str
    thread_id: str = "default"


SYSTEM_PROMPT = """\
You are Palette, a conversational slide-building assistant. You help the user create and iterate on a deck through chat.

## FORMAT — every turn

Every reply MUST be a fenced ```python ... ``` block. To talk to the user, call `final_answer("…")` inside it.

  Pattern A — plan a fresh deck (most common for a new request):
  ```python
  plan = plan_deck("Topic here")
  final_answer("Here's my plan — say 'build it' to proceed or describe changes:\\n\\n" + plan)
  ```
  Note: `final_answer(plan)` shows the plan to the user. `final_answer("Here's the plan…")` without `+ plan` does NOT — the user never sees what plan_deck produced.

  Pattern B — build after the user confirms:
  ```python
  status = build_deck()
  final_answer("Done.\\n\\n" + status)
  ```

  Pattern C — answer or clarify without building:
  ```python
  final_answer("…your reply…")
  ```

Do NOT write `final_answer(...)` in prose outside the fence. The user only sees what `final_answer` was called with.

## Tools

- plan_deck(topic) -> str
    Two-stage planning: reflection on genre/audience/needs, then a structured blueprint. Returns either clarifying questions or the plan summary. Stores the blueprint on the session for build_deck.

- build_deck() -> str
    Gathers evidence for slides that need it, designs each slide as pptxgenjs, renders to .pptx + PNG previews. Uses the blueprint stored from the most recent plan_deck call. Long-running (~10-20s per slide).

- clear_deck() -> str
    Wipes the current plan, blueprint, and rendered files. Use when starting a fresh deck.

- web_search(query, max_results=4) -> str
- read_webpage(url) -> str
    Use sparingly. The build pipeline already runs web_search on slides whose blueprint flagged evidence_needed; you only need to call these tools yourself when the user asks a factual question mid-conversation.

## Workflow

1. New topic from the user → call `plan_deck(topic)`, show the plan via final_answer, wait for the user's confirmation.
2. User says "build it" / "go ahead" / "looks good" → call `build_deck()` and final_answer the status.
3. User asks for changes to the plan → re-call `plan_deck` with a refined prompt (or relay specific edits) and show the new plan.

Do NOT call build_deck in the same turn as plan_deck unless the user explicitly said "build it" or "go ahead" in their original message.

## Style

- Decks default to a clean professional aesthetic. The model designs each slide freely against a 16:9 canvas using pptxgenjs primitives.
- For numerical claims, the build pipeline runs web research on flagged slides and the per-slide writer marks unverified specifics as "(illustrative — pending review)".
- Don't invent presenter names, company names, taglines, or branding the user didn't supply.
"""


_SESSIONS_ROOT = _DIR / "sessions"
_SESSIONS_ROOT.mkdir(exist_ok=True)
_registry: dict[str, dict] = {}
_registry_lock = Lock()


def _get_or_create_session(thread_id: str) -> dict:
    with _registry_lock:
        if thread_id in _registry:
            return _registry[thread_id]
        session = SlideSession.create(_SESSIONS_ROOT, session_id=thread_id)
        tools = make_tools(session)
        ns = build_sandbox(tools)
        entry = {"session": session, "namespace": ns, "history": []}
        _registry[thread_id] = entry
        log.info("new session: %s", thread_id)
        return entry


def _web(port: int) -> None:
    import uvicorn
    from fastapi import FastAPI
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

    app = FastAPI(title="Palette", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse(HTML)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.post("/ask")
    async def ask(req: AskReq):
        question = req.question.strip()
        if not question:
            return JSONResponse({"error": "empty question"}, status_code=400)
        log.info("─" * 70)
        log.info("thread=%s q=%r", req.thread_id, question[:160])
        entry = _get_or_create_session(req.thread_id)
        t0 = time.time()
        try:
            answer, trace = await asyncio.to_thread(
                run_agent,
                user_message=question,
                namespace=entry["namespace"],
                message_history=entry["history"],
                system_prompt=SYSTEM_PROMPT,
                max_iterations=6,
            )
            log.info("thread=%s done in %.1fs (%d iter)",
                     req.thread_id, time.time() - t0, len(trace))
            return {"answer": answer}
        except Exception as exc:
            log.exception("agent error")
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.get("/progress/{thread_id}")
    async def progress(thread_id: str):
        if thread_id not in _registry:
            return {"stage": "idle", "current": 0, "total": 0, "message": ""}
        return _registry[thread_id]["session"].progress or {
            "stage": "idle", "current": 0, "total": 0, "message": "",
        }

    def _slide_pngs(session: SlideSession) -> list[Path]:
        return sorted(session.output_dir.glob("slide-*.png"))

    @app.get("/deck/{thread_id}")
    async def get_deck(thread_id: str):
        if thread_id not in _registry:
            return {"slide_count": 0, "title": "", "subtitle": ""}
        session: SlideSession = _registry[thread_id]["session"]
        bp = session.blueprint or {}
        return {
            "title": bp.get("deck_title", ""),
            "subtitle": bp.get("deck_subtitle", ""),
            "slide_count": len(_slide_pngs(session)),
        }

    @app.get("/preview/{thread_id}/{idx}")
    async def preview(thread_id: str, idx: int):
        if thread_id not in _registry:
            return JSONResponse({"error": "unknown session"}, status_code=404)
        session: SlideSession = _registry[thread_id]["session"]
        pngs = _slide_pngs(session)
        if not (0 < idx <= len(pngs)):
            return JSONResponse({"error": "slide not rendered"}, status_code=404)
        return FileResponse(pngs[idx - 1], media_type="image/png")

    @app.get("/download/{thread_id}")
    async def download(thread_id: str):
        if thread_id not in _registry:
            return JSONResponse({"error": "unknown session"}, status_code=404)
        session: SlideSession = _registry[thread_id]["session"]
        if not session.pptx_path.exists():
            return JSONResponse({"error": "deck not rendered yet"}, status_code=404)
        return FileResponse(
            session.pptx_path,
            media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
            filename="deck.pptx",
        )

    @app.post("/clear/{thread_id}")
    async def clear(thread_id: str):
        with _registry_lock:
            entry = _registry.pop(thread_id, None)
        if entry is None:
            return {"cleared": False}
        import shutil
        try:
            shutil.rmtree(entry["session"].root, ignore_errors=True)
        except Exception:
            pass
        return {"cleared": True}

    print(f"\n  Palette  →  http://127.0.0.1:{port}\n")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


def main() -> None:
    parser = argparse.ArgumentParser(description="Palette — conversational slide builder")
    parser.add_argument("--port", type=int, default=18814)
    args = parser.parse_args()

    if not os.environ.get("RITS_API_KEY"):
        log.warning("RITS_API_KEY not set — /ask will fail until exported.")

    _web(args.port)


if __name__ == "__main__":
    main()
