"""Regressions from the first real end-to-end build.

Both bugs here were introduced by moving the session workspace out of the
source tree (PALETTE_WORKSPACE). Neither showed up in unit tests, a health
check, or a mocked build — only in an actual deck render:

  1. The node renderer located `node_modules` by walking *up from the session
     directory*. That worked only while the workspace lived inside the
     checkout. Moved to a state dir, every render failed with "pptxgenjs not
     installed" — or, worse, silently picked up an unrelated node_modules
     from the user's home directory.

  2. render.py signals missing prerequisites with `SystemExit`, which is a
     BaseException. `except Exception` did not catch it, so it escaped the
     background build task and tore down the uvicorn event loop: one bad
     build killed the server for every session.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.contract

server = pytest.importorskip("app", reason="server deps not installed")
render = pytest.importorskip("render")
config = pytest.importorskip("config")


class TestRendererFindsItsAssets:
    """node must run from the checkout, wherever the workspace lives."""

    def test_repo_root_wins_over_the_session_directory(self, tmp_path: Path) -> None:
        session = tmp_path / "state" / "palette" / "workspace" / "skill-abc123"
        session.mkdir(parents=True)
        assert render._node_module_dir(session) == config.ROOT

    def test_ignores_an_unrelated_node_modules_above_the_workspace(self, tmp_path: Path) -> None:
        """A stray node_modules in $HOME must not hijack the render."""
        decoy = tmp_path / "node_modules" / "pptxgenjs"
        decoy.mkdir(parents=True)
        session = tmp_path / "workspace" / "skill-abc123"
        session.mkdir(parents=True)
        assert render._node_module_dir(session) == config.ROOT

    def test_chosen_directory_has_what_the_slide_js_needs(self) -> None:
        """Slide JS references icons by path relative to cwd, so both must be there."""
        chosen = render._node_module_dir(Path("/tmp/anywhere"))
        assert (chosen / "icons" / "carbon").is_dir(), "icon paths in slide JS would not resolve"
        assert (chosen / "assets").is_dir(), "asset paths in slide JS would not resolve"


class TestOneBadBuildCannotKillTheServer:
    """A pipeline SystemExit must become a failed build, not a dead process."""

    @pytest.fixture
    def session(self, tmp_path: Path):
        from session import SlideSession

        return SlideSession.create(tmp_path, "regression-thread")

    def _request(self):
        return server.BuildReq(plan="# Plan", thread_id="regression-thread")

    def test_system_exit_is_recorded_as_a_failed_build(self, session, monkeypatch) -> None:
        def explode(*args, **kwargs):
            raise SystemExit("pptxgenjs not installed under /nowhere/node_modules/")

        monkeypatch.setattr(server, "generate_deck", explode)

        with pytest.raises(SystemExit):
            asyncio.run(server._run_build(self._request(), session))

        assert session.building is False, "a failed build must release the session"
        assert session.progress["stage"] == "error"
        assert "pptxgenjs" in session.last_error
        assert "pptxgenjs" in session.progress["message"], "the caller must see the real cause"

    def test_background_task_swallows_system_exit(self, session, monkeypatch) -> None:
        """The task wrapper is the last line of defence for the event loop."""

        def explode(*args, **kwargs):
            raise SystemExit("node not found on PATH")

        monkeypatch.setattr(server, "generate_deck", explode)

        async def drive():
            response = await server.build_async(self._request())
            # Let the spawned task run to completion; nothing may escape it.
            await asyncio.sleep(0.1)
            return response

        response = asyncio.run(drive())
        assert response["started"] is True

    def test_ordinary_exceptions_still_recorded(self, session, monkeypatch) -> None:
        def explode(*args, **kwargs):
            raise RuntimeError("designer output did not parse as a deck JSON")

        monkeypatch.setattr(server, "generate_deck", explode)

        with pytest.raises(RuntimeError):
            asyncio.run(server._run_build(self._request(), session))
        assert session.progress["stage"] == "error"
        assert "designer output" in session.last_error

    def test_keyboard_interrupt_is_not_swallowed(self, session, monkeypatch) -> None:
        """Real shutdown signals must still propagate."""

        def interrupt(*args, **kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(server, "generate_deck", interrupt)

        with pytest.raises(KeyboardInterrupt):
            asyncio.run(server._run_build(self._request(), session))
