"""The skill has to describe the CLI that actually exists.

`skills/palette/SKILL.md` is hand-written prose telling an agent which commands
to run. Nothing generates it, so nothing stops it describing a flag that was
renamed six months ago — and an agent following it fails in a way that looks
like the agent's fault.

So these read `palette.py`'s own argparse setup and compare. No server, no
network, no model: this tier runs anywhere, in about a second.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "palette"
SKILL_MD = SKILL_DIR / "SKILL.md"
DECK_PY = SKILL_DIR / "scripts" / "deck.py"
PALETTE_PY = REPO_ROOT / "palette.py"


def cli_surface() -> dict[str, set[str]]:
    """{subcommand: {--flags}} read straight out of palette.py's argparse calls."""
    tree = ast.parse(PALETTE_PY.read_text(encoding="utf-8"))
    commands: dict[str, set[str]] = {}
    parsers: dict[str, str] = {}          # local variable name -> subcommand

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not isinstance(call.func, ast.Attribute) or call.func.attr != "add_parser":
            continue
        if not call.args or not isinstance(call.args[0], ast.Constant):
            continue
        name = call.args[0].value
        commands[name] = set()
        for target in node.targets:
            if isinstance(target, ast.Name):
                parsers[target.id] = name

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_argument":
            continue
        owner = node.func.value
        if not isinstance(owner, ast.Name) or owner.id not in parsers:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and str(arg.value).startswith("--"):
                commands[parsers[owner.id]].add(arg.value)
    return commands


def skill_text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


class TestSkillShape:
    """The frontmatter is the routing decision; the body is the playbook."""

    def test_skill_exists_and_has_frontmatter(self) -> None:
        text = skill_text()
        assert text.startswith("---\n"), "a skill needs YAML frontmatter or the host cannot index it"
        head = text.split("---", 2)[1]
        assert re.search(r"^name:\s*palette\s*$", head, re.M)
        assert "description:" in head

    def test_description_carries_the_words_a_user_would_say(self) -> None:
        """The description is the whole routing decision — see the routing miss
        recorded in git history: a request phrased "draft a plan…" with no
        "deck" in it never reached the skill at all."""
        head = skill_text().split("---", 2)[1].lower()
        for word in ("deck", "slide", "presentation", "pptx"):
            assert word in head, f"description never says {word!r}; requests using it will not route here"

    def test_it_is_a_self_contained_folder(self) -> None:
        """skills.sh style: drop the folder in a skills root and it works."""
        assert (SKILL_DIR / "SKILL.md").is_file()
        assert DECK_PY.is_file()
        stray = [p.name for p in SKILL_DIR.rglob("*")
                 if p.is_file() and p.suffix not in {".md", ".py"}
                 and "__pycache__" not in p.parts]
        assert not stray, f"skill folder carries non-portable files: {stray}"


class TestSkillMatchesTheCLI:
    """Every command and flag the skill names must exist in palette.py."""

    def test_every_documented_subcommand_exists(self) -> None:
        surface = cli_surface()
        assert surface, "could not read any subcommands out of palette.py"
        named = set(re.findall(r"palette\.py (build-plan|edit-plan|build-deck)", skill_text()))
        assert named, "the skill names no palette.py commands at all"
        missing = named - set(surface)
        assert not missing, f"skill tells the agent to run commands palette.py lacks: {sorted(missing)}"

    @pytest.mark.parametrize("command", ["build-plan", "edit-plan", "build-deck"])
    def test_every_command_is_documented(self, command: str) -> None:
        """A command nobody documents is a capability the agent never reaches for."""
        assert command in cli_surface(), f"palette.py lost {command}"
        assert f"palette.py {command}" in skill_text(), f"{command} is undocumented in SKILL.md"

    def test_every_flag_the_skill_uses_exists(self) -> None:
        surface = cli_surface()
        text = skill_text()
        problems = []
        for command, flags in surface.items():
            for match in re.finditer(rf"palette\.py {command}([^\n`]*)", text):
                for flag in re.findall(r"--[a-z][a-z-]+", match.group(1)):
                    if flag not in flags:
                        problems.append(f"{command} {flag}")
        assert not problems, f"skill passes flags palette.py does not accept: {sorted(set(problems))}"

    def test_the_context_flag_is_documented(self) -> None:
        """Pasted material is the whole point of --context on a chat host with
        no file upload; undocumented, the agent crams it into the request."""
        assert "--context" in cli_surface()["build-plan"]
        assert "--context" in skill_text()


