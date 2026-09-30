"""The skill's remote mode ($PALETTE_URL), against a fake Palette server.

Remote mode sends the same commands to a running Palette web app instead of a
local checkout. These tests stand up a stdlib HTTP server that speaks the
app's async endpoints and drive `deck.py` as an agent would — with
`PALETTE_URL` set and **no** `PALETTE_HOME`, which is the point of the mode.
No network, no models, no rendering.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DECK_PY = REPO_ROOT / "skills" / "palette" / "scripts" / "deck.py"

PLAN = "# Deck\n\n## Cover\n- Title\n"
PPTX = b"PK" + b"\0" * 30_000          # over deck.py's 20KB "real deck" floor
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 100


class FakePalette(BaseHTTPRequestHandler):
    """Just enough of app.py's API. Each build finishes on its third poll."""

    sessions: dict[str, dict] = {}
    forget_builds = False       # simulate a server restart mid-build
    fail_builds = False
    last_upload = b""

    def log_message(self, *args):  # keep test output quiet
        pass

    def _send(self, code: int, payload, content_type: str = "application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))

    def do_POST(self):  # noqa: N802
        raw = self._body()
        if self.path == "/draft_async":
            FakePalette.last_upload = raw
            thread = raw.split(b'name="thread_id"\r\n\r\n', 1)[1].split(b"\r\n", 1)[0].decode()
            self.sessions[thread] = {"plan": PLAN}
            return self._send(200, {"started": True, "thread_id": thread})
        body = json.loads(raw)
        if self.path == "/edit_plan_async":
            self.sessions[body["thread_id"]] = {"plan": body["plan"] + "\n<!-- " + body["instruction"] + " -->\n"}
            return self._send(200, {"started": True})
        if self.path == "/build_async":
            self.sessions[body["thread_id"]] = {"polls": 0}
            return self._send(200, {"started": True})
        self._send(404, {"error": "no route"})

    def do_GET(self):  # noqa: N802
        parts = self.path.strip("/").split("/")
        session = self.sessions.get(parts[1]) if len(parts) > 1 else None
        if session is None or (self.forget_builds and parts[0] == "result"):
            return self._send(404, {"error": "unknown thread_id"})
        if parts[0] == "draft_result":
            return self._send(200, {"stage": "done", "plan": session["plan"], "error": None})
        if parts[0] == "result":
            session["polls"] += 1
            if session["polls"] < 3:
                return self._send(200, {"stage": "running", "result": None, "error": None})
            if self.fail_builds:
                return self._send(200, {"stage": "error", "result": None, "error": "coder exploded"})
            return self._send(200, {"stage": "done", "error": None,
                                    "result": {"slide_count": 2, "title": "Deck"}})
        if parts[0] == "progress":
            return self._send(200, {"stage": "build", "message": "coding slides"})
        if parts[0] == "download":
            return self._send(200, PPTX, "application/octet-stream")
        if parts[0] == "preview":
            return self._send(200, PNG, "image/png")
        self._send(404, {"error": "no route"})


@pytest.fixture
def server():
    FakePalette.sessions = {}
    FakePalette.forget_builds = FakePalette.fail_builds = False
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakePalette)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def deck(tmp_path: Path, url: str, *args: str) -> tuple[int, dict]:
    """Run deck.py like an agent: PALETTE_URL set, PALETTE_HOME deliberately absent."""
    env = {k: v for k, v in os.environ.items() if k not in ("PALETTE_HOME", "PALETTE_TRACE")}
    env["PALETTE_URL"] = url
    done = subprocess.run([sys.executable, str(DECK_PY), *args], cwd=tmp_path, env=env,
                          capture_output=True, text=True, timeout=60)
    return done.returncode, json.loads(done.stdout)


def test_plan_comes_back_in_one_call_without_a_checkout(tmp_path, server):
    code, out = deck(tmp_path, server, "plan", "--request", "3 slides on RAG",
                     "--context", "notes the user pasted", "--out", "plan.md")
    assert code == 0 and out["state"] == "done" and out["text"] == PLAN
    assert (tmp_path / "plan.md").read_text() == PLAN
    # pasted material travels as an uploaded source, like palette.py's temp file
    assert b'filename="context.txt"' in FakePalette.last_upload
    assert b"notes the user pasted" in FakePalette.last_upload


def test_edit_revises_the_plan_in_place(tmp_path, server):
    (tmp_path / "plan.md").write_text(PLAN)
    code, out = deck(tmp_path, server, "edit", "--instruction", "make it casual", "--plan", "plan.md")
    assert code == 0 and out["state"] == "done"
    assert "make it casual" in (tmp_path / "plan.md").read_text()


def test_build_is_polled_then_downloaded_and_verified_from_disk(tmp_path, server):
    (tmp_path / "plan.md").write_text(PLAN)
    code, started = deck(tmp_path, server, "start", "--plan", "plan.md", "--out-dir", "deck")
    assert code == 0 and started["state"] == "running" and started["remote"] == server

    code, first = deck(tmp_path, server, "status", "--out-dir", "deck", "--hold-seconds", "0")
    assert first["state"] == "running" and first["progress"] == "coding slides"

    code, final = deck(tmp_path, server, "status", "--out-dir", "deck", "--hold-seconds", "30")
    assert code == 0 and final["state"] == "done" and final["verified"] is True
    assert (tmp_path / "deck" / "deck.pptx").read_bytes() == PPTX
    assert final["slide_previews"] == 2

    # find sees the finished remote build like a local one
    code, found = deck(tmp_path, server, "find", "--root", ".")
    assert any(f["kind"] == "build" and f["state"] == "done" for f in found["found"])


def test_a_second_start_collects_the_running_build(tmp_path, server):
    (tmp_path / "plan.md").write_text(PLAN)
    deck(tmp_path, server, "start", "--plan", "plan.md", "--out-dir", "deck")
    code, again = deck(tmp_path, server, "start", "--plan", "plan.md", "--out-dir", "deck")
    assert "already building" in again["note"]
    assert len(FakePalette.sessions) == 1


def test_a_server_side_failure_surfaces_its_reason(tmp_path, server):
    FakePalette.fail_builds = True
    (tmp_path / "plan.md").write_text(PLAN)
    deck(tmp_path, server, "start", "--plan", "plan.md", "--out-dir", "deck")
    code, final = deck(tmp_path, server, "status", "--out-dir", "deck", "--hold-seconds", "30")
    assert code == 1 and final["state"] == "error" and "coder exploded" in final["log_tail"]


def test_a_restarted_server_is_reported_not_polled_forever(tmp_path, server):
    (tmp_path / "plan.md").write_text(PLAN)
    deck(tmp_path, server, "start", "--plan", "plan.md", "--out-dir", "deck")
    FakePalette.forget_builds = True
    code, final = deck(tmp_path, server, "status", "--out-dir", "deck", "--hold-seconds", "5")
    assert code == 1 and final["state"] == "error"
    assert "no longer knows this build" in final["log_tail"]


def test_an_unreachable_server_names_the_url(tmp_path):
    code, out = deck(tmp_path, "http://127.0.0.1:9", "plan", "--request", "x", "--out", "plan.md")
    assert code == 1 and out["state"] == "error"
    assert "cannot reach the Palette server at http://127.0.0.1:9" in out["error"]
