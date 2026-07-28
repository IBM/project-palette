"""The local-service supervisor.

Nothing here starts a real server — process spawning, container runtimes, and
launchd are stubbed. What is tested is the logic that decides *what* to run,
*where* state goes, and *what to tell the user* when something is missing,
because those are the parts that produce confusing failures at 9pm.
"""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from palette_skill import service  # noqa: E402
from palette_skill.client import PaletteError  # noqa: E402
from palette_skill.service import ServiceError  # noqa: E402


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A directory that looks enough like a Palette checkout."""
    home = tmp_path / "palette"
    home.mkdir()
    for marker in service.CHECKOUT_MARKERS:
        (home / marker).write_text("# stub\n")
    return home


@pytest.fixture
def cfg(tmp_path: Path, checkout: Path) -> service.ServiceConfig:
    return service.resolve_config(
        home=checkout,
        port=18999,
        env_file=tmp_path / "env",
        state_dir=tmp_path / "state",
    )


# -- env file --------------------------------------------------------------


class TestEnvFile:
    def test_missing_file_is_not_an_error(self, tmp_path: Path) -> None:
        assert service.load_env_file(tmp_path / "nope") == {}

    def test_parses_comments_exports_and_quotes(self, tmp_path: Path) -> None:
        path = tmp_path / "env"
        path.write_text(
            "# a comment\n"
            "\n"
            "RITS_API_KEY=abc123\n"
            "export PALETTE_PORT=18814\n"
            '  PALETTE_HOME = "/tmp/palette"  \n'
            "QUOTED='single'\n"
            "NOT_A_PAIR\n"
        )
        assert service.load_env_file(path) == {
            "RITS_API_KEY": "abc123",
            "PALETTE_PORT": "18814",
            "PALETTE_HOME": "/tmp/palette",
            "QUOTED": "single",
        }

    def test_values_containing_equals_survive(self, tmp_path: Path) -> None:
        path = tmp_path / "env"
        path.write_text("RITS_API_KEY=a=b=c\n")
        assert service.load_env_file(path)["RITS_API_KEY"] == "a=b=c"

    def test_init_writes_a_private_template(self, cfg: service.ServiceConfig) -> None:
        result = service.init_env_file(cfg)
        assert result["created"] is True
        assert cfg.env_file.stat().st_mode & 0o777 == 0o600, "the file holds a secret"
        body = cfg.env_file.read_text()
        assert "RITS_API_KEY=" in body and str(cfg.home) in body

    def test_init_will_not_clobber_a_real_key(self, cfg: service.ServiceConfig) -> None:
        cfg.env_file.parent.mkdir(parents=True, exist_ok=True)
        cfg.env_file.write_text("RITS_API_KEY=precious\n")
        assert service.init_env_file(cfg)["created"] is False
        assert "precious" in cfg.env_file.read_text()
        service.init_env_file(cfg, force=True)
        assert "precious" not in cfg.env_file.read_text()


# -- configuration ---------------------------------------------------------


class TestConfig:
    def test_env_file_supplies_port_and_home(self, tmp_path: Path, checkout: Path) -> None:
        env = tmp_path / "env"
        env.write_text(f"PALETTE_PORT=19001\nPALETTE_HOME={checkout}\n")
        cfg = service.resolve_config(env_file=env, state_dir=tmp_path / "state")
        assert cfg.port == 19001
        assert cfg.url == "http://127.0.0.1:19001"
        assert cfg.home == checkout

    def test_explicit_arguments_beat_the_env_file(self, tmp_path: Path, checkout: Path) -> None:
        env = tmp_path / "env"
        env.write_text("PALETTE_PORT=19001\n")
        assert service.resolve_config(port=19002, env_file=env, home=checkout).port == 19002

    def test_workspace_defaults_outside_the_checkout(self, cfg: service.ServiceConfig, checkout: Path) -> None:
        """A service must not write session state into its own source tree."""
        assert checkout not in cfg.workspace.parents
        assert cfg.workspace.is_relative_to(cfg.state_dir)

    def test_child_env_carries_workspace_and_port(self, cfg: service.ServiceConfig) -> None:
        env = cfg.child_env()
        assert env["PALETTE_WORKSPACE"] == str(cfg.workspace)
        assert env["PORT"] == str(cfg.port)

    def test_child_env_includes_the_secret(self, tmp_path: Path, checkout: Path) -> None:
        env_file = tmp_path / "env"
        env_file.write_text("RITS_API_KEY=from-file\n")
        cfg = service.resolve_config(home=checkout, env_file=env_file, state_dir=tmp_path / "s")
        assert cfg.child_env()["RITS_API_KEY"] == "from-file"

    def test_home_discovery_rejects_a_non_checkout(self, tmp_path: Path) -> None:
        assert service.find_home(tmp_path / "empty") != tmp_path / "empty"

    def test_home_discovery_accepts_a_checkout(self, checkout: Path) -> None:
        assert service.find_home(checkout) == checkout

    def test_this_repo_is_discoverable(self) -> None:
        """The package's own parent is a checkout, so editable installs work."""
        assert service.find_home() == REPO_ROOT


