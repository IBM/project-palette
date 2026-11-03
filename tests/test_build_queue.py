"""Build admission: the concurrency cap and the queue a waiting user sees.

No model is called. BuildQueue is exercised directly for ordering, hand-off
and abort; one end-to-end test drives /build_async through the app with
generate_deck stubbed, to check /progress and /health report the queue.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

fastapi_testclient = pytest.importorskip("fastapi.testclient")

# config and usage read these at import: keep the test off the real data dir,
# then put the environment back so later tests (test_service) see their own.
_TMP = Path(tempfile.mkdtemp(prefix="palette-queue-test-"))
_saved_env = dict(os.environ)
os.environ.update(PALETTE_WORKSPACE=str(_TMP / "workspace"),
                  PALETTE_DATA_DIR=str(_TMP / "data"),
                  PALETTE_KEEPWARM_MINUTES="0")
try:
    import app as palette_app  # noqa: E402
    from session import SlideSession  # noqa: E402
finally:
    os.environ.clear()
    os.environ.update(_saved_env)


def _session(name: str) -> SlideSession:
    return SlideSession.create(_TMP / "queue-sessions", name)


# -- BuildQueue ----------------------------------------------------------------


def test_queue_admits_up_to_limit_then_serves_in_arrival_order() -> None:
    async def scenario() -> None:
        q = palette_app.BuildQueue(2)
        a, b, c, d = (_session(n) for n in "abcd")
        await q.acquire(a)
        await q.acquire(b)
        started: list[str] = []

        async def wait(s: SlideSession) -> None:
            await q.acquire(s)
            started.append(s.session_id)

        tc = asyncio.create_task(wait(c))
        td = asyncio.create_task(wait(d))
        await asyncio.sleep(0)
        assert q.stats() == {"running": 2, "queued": 2, "limit": 2}
        assert c.progress["stage"] == "queued"
        assert "#1 in line" in c.progress["message"]
        assert "#2 in line" in d.progress["message"]

        q.release()  # a finishes: c takes its slot, d moves up
        await tc
        assert started == ["c"]
        assert q.stats()["running"] == 2
        assert "#1 in line" in d.progress["message"]

        q.release()
        await td
        assert started == ["c", "d"]
        q.release()
        q.release()
        assert q.stats() == {"running": 0, "queued": 0, "limit": 2}

    asyncio.run(scenario())


def test_dropping_a_queued_build_raises_and_frees_its_place() -> None:
    async def scenario() -> None:
        q = palette_app.BuildQueue(1)
        a, b, c = (_session(n) for n in "abc")
        await q.acquire(a)
        tb = asyncio.create_task(q.acquire(b))
        tc = asyncio.create_task(q.acquire(c))
        await asyncio.sleep(0)

        assert q.drop(b) is True
        with pytest.raises(palette_app.BuildAborted):
            await tb
        assert "#1 in line" in c.progress["message"]
        assert q.drop(a) is False, "a running build is not in the queue"

        q.release()
        await tc
        assert q.stats() == {"running": 1, "queued": 0, "limit": 1}

    asyncio.run(scenario())


def test_cancelled_waiter_leaves_the_queue() -> None:
    async def scenario() -> None:
        q = palette_app.BuildQueue(1)
        await q.acquire(_session("a"))
        t = asyncio.create_task(q.acquire(_session("b")))
        await asyncio.sleep(0)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
        assert q.stats()["queued"] == 0
        q.release()
        assert q.stats()["running"] == 0

    asyncio.run(scenario())


# -- through the app -------------------------------------------------------------


def test_build_async_queues_past_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = threading.Event()

    def fake_generate_deck(plan_md, out_dir, **kw):
        gate.wait(timeout=10)
        return {"deck": {"deck_title": "t", "slides": []}, "pptx": None,
                "previews": [], "lint": []}

    monkeypatch.setattr(palette_app, "generate_deck", fake_generate_deck)
    monkeypatch.setattr(palette_app, "_builds", palette_app.BuildQueue(1))

    with fastapi_testclient.TestClient(palette_app.app) as client:
        for tid in ("q-1", "q-2", "q-3"):
            r = client.post("/build_async", json={"plan": "# x", "thread_id": tid})
            assert r.status_code == 200, r.text

        deadline = time.time() + 5
        while client.get("/health").json()["builds"]["queued"] < 2:
            assert time.time() < deadline, "builds never queued"
            time.sleep(0.05)
        assert client.get("/health").json()["builds"] == {
            "running": 1, "queued": 2, "limit": 1}
        assert client.get("/progress/q-2").json()["message"].startswith("queued — #1")
        assert client.get("/progress/q-3").json()["message"].startswith("queued — #2")

        # Abort the last one while it waits; it must never start.
        assert client.post("/abort/q-3").json() == {"aborted": True}
        assert client.get("/health").json()["builds"]["queued"] == 1

        gate.set()
        deadline = time.time() + 10
        while client.get("/result/q-2").json()["stage"] != "done":
            assert time.time() < deadline, "queued build never ran"
            time.sleep(0.05)
        assert client.get("/result/q-1").json()["stage"] == "done"
        assert client.get("/progress/q-3").json()["stage"] == "aborted"
        assert client.get("/health").json()["builds"] == {
            "running": 0, "queued": 0, "limit": 1}
