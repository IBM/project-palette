"""PaletteClient behaviour, against a mock server.

The build lifecycle is the part worth testing hard: it is what agent hosts
depend on, and the bounded-wait contract (return by ``max_seconds`` even when
the build is still running) is easy to break by accident.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from palette_skill import contract  # noqa: E402
from palette_skill.client import (  # noqa: E402
    BuildOutcome,
    PaletteClient,
    PaletteError,
    PaletteHTTPError,
    PaletteTimeout,
    PaletteUnavailable,
    Progress,
    new_thread_id,
)

BASE = "http://palette.test"

BUILD_PAYLOAD = {
    "slide_count": 12,
    "title": "Vector Databases",
    "elapsed": 143.2,
    "lint": [],
    "unrepaired": [3],
    "geometry": {"defects": 4, "repaired": 3},
    "retries": {"5": 1},
}


class FakePalette:
    """A scriptable stand-in for the server.

    ``background`` controls whether /build_async and /result are advertised, so
    the same tests cover old and new deployments.
    """

    def __init__(self, *, background: bool = True, stages: list[dict] | None = None) -> None:
        self.background = background
        self.stages = stages or [{"stage": "done", "message": "deck ready", "current": 0, "total": 0}]
        self.calls: list[tuple[str, str]] = []
        self.started: list[dict] = []
        self._poll = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _paths(self) -> list[str]:
        paths = [e.path for e in contract.BASELINE]
        if self.background:
            paths += [contract.BUILD_ASYNC.path, contract.RESULT.path]
        return paths

    def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))

        if path == "/openapi.json":
            return httpx.Response(200, json={"paths": {p: {} for p in self._paths()}})
        if path == "/health":
            return httpx.Response(200, json={"status": "ok", "roster": {}, "icons": 20, "rits_key_set": True})
        if path == "/examples":
            return httpx.Response(200, json={"examples": [{"file": "rag.md", "label": "RAG"}]})
        if path.startswith("/example/"):
            return httpx.Response(200, json={"name": path.rsplit("/", 1)[-1], "content": "# Plan\n"})
        if path == "/draft":
            return httpx.Response(200, json={"plan": "# Drafted plan\n", "sources": []})

        if path == "/build":
            return httpx.Response(200, json=BUILD_PAYLOAD)
        if path == "/build_async":
            if not self.background:
                return httpx.Response(404, json={"error": "not found"})
            self.started.append(json.loads(request.content))
            return httpx.Response(200, json={"started": True, "thread_id": self.started[-1]["thread_id"]})
        if path.startswith("/result/"):
            if not self.background:
                return httpx.Response(404, json={"error": "not found"})
            # Mirrors app.py: terminal only once the build actually ended.
            final = self.stages[-1]
            if final["stage"] == "error":
                return httpx.Response(200, json={"stage": "error", "result": None, "error": final["message"]})
            if final["stage"] == "done":
                return httpx.Response(200, json={"stage": "done", "result": BUILD_PAYLOAD, "error": None})
            return httpx.Response(200, json={"stage": "running", "result": None, "error": None})
        if path.startswith("/progress/"):
            stage = self.stages[min(self._poll, len(self.stages) - 1)]
            self._poll += 1
            return httpx.Response(200, json=stage)
        if path.startswith("/deck/"):
            return httpx.Response(200, json={"slide_count": 2, "title": "Vector Databases", "building": False})

        if path == "/edit":
            return httpx.Response(200, json={"slide_count": 12, "edited": 5})
        if path.startswith("/retry/"):
            return httpx.Response(200, json={"slide_count": 12, "retried": 5, "geometry": {}})
        if path.startswith("/download/"):
            return httpx.Response(200, content=b"PK\x03\x04fake-pptx")
        if path.startswith("/preview/"):
            return httpx.Response(200, content=b"\x89PNG\r\n\x1a\n")
        if path.startswith("/clear/"):
            return httpx.Response(200, json={"cleared": True})
        if path.startswith("/abort/"):
            return httpx.Response(200, json={"aborted": True})
        return httpx.Response(404, json={"error": f"no route {path}"})


def client(server: FakePalette) -> PaletteClient:
    return PaletteClient(BASE, transport=server.transport())


# -- basics ----------------------------------------------------------------


class TestBasics:
    def test_base_url_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(contract.BASE_URL_ENV, "http://from-env:9/")
        assert PaletteClient().base_url == "http://from-env:9"
        assert PaletteClient("http://explicit:1/").base_url == "http://explicit:1"

    def test_falls_back_to_contract_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(contract.BASE_URL_ENV, raising=False)
        assert PaletteClient().base_url == contract.DEFAULT_BASE_URL

    def test_token_is_sent_as_bearer(self) -> None:
        seen: list[str | None] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("authorization"))
            return httpx.Response(200, json={"status": "ok"})

        PaletteClient(BASE, token="s3cret", transport=httpx.MockTransport(handle)).health()
        assert seen == ["Bearer s3cret"]

    def test_thread_ids_are_unique_and_prefixed(self) -> None:
        ids = {new_thread_id() for _ in range(50)}
        assert len(ids) == 50
        assert all(i.startswith("skill-") for i in ids)

    def test_health_and_examples(self) -> None:
        pal = client(FakePalette())
        assert pal.health()["rits_key_set"] is True
        assert pal.examples() == [{"file": "rag.md", "label": "RAG"}]
        assert pal.example("rag.md") == "# Plan\n"


# -- capability detection --------------------------------------------------


class TestCapabilities:
    def test_detects_background_support(self) -> None:
        assert client(FakePalette(background=True)).supports("build_async") is True
        assert client(FakePalette(background=False)).supports("build_async") is False

    def test_baseline_routes_need_no_probe(self) -> None:
        server = FakePalette()
        assert client(server).supports("build") is True
        assert not server.calls, "baseline routes must not trigger an openapi fetch"

    def test_capabilities_are_cached(self) -> None:
        server = FakePalette()
        pal = client(server)
        pal.capabilities()
        pal.capabilities()
        assert server.calls.count(("GET", "/openapi.json")) == 1

    def test_unreadable_openapi_assumes_baseline_only(self) -> None:
        pal = PaletteClient(
            BASE,
            transport=httpx.MockTransport(lambda r: httpx.Response(404, text="nope")),
        )
        assert pal.capabilities() == frozenset(e.path for e in contract.BASELINE)
        assert pal.supports("build_async") is False


# -- the build lifecycle ---------------------------------------------------


class TestBuildLifecycle:
    def test_start_build_returns_a_thread_id(self) -> None:
        server = FakePalette()
        tid = client(server).start_build("# Plan")
        assert tid.startswith("skill-")
        assert server.started[0]["thread_id"] == tid

    def test_start_build_is_refused_without_the_route(self) -> None:
        with pytest.raises(PaletteError, match="no /build_async route"):
            client(FakePalette(background=False)).start_build("# Plan")

    def test_wait_returns_at_the_bound_while_still_running(self) -> None:
        """The property agent hosts rely on: never block past max_seconds."""
        running = [{"stage": "build", "message": "coding slides", "current": 3, "total": 12}] * 50
        pal = client(FakePalette(stages=running))
        snapshot = pal.wait("tid", max_seconds=0.3, poll_interval=0.05)
        assert snapshot.terminal is False
        assert snapshot.stage == "build"

    def test_wait_returns_early_when_the_build_ends(self) -> None:
        stages = [
            {"stage": "build", "message": "designer", "current": 0, "total": 0},
            {"stage": "build", "message": "coding", "current": 6, "total": 12},
            {"stage": "done", "message": "deck ready", "current": 0, "total": 0},
        ]
        pal = client(FakePalette(stages=stages))
        snapshot = pal.wait("tid", max_seconds=10, poll_interval=0.01)
        assert snapshot.terminal and not snapshot.failed

    def test_wait_reports_each_change_once(self) -> None:
        stages = [
            {"stage": "build", "message": "designer", "current": 0, "total": 0},
            {"stage": "build", "message": "designer", "current": 0, "total": 0},
            {"stage": "build", "message": "coding", "current": 6, "total": 12},
            {"stage": "done", "message": "deck ready", "current": 0, "total": 0},
        ]
        seen: list[str] = []
        client(FakePalette(stages=stages)).wait(
            "tid", max_seconds=10, poll_interval=0.01, on_progress=lambda p: seen.append(p.message)
        )
        assert seen == ["designer", "coding", "deck ready"]

    def test_result_raises_while_still_running(self) -> None:
        pal = client(FakePalette(stages=[{"stage": "build", "message": "", "current": 0, "total": 0}]))
        with pytest.raises(PaletteError, match="not finished"):
            pal.result("tid")

    def test_result_surfaces_a_server_side_failure(self) -> None:
        stages = [{"stage": "error", "message": "designer returned no brief", "current": 0, "total": 0}]
        with pytest.raises(PaletteError, match="designer returned no brief"):
            client(FakePalette(stages=stages)).result("tid")

    def test_build_and_wait_uses_the_background_route(self) -> None:
        server = FakePalette(stages=[{"stage": "done", "message": "ready", "current": 0, "total": 0}])
        outcome = client(server).build_and_wait("# Plan")
        assert ("POST", "/build_async") in server.calls
        assert ("POST", "/build") not in server.calls
        assert outcome.slide_count == 12

    def test_build_and_wait_falls_back_to_blocking(self) -> None:
        server = FakePalette(background=False)
        outcome = client(server).build_and_wait("# Plan")
        assert ("POST", "/build") in server.calls
        assert outcome.title == "Vector Databases"

    def test_build_and_wait_raises_on_a_failed_build(self) -> None:
        stages = [{"stage": "error", "message": "render failed", "current": 0, "total": 0}]
        with pytest.raises(PaletteError, match="render failed"):
            client(FakePalette(stages=stages)).build_and_wait("# Plan")

    def test_empty_plan_is_rejected_before_any_request(self) -> None:
        server = FakePalette()
        with pytest.raises(PaletteError, match="plan is empty"):
            client(server).start_build("   ")
        assert ("POST", "/build_async") not in server.calls


class TestOutcomeAndProgress:
    def test_outcome_keeps_the_raw_payload(self) -> None:
        extra = {**BUILD_PAYLOAD, "future_field": 42}
        outcome = BuildOutcome.from_payload("tid", extra)
        assert outcome.raw["future_field"] == 42
        assert outcome.unrepaired == (3,)

    def test_outcome_renders_for_a_human(self) -> None:
        assert str(BuildOutcome.from_payload("tid", BUILD_PAYLOAD)) == "Vector Databases — 12 slides in 143s"

    @pytest.mark.parametrize(
        ("stage", "terminal", "failed"),
        [("idle", False, False), ("build", False, False), ("done", True, False),
         ("error", True, True), ("aborted", True, True)],
    )
    def test_stage_classification(self, stage: str, terminal: bool, failed: bool) -> None:
        snapshot = Progress(stage=stage)
        assert (snapshot.terminal, snapshot.failed) == (terminal, failed)

    def test_progress_renders_the_counter(self) -> None:
        assert str(Progress("build", "coding slides", 7, 12)) == "build [7/12] — coding slides"


# -- drafting, refinement, artefacts ---------------------------------------


class TestDraftAndRefine:
    def test_draft_returns_the_plan(self) -> None:
        assert client(FakePalette()).draft("a deck about RAG") == "# Drafted plan\n"

    def test_draft_rejects_an_empty_request(self) -> None:
        with pytest.raises(PaletteError, match="request is empty"):
            client(FakePalette()).draft("  ")

    def test_draft_rejects_a_missing_reference_file(self, tmp_path: Path) -> None:
        with pytest.raises(PaletteError, match="reference file not found"):
            client(FakePalette()).draft("deck", files=[tmp_path / "nope.pdf"])

    def test_draft_uploads_reference_files(self, tmp_path: Path) -> None:
        doc = tmp_path / "notes.md"
        doc.write_text("# Notes")
        bodies: list[bytes] = []

        def handle(request: httpx.Request) -> httpx.Response:
            bodies.append(request.content)
            return httpx.Response(200, json={"plan": "# Plan", "sources": ["notes.md"]})

        PaletteClient(BASE, transport=httpx.MockTransport(handle)).draft("deck", files=[doc])
        assert b"notes.md" in bodies[0] and b"# Notes" in bodies[0]

    def test_edit_requires_an_instruction(self) -> None:
        with pytest.raises(PaletteError, match="instruction is empty"):
            client(FakePalette()).edit("tid", 5, "")

    def test_edit_and_retry(self) -> None:
        pal = client(FakePalette())
        assert pal.edit("tid", 5, "make it a table")["edited"] == 5
        assert pal.retry("tid", 5)["retried"] == 5


class TestArtefacts:
    def test_download_writes_the_file(self, tmp_path: Path) -> None:
        path = client(FakePalette()).download("tid", tmp_path / "out" / "deck.pptx")
        assert path.read_bytes().startswith(b"PK")

    def test_download_into_a_directory_picks_a_name(self, tmp_path: Path) -> None:
        assert client(FakePalette()).download("tid", tmp_path).name == "deck.pptx"

    def test_previews_saves_every_slide(self, tmp_path: Path) -> None:
        paths = client(FakePalette()).previews("tid", tmp_path)
        assert [p.name for p in paths] == ["slide-01.png", "slide-02.png"]
        assert all(p.read_bytes().startswith(b"\x89PNG") for p in paths)


# -- failure translation ---------------------------------------------------


class TestErrors:
    def test_connection_failure_names_the_url(self) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(PaletteUnavailable, match=BASE):
            PaletteClient(BASE, transport=httpx.MockTransport(refuse)).health()

    def test_timeout_points_at_the_non_blocking_path(self) -> None:
        def slow(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow")

        with pytest.raises(PaletteTimeout, match="start_build"):
            PaletteClient(BASE, transport=httpx.MockTransport(slow)).health()

    def test_http_error_carries_the_server_message(self) -> None:
        def conflict(request: httpx.Request) -> httpx.Response:
            return httpx.Response(409, json={"error": "a build is already running"})

        with pytest.raises(PaletteHTTPError) as caught:
            PaletteClient(BASE, transport=httpx.MockTransport(conflict)).health()
        assert caught.value.status_code == 409
        assert caught.value.message == "a build is already running"

    def test_non_json_error_body_still_surfaces(self) -> None:
        def broken(request: httpx.Request) -> httpx.Response:
            return httpx.Response(502, text="<html>bad gateway</html>")

        with pytest.raises(PaletteHTTPError, match="bad gateway"):
            PaletteClient(BASE, transport=httpx.MockTransport(broken)).health()

    def test_missing_path_parameter_is_caught_client_side(self) -> None:
        pal = client(FakePalette())
        with pytest.raises(PaletteError, match="missing path parameter"):
            pal._url(contract.PROGRESS)