# -- mode selection --------------------------------------------------------


class TestModeSelection:
    def test_container_preferred_when_an_image_exists(
        self, cfg: service.ServiceConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(service, "image_available", lambda _cfg: True)
        assert service.detect_mode(cfg) == "container"

    def test_process_when_no_image(
        self, cfg: service.ServiceConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(service, "image_available", lambda _cfg: False)
        assert service.detect_mode(cfg) == "process"

    def test_container_runtime_prefers_docker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(service.shutil, "which", lambda name: f"/usr/bin/{name}")
        assert service.container_runtime() == "docker"

    def test_container_runtime_falls_back_to_podman(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            service.shutil, "which", lambda name: "/usr/bin/podman" if name == "podman" else None
        )
        assert service.container_runtime() == "podman"

    def test_no_runtime_reports_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(service.shutil, "which", lambda _name: None)
        assert service.container_runtime() is None


# -- failure messages ------------------------------------------------------


class TestActionableFailures:
    def test_missing_checkout_names_the_env_file(self, tmp_path: Path) -> None:
        cfg = service.resolve_config(home=tmp_path / "nowhere", env_file=tmp_path / "env",
                                     state_dir=tmp_path / "state")
        object.__setattr__(cfg, "home", None)
        with pytest.raises(ServiceError, match="no Palette checkout found"):
            service.start_process(cfg)

    def test_container_start_without_an_image_says_how_to_build(
        self, cfg: service.ServiceConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(service, "container_runtime", lambda: "docker")
        monkeypatch.setattr(service, "image_available", lambda _cfg: False)
        with pytest.raises(ServiceError, match="make docker-build"):
            service.start_container(cfg)

    def test_container_start_without_a_runtime_says_so(
        self, cfg: service.ServiceConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(service, "container_runtime", lambda: None)
        with pytest.raises(ServiceError, match="docker or podman"):
            service.start_container(cfg)

    def test_service_errors_are_palette_errors(self) -> None:
        """One `except PaletteError` must cover the client and the supervisor."""
        assert issubclass(ServiceError, PaletteError)

    def test_ensure_reports_the_log_tail_when_health_never_arrives(
        self, cfg: service.ServiceConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        cfg.log_file.write_text("Traceback...\nModuleNotFoundError: No module named 'fastapi'\n")
        monkeypatch.setattr(service, "probe", lambda _cfg, timeout=3.0: None)
        monkeypatch.setattr(service, "start", lambda _cfg, _mode: {"mode": "process"})
        monkeypatch.setattr(service, "wait_until_healthy", lambda _cfg, timeout=90.0: None)
        with pytest.raises(ServiceError, match="ModuleNotFoundError"):
            service.ensure(cfg, timeout=0.1)


# -- doctor ----------------------------------------------------------------


class TestDoctor:
    def test_reports_per_mode_blockers(
        self, cfg: service.ServiceConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(service.shutil, "which", lambda _name: None)
        monkeypatch.setattr(service, "container_runtime", lambda: None)
        monkeypatch.setattr(service, "_soffice", lambda: None)
        report = service.doctor(cfg)
        assert report["modes"]["process"]["ready"] is False
        assert "node" in report["modes"]["process"]["blockers"]
        assert "container_runtime" in report["modes"]["container"]["blockers"]

    def test_flags_a_missing_key_separately_from_mode_readiness(
        self, cfg: service.ServiceConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The server starts fine without a key and only fails at model time."""
        monkeypatch.delenv("RITS_API_KEY", raising=False)
        assert service.doctor(cfg)["can_build"] is False

    def test_pptxgenjs_check_points_at_the_checkout(self, cfg: service.ServiceConfig) -> None:
        detail = service.doctor(cfg)["checks"]["pptxgenjs"]["detail"]
        assert "npm install" in detail and str(cfg.home) in detail


# -- launchd ---------------------------------------------------------------


class TestLaunchd:
    def test_plist_is_valid_and_self_describing(self, cfg: service.ServiceConfig) -> None:
        parsed = plistlib.loads(plistlib.dumps(service.launchd_plist(cfg)))
        assert parsed["Label"] == service.LAUNCHD_LABEL
        assert parsed["RunAtLoad"] is True and parsed["KeepAlive"] is True
        assert str(cfg.port) in parsed["ProgramArguments"]
        assert parsed["StandardOutPath"] == str(cfg.log_file)

    def test_plist_never_embeds_the_secret(self, tmp_path: Path, checkout: Path) -> None:
        """The key lives in one 600-mode file, not in a world-readable plist."""
        env_file = tmp_path / "env"
        env_file.write_text("RITS_API_KEY=super-secret-value\n")
        cfg = service.resolve_config(home=checkout, env_file=env_file, state_dir=tmp_path / "state")
        rendered = plistlib.dumps(service.launchd_plist(cfg)).decode()
        assert "super-secret-value" not in rendered
        assert str(env_file) in rendered, "but it must point at where the key lives"

    def test_non_darwin_is_refused_with_a_pointer(
        self, cfg: service.ServiceConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(service.platform, "system", lambda: "Linux")
        with pytest.raises(ServiceError, match="process or container"):
            service.install_launchd(cfg)


# -- process lifecycle -----------------------------------------------------


class TestProcessLifecycle:
    def test_start_spawns_detached_and_records_the_pid(
        self, cfg: service.ServiceConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        class FakeProcess:
            pid = 4242

        def fake_popen(command, **kwargs):
            captured["command"] = command
            captured["kwargs"] = kwargs
            return FakeProcess()

        monkeypatch.setattr(service.subprocess, "Popen", fake_popen)
        result = service.start_process(cfg)

        assert result == {"started": True, "pid": 4242, "mode": "process", "log": str(cfg.log_file)}
        assert cfg.pid_file.read_text() == "4242"
        assert captured["command"][1:] == ["app.py", "--port", str(cfg.port)]
        assert captured["kwargs"]["start_new_session"] is True, "must outlive the calling shell"
        assert captured["kwargs"]["stdin"] is subprocess.DEVNULL, "must not hold the caller's stdin"
        assert captured["kwargs"]["cwd"] == str(cfg.home)

    def test_start_is_idempotent_while_alive(
        self, cfg: service.ServiceConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        cfg.pid_file.write_text("4242")
        monkeypatch.setattr(service, "_pid_alive", lambda _pid: True)
        assert service.start_process(cfg)["reason"] == "already running"

    def test_stale_pid_file_is_cleared(
        self, cfg: service.ServiceConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        cfg.pid_file.write_text("999999")
        monkeypatch.setattr(service, "_pid_alive", lambda _pid: False)
        assert service.running_pid(cfg) is None
        assert not cfg.pid_file.exists()

    def test_garbage_pid_file_does_not_raise(self, cfg: service.ServiceConfig) -> None:
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        cfg.pid_file.write_text("not-a-number")
        assert service.running_pid(cfg) is None

    def test_ensure_short_circuits_when_already_healthy(
        self, cfg: service.ServiceConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(service, "probe", lambda _cfg, timeout=3.0: {"status": "ok"})
        monkeypatch.setattr(
            service, "start", lambda *a, **k: pytest.fail("must not start an already-running service")
        )
        assert service.ensure(cfg)["action"] == "already-running"


# -- status rendering ------------------------------------------------------


class TestStatus:
    def test_down_status_tells_you_what_to_run(self) -> None:
        rendered = service.format_status({"running": False, "url": "http://127.0.0.1:18814"})
        assert "NOT running" in rendered and "serve start" in rendered

    def test_up_status_warns_about_a_missing_key(self) -> None:
        rendered = service.format_status(
            {"running": True, "url": "http://x", "mode": "process", "rits_key_set": False}
        )
        assert "RITS_API_KEY not set" in rendered

    def test_up_status_is_quiet_when_healthy(self) -> None:
        rendered = service.format_status(
            {"running": True, "url": "http://x", "mode": "container", "rits_key_set": True}
        )
        assert "[!]" not in rendered and "container" in rendered


# -- packaging -------------------------------------------------------------


class TestUninstallSafety:
    """`--skills-root` takes an arbitrary path, so a typo must not delete data."""

    def test_refuses_a_directory_without_a_manifest(self, tmp_path: Path, capsys) -> None:
        from palette_skill import install

        victim = tmp_path / "palette"
        victim.mkdir()
        (victim / "notes.txt").write_text("not ours")

        assert install.uninstall(victim) == 1
        assert (victim / "notes.txt").is_file(), "a directory we did not install must survive"

    def test_missing_directory_is_a_no_op(self, tmp_path: Path) -> None:
        from palette_skill import install

        assert install.uninstall(tmp_path / "absent") == 0

    def test_removes_a_directory_we_installed(self, tmp_path: Path) -> None:
        from palette_skill import install

        target = tmp_path / "palette"
        target.mkdir()
        (target / install.MANIFEST_NAME).write_text('{"host": "cuga"}')
        (target / "SKILL.md").write_text("x")

        assert install.uninstall(target) == 0
        assert not target.exists()


class TestPackaging:
    """requirements.txt and the [server] extra are two lists of the same deps."""

    def _requirement_names(self, text: str) -> set[str]:
        names = set()
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            name = line.split("[", 1)[0].split(">=", 1)[0].split("==", 1)[0].strip()
            names.add(name.lower())
        return names

    def test_client_runtime_is_httpx_only(self) -> None:
        """Every runtime dependency is paid by every agent, every session.

        The wheel ships into agent sandboxes. `test_client_imports_nothing_heavy`
        guards what the code *imports*; this guards what the package *declares*,
        which is the easier of the two to widen by accident — a test or server
        dependency dropped into [project.dependencies] installs everywhere and
        nothing else notices.
        """
        import tomllib  # noqa: PLC0415

        with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
            declared = tomllib.load(handle)["project"]["dependencies"]

        names = {d.split("[")[0].split(">=")[0].split("==")[0].strip().lower() for d in declared}
        extra = sorted(names - {"httpx"})
        assert names == {"httpx"}, (
            f"the client declares runtime dependencies beyond httpx: {extra}.\n"
            "\n"
            "That wheel installs into every agent sandbox, so anything here is paid\n"
            "everywhere. If you added pytest because it was missing from your env,\n"
            "the fix is the install, not the manifest:\n"
            "\n"
            "    uv pip install -e '.[dev]'      # [server] deps + pytest\n"
            "\n"
            "`.[server]` alone deliberately omits pytest — it is for a machine that\n"
            "only runs the service."
        )

    def test_server_extra_matches_requirements_txt(self) -> None:
        import tomllib  # noqa: PLC0415 — stdlib from 3.11

        requirements = self._requirement_names((REPO_ROOT / "requirements.txt").read_text())

        with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
            pyproject = tomllib.load(handle)
        extra = self._requirement_names(
            "\n".join(pyproject["project"]["optional-dependencies"]["server"])
        )

        assert extra == requirements, (
            "requirements.txt (used by the Dockerfile) and the [server] extra have diverged: "
            f"only in requirements.txt: {sorted(requirements - extra)}; "
            f"only in the extra: {sorted(extra - requirements)}"
        )


class TestDeckOrchestration:
    """`run_deck` exists because agents lose the thread when they drive by hand.

    Twice in the wild an agent drafted, never built, and reported success. The
    orchestrator owns the session and computes completion from disk, so the
    claim is not the model's to make.
    """

    def test_verify_rejects_a_missing_deck(self, tmp_path: Path) -> None:
        from palette_skill.client import _verify

        assert _verify(tmp_path)["verified"] is False

    def test_verify_rejects_a_stub_pptx(self, tmp_path: Path) -> None:
        """A 12 KB file is a failed render, not a deck."""
        from palette_skill.client import _verify

        (tmp_path / "deck.pptx").write_bytes(b"x" * 5_000)
        (tmp_path / "slide-01.png").write_bytes(b"\x89PNG" + b"x" * 9_000)
        assert _verify(tmp_path)["verified"] is False

    def test_verify_rejects_a_deck_with_no_previews(self, tmp_path: Path) -> None:
        from palette_skill.client import _verify

        (tmp_path / "deck.pptx").write_bytes(b"x" * 50_000)
        assert _verify(tmp_path)["verified"] is False

    def test_verify_accepts_a_real_deck(self, tmp_path: Path) -> None:
        from palette_skill.client import _verify

        (tmp_path / "deck.pptx").write_bytes(b"x" * 50_000)
        (tmp_path / "slide-01.png").write_bytes(b"\x89PNG" + b"x" * 9_000)
        checked = _verify(tmp_path)
        assert checked["verified"] is True
        assert checked["slide_count"] == 1

    def test_verify_reports_a_path_the_user_can_open(self, tmp_path: Path) -> None:
        """A relative path names a working directory the user has never seen.

        The agent's `./` is a per-thread sandbox workspace, so "saved to
        deck/deck.pptx" sends someone looking in their own shell's cwd and
        finding nothing. Sandboxes restrict permissions rather than remap the
        filesystem, so the resolved path is the one that actually opens.
        """
        from palette_skill.client import _verify

        (tmp_path / "deck.pptx").write_bytes(b"x" * 50_000)
        (tmp_path / "slide-01.png").write_bytes(b"\x89PNG" + b"x" * 9_000)
        checked = _verify(tmp_path)

        assert Path(checked["pptx_path"]).is_absolute()
        assert Path(checked["pptx_path"]).is_file()
        assert Path(checked["dir"]).is_absolute()

    def test_a_missing_deck_has_no_path_to_offer(self, tmp_path: Path) -> None:
        from palette_skill.client import _verify

        checked = _verify(tmp_path)
        assert checked["pptx_path"] is None and checked["verified"] is False

    def test_state_survives_between_calls(self, tmp_path: Path) -> None:
        """Resumability is the whole point — a retry must not orphan the session."""
        from palette_skill.client import _load_state, _save_state

        _save_state(tmp_path, {"stage": "building", "thread_id": "skill-abc"})
        assert _load_state(tmp_path)["thread_id"] == "skill-abc"

    def test_corrupt_state_is_treated_as_absent(self, tmp_path: Path) -> None:
        from palette_skill.client import DECK_STATE, _load_state

        (tmp_path / DECK_STATE).write_text("{not json")
        assert _load_state(tmp_path) == {}

    def test_done_state_is_reverified_not_trusted(self, tmp_path: Path) -> None:
        """A stored 'done' with no files must not report done."""
        from palette_skill.client import PaletteClient, _save_state, run_deck

        _save_state(tmp_path, {"stage": "done", "thread_id": "skill-abc"})
        result = run_deck(PaletteClient("http://unused"), dest=tmp_path)
        assert result["done"] is True  # stage is terminal…
        assert result["verified"] is False, "…but the files are gone, and that must show"


class TestPlanApproval:
    """"Draft a plan, show it to me, then build it" — the commonest phrasing.

    It used to be answered by a doc section telling the agent to drive
    `start-draft` / `wait-draft` / `draft-result` itself, which is the very
    hand-managed sequence `run_deck` was written to abolish. An agent given
    that prompt followed the section and reproduced the original failure
    exactly: four drafts, four thread ids, no build. So the pause belongs
    inside the orchestrator, where the session is still owned.
    """

    class _FakeDraft:
        """A client stub that drafts instantly and records what was built."""

        def __init__(self, plan: str = "# Plan\n" + "section. " * 40) -> None:
            self.plan = plan
            self.built: list[tuple[str, str]] = []

        def start_draft(self, request: str) -> str:
            return "skill-fake"

        def wait_draft(self, thread_id, max_seconds=25.0):
            from palette_skill.client import Progress

            return Progress(stage="draft_done")

        def draft_result(self, thread_id) -> str:
            return self.plan

        def start_build(self, plan, thread_id=None):
            self.built.append((thread_id, plan))
            return thread_id or "skill-fake"

    def _advance(self, pal, dest: Path, **kwargs):
        from palette_skill.client import run_deck

        return run_deck(pal, dest=dest, **kwargs)

    def test_pause_stops_before_the_expensive_stage(self, tmp_path: Path) -> None:
        pal = self._FakeDraft()
        self._advance(pal, tmp_path, request="Q3 sales review", pause_after_plan=True)
        result = self._advance(pal, tmp_path)

        assert result["stage"] == "plan-ready"
        assert result["done"] is False
        assert pal.built == [], "the build must not start until the user approves"
        assert result["plan_markdown"].startswith("# Plan")
        assert (tmp_path / "plan.md").is_file()

    def test_pause_is_idempotent_while_waiting(self, tmp_path: Path) -> None:
        """Polling while the user reads must not build, and must not re-draft."""
        pal = self._FakeDraft()
        self._advance(pal, tmp_path, request="Q3 sales review", pause_after_plan=True)
        for _ in range(3):
            result = self._advance(pal, tmp_path)
        assert result["stage"] == "plan-ready"
        assert pal.built == []

    def test_approve_builds_in_the_same_session(self, tmp_path: Path) -> None:
        """One thread id across draft and build — that is what stops the orphaning."""
        pal = self._FakeDraft()
        first = self._advance(pal, tmp_path, request="Q3 sales review", pause_after_plan=True)
        self._advance(pal, tmp_path)
        result = self._advance(pal, tmp_path, approve=True)

        assert result["stage"] == "building"
        assert len(pal.built) == 1
        assert pal.built[0][0] == first["thread_id"]

    def test_approve_builds_the_edited_plan(self, tmp_path: Path) -> None:
        """If the user asked for changes, the build must use them."""
        pal = self._FakeDraft()
        self._advance(pal, tmp_path, request="Q3 sales review", pause_after_plan=True)
        self._advance(pal, tmp_path)
        (tmp_path / "plan.md").write_text("# Plan, revised\n" + "section. " * 40)
        self._advance(pal, tmp_path, approve=True)

        assert "revised" in pal.built[0][1], "the build replayed the original draft"

    def test_without_pause_it_builds_straight_through(self, tmp_path: Path) -> None:
        pal = self._FakeDraft()
        self._advance(pal, tmp_path, request="Q3 sales review")
        result = self._advance(pal, tmp_path)
        assert result["stage"] == "building"
        assert len(pal.built) == 1

    def test_every_unfinished_result_carries_the_next_command(self, tmp_path: Path) -> None:
        """The instruction has to travel with the output, not live only in SKILL.md.

        An agent that has been polling for six minutes is being pulled toward
        summarising for the user, and SKILL.md was read many turns ago. A
        literal command in the payload it just printed is much harder to talk
        itself out of. Runs that ended mid-build all ended on a turn whose last
        output had nothing actionable in it.
        """
        pal = self._FakeDraft()
        started = self._advance(pal, tmp_path, request="Q3 sales review")
        assert started["next"].startswith("palette-skill deck --dest ")
        assert "--max-seconds" in started["next"]

        building = self._advance(pal, tmp_path)
        assert building["done"] is False
        assert "--max-seconds" in building["next"]

    def test_a_paused_plan_points_at_approve_not_at_polling(self, tmp_path: Path) -> None:
        """Telling a waiting agent to poll would spin it against a stopped machine."""
        pal = self._FakeDraft()
        self._advance(pal, tmp_path, request="Q3 sales review", pause_after_plan=True)
        paused = self._advance(pal, tmp_path)
        assert paused["stage"] == "plan-ready"
        assert paused["next"].endswith("--approve")

    def test_a_finished_deck_has_nothing_next(self, tmp_path: Path) -> None:
        """`next` present means keep going, so a done deck must not carry one."""
        from palette_skill.client import PaletteClient, _save_state, run_deck

        (tmp_path / "deck.pptx").write_bytes(b"x" * 50_000)
        (tmp_path / "slide-01.png").write_bytes(b"\x89PNG" + b"x" * 9_000)
        _save_state(tmp_path, {"stage": "done", "thread_id": "skill-abc"})
        finished = run_deck(PaletteClient("http://unused"), dest=tmp_path)
        assert finished["done"] is True and finished["verified"] is True
        assert "next" not in finished

    def test_an_empty_plan_is_refused_before_the_build(self, tmp_path: Path) -> None:
        """The crafter really did return 0 chars once. Four minutes to render it."""
        from palette_skill.client import PaletteError

        pal = self._FakeDraft(plan="")
        self._advance(pal, tmp_path, request="Q3 sales review")
        with pytest.raises(PaletteError, match="not a usable plan"):
            self._advance(pal, tmp_path)
        assert pal.built == []