class TestTheConfirmationGate:
    """Building an unapproved plan wastes minutes and produces the wrong deck."""

    def test_the_gate_is_stated(self) -> None:
        text = skill_text()
        assert "Never call build-deck until the user has confirmed" in text
        assert "build-plan → confirm" in text

    def test_the_gate_is_not_optional(self) -> None:
        assert re.search(r"confirmation gate is (required|mandatory)", skill_text(), re.I)


class TestLongBuildsSurviveAStepLimit:
    """build-deck runs for minutes; some hosts kill a step at 120 seconds."""

    def test_the_async_path_is_documented(self) -> None:
        text = skill_text()
        assert "deck.py start" in text and "deck.py status" in text
        assert "120" in text, "the skill should say which limit it is working around"

    def test_status_reports_verified_from_the_filesystem(self) -> None:
        """`done` must never be relayed from an exit code."""
        source = DECK_PY.read_text(encoding="utf-8")
        assert "def verify(" in source
        assert "MIN_PPTX_BYTES" in source
        assert '"verified": true' in skill_text().lower()

    def test_the_skill_forbids_reporting_an_unverified_deck(self) -> None:
        assert "Never report a deck that does not exist" in skill_text()

    def test_the_agent_is_told_when_a_build_has_run_too_long(self) -> None:
        """An unreachable endpoint looks exactly like a slow render to a poller.

        Palette retries every stage before giving up — measured at 51 minutes
        to fail with nothing but connection timeouts. Without a ceiling the
        agent polls in silence for the whole of it.
        """
        text = skill_text()
        assert "elapsed_seconds" in text
        assert "build.log" in text, "the agent is never told where the reason is written"

    def test_deck_helper_runs_and_is_stdlib_only(self) -> None:
        """It ships into whatever environment the agent has; imports must be safe."""
        result = subprocess.run(
            [sys.executable, str(DECK_PY), "--help"], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        tree = ast.parse(DECK_PY.read_text(encoding="utf-8"))
        imported = {
            (node.module or "").split(".")[0]
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        } | {
            alias.name.split(".")[0]
            for node in ast.walk(tree) if isinstance(node, ast.Import)
            for alias in node.names
        }
        third_party = imported - set(sys.stdlib_module_names) - {"", "__future__"}
        assert not third_party, f"deck.py imports non-stdlib modules: {sorted(third_party)}"


class TestDeckHelperBehaviour:
    """The parts that decide whether a deck is reported as real."""

    def _deck_module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("deck_helper", DECK_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_a_missing_deck_is_not_verified(self, tmp_path: Path) -> None:
        assert self._deck_module().verify(tmp_path)["verified"] is False

    def test_a_stub_pptx_is_not_verified(self, tmp_path: Path) -> None:
        """A failed render can still leave a small well-formed file behind."""
        (tmp_path / "deck.pptx").write_bytes(b"x" * 5_000)
        checked = self._deck_module().verify(tmp_path)
        assert checked["verified"] is False and checked["pptx_bytes"] == 5_000

    def test_a_real_deck_verifies_and_reports_an_absolute_path(self, tmp_path: Path) -> None:
        (tmp_path / "deck.pptx").write_bytes(b"x" * 60_000)
        (tmp_path / "slide-01.png").write_bytes(b"\x89PNG")
        checked = self._deck_module().verify(tmp_path)
        assert checked["verified"] is True
        assert Path(checked["pptx"]).is_absolute(), "a relative path is unusable to the user"
        assert checked["slide_previews"] == 1

    def test_status_without_a_start_refuses_rather_than_guessing(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(DECK_PY), "status", "--out-dir", str(tmp_path)],
            capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "no build started" in result.stderr

    def test_start_refuses_a_missing_plan(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(DECK_PY), "start",
             "--plan", str(tmp_path / "nope.md"), "--out-dir", str(tmp_path / "out")],
            capture_output=True, text=True,
        )
        assert result.returncode != 0 and "no plan at" in result.stderr


class TestPaletteHomeResolution:
    """The skill runs palette.py from a checkout the agent has to locate."""

    def _deck_module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("deck_helper", DECK_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_it_finds_this_checkout_with_no_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PALETTE_HOME", raising=False)
        assert self._deck_module().palette_home() == REPO_ROOT

    def test_a_wrong_palette_home_fails_loudly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Silently falling back would run a different Palette than the user meant."""
        monkeypatch.setenv("PALETTE_HOME", str(tmp_path))
        with pytest.raises(SystemExit, match="no palette.py"):
            self._deck_module().palette_home()

    def test_the_skill_tells_the_agent_about_palette_home(self) -> None:
        assert "PALETTE_HOME" in skill_text()
