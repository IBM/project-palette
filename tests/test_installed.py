"""Is the skill correctly installed *where an agent will actually read it*?

Every other test in this repo checks the skill in the working tree. Agents do
not read the working tree — they read a copy under a skills root, and the gap
between the two is the single most common failure this project has had. Twice,
a run was diagnosed at length before anyone noticed it was executing a copy
made before the fix.

So this file checks the copies. It takes the three locations as input and
skips, loudly and by name, for any it was not given:

    PALETTE_HOME   the checkout the skill shells into      (default: this repo)
    CUGA_HOME      a CUGA checkout, read as <it>/.cuga/skills
    SKILLS_ROOTS   any other skills roots, colon-separated (default: ~/.claude/skills)

Run it directly, or through `make verify CUGA=<path>`:

    CUGA_HOME=~/code/cuga-agent .venv/bin/python -m pytest tests/test_installed.py -q

Everything here is filesystem and subprocess work: no models, no network, no
key. The one test that builds a real deck is opt-in via PALETTE_VERIFY_DECK=1,
because it costs three to ten minutes and needs the VPN.
"""

from __future__ import annotations

import filecmp
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE = REPO_ROOT / "skills" / "palette"
IGNORE = ["__pycache__", ".DS_Store"]


def _label(path: Path) -> str:
    """A name you can read in a test id and know which host failed."""
    if ".claude" in path.parts:
        return "claude"
    if ".cuga" in path.parts:
        return "cuga"
    return path.parent.name or path.name


def _roots() -> dict[str, Path]:
    """{label: skills-root} for every location this run was told about."""
    found: dict[str, Path] = {}

    cuga = os.environ.get("CUGA_HOME", "").strip()
    if cuga:
        found["cuga"] = Path(cuga).expanduser() / ".cuga" / "skills"

    raw = os.environ.get("SKILLS_ROOTS", "~/.claude/skills")
    for entry in filter(None, (part.strip() for part in raw.split(":"))):
        path = Path(entry).expanduser()
        found.setdefault(_label(path), path)

    return found


ROOTS = _roots()


@pytest.fixture(params=sorted(ROOTS), ids=lambda label: label)
def installed(request: pytest.FixtureRequest) -> Path:
    """The installed `palette` folder for one host, or a skip naming the host."""
    root = ROOTS[request.param]
    skill = root / "palette"
    if not skill.is_dir():
        pytest.skip(
            f"no skill installed at {skill} — run `make skill-install CUGA=<path>` "
            f"or `make skill-install-claude`"
        )
    return skill


def _differences(left: Path, right: Path) -> list[str]:
    """Every file that differs between two trees, as readable paths."""
    comparison = filecmp.dircmp(str(left), str(right), ignore=IGNORE)
    out: list[str] = []

    def walk(node: filecmp.dircmp, prefix: str) -> None:
        out.extend(f"{prefix}{name} (only in installed copy)" for name in node.left_only)
        out.extend(f"{prefix}{name} (missing from installed copy)" for name in node.right_only)
        out.extend(f"{prefix}{name} (contents differ)" for name in node.diff_files)
        for name, sub in node.subdirs.items():
            walk(sub, f"{prefix}{name}/")

    walk(comparison, "")
    return out


class TestTheInstalledCopy:
    def test_it_is_byte_identical_to_the_source(self, installed: Path) -> None:
        """The whole point. A copy that has drifted is a copy running old code.

        Both real CUGA failures were this: the fix was in the working tree and
        the agent was running the copy from before it.
        """
        drift = _differences(installed, SOURCE)
        assert not drift, (
            f"{installed} has drifted from {SOURCE}:\n  " + "\n  ".join(drift) +
            "\nreinstall it: make skill-install CUGA=<path> / make skill-install-claude"
        )

    def test_it_carries_the_frontmatter_a_host_routes_on(self, installed: Path) -> None:
        """No frontmatter, no discovery — the skill is invisible however good it is."""
        text = (installed / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\n"), "SKILL.md has no YAML frontmatter"
        header = text.split("---", 2)[1]
        assert "name: palette" in header
        assert "description:" in header, "the description is the entire routing decision"

    def test_its_helper_runs_where_it_was_installed(self, installed: Path) -> None:
        """`deck.py` is stdlib-only so it runs under whatever python the host has."""
        result = subprocess.run(
            [sys.executable, str(installed / "scripts" / "deck.py"), "--help"],
            capture_output=True, text=True, timeout=60,
        )
        assert result.returncode == 0, f"deck.py --help failed:\n{result.stderr}"
        for command in ("plan", "plan-status", "start", "status"):
            assert command in result.stdout, f"{command!r} missing from the installed helper"

    def test_it_finds_the_checkout_from_where_it_sits(self, installed: Path) -> None:
        """An installed copy is outside the repo, so it cannot walk up to it.

        `$PALETTE_HOME` is the only thing that can tell it, and getting this
        wrong produces a confusing mid-build failure rather than a clear one.
        """
        env = {**os.environ, "PALETTE_HOME": str(palette_home())}
        result = subprocess.run(
            [sys.executable, str(installed / "scripts" / "deck.py"),
             "start", "--plan", "/nonexistent/plan.md", "--out-dir", "/tmp/verify-unused"],
            capture_output=True, text=True, timeout=60, env=env,
        )
        # It must get far enough to complain about the *plan*, which means it
        # resolved the checkout first.
        assert "no plan at" in result.stderr, (
            "the installed copy could not resolve $PALETTE_HOME:\n" + result.stderr
        )

    def test_it_refuses_a_checkout_that_is_not_one(self, installed: Path, tmp_path: Path) -> None:
        """Pointing PALETTE_HOME at the skill folder is the classic mistake."""
        env = {**os.environ, "PALETTE_HOME": str(tmp_path)}
        result = subprocess.run(
            [sys.executable, str(installed / "scripts" / "deck.py"),
             "plan", "--request", "x", "--out", str(tmp_path / "p.md")],
            capture_output=True, text=True, timeout=60, env=env,
        )
        assert "no palette.py" in result.stderr, (
            "a PALETTE_HOME with no palette.py in it was accepted"
        )


def palette_home() -> Path:
    """The checkout under test. $PALETTE_HOME, else this repo."""
    configured = os.environ.get("PALETTE_HOME", "").strip()
    home = Path(configured).expanduser() if configured else REPO_ROOT
    if not (home / "palette.py").is_file():
        pytest.skip(f"{home} is not a Palette checkout (no palette.py)")
    return home


class TestTheCheckoutItShellsInto:
    def test_it_has_the_cli_the_skill_drives(self) -> None:
        home = palette_home()
        result = subprocess.run(
            [sys.executable, "palette.py", "--help"],
            cwd=str(home), capture_output=True, text=True, timeout=120,
        )
        assert result.returncode == 0, f"palette.py --help failed:\n{result.stderr}"
        for command in ("build-plan", "edit-plan", "build-deck"):
            assert command in result.stdout, f"palette.py has no {command!r}"

    def test_the_source_of_truth_is_this_repo(self) -> None:
        """One copy, owned by Palette. Anything else drifts."""
        assert (palette_home() / "skills" / "palette" / "SKILL.md").is_file(), (
            "the checkout does not carry the skill; it is meant to ship from here"
        )


@pytest.mark.skipif(
    os.environ.get("PALETTE_VERIFY_DECK", "") != "1",
    reason="set PALETTE_VERIFY_DECK=1 to build a real deck (3-10 minutes, needs RITS + VPN)",
)
class TestItActuallyBuildsADeck:
    """The end of the chain: the installed skill produces a real .pptx.

    Opt-in because it costs minutes and needs the VPN, but it is the only test
    here that proves the whole thing works rather than that it is wired up.
    """

    def test_plan_then_build_then_verify(self, tmp_path: Path) -> None:
        home = palette_home()
        skill = next(iter(sorted(ROOTS.values())), None)
        helper = (skill / "palette" / "scripts" / "deck.py") if skill else SOURCE / "scripts" / "deck.py"
        if not helper.is_file():
            helper = SOURCE / "scripts" / "deck.py"
        env = {**os.environ, "PALETTE_HOME": str(home)}
        assert env.get("RITS_API_KEY"), "RITS_API_KEY is not set; the build cannot reach a model"

        plan = tmp_path / "plan.md"
        drafted = subprocess.run(
            [sys.executable, str(helper), "plan", "--wait",
             "--request", "A 3-slide deck on why deck automation is hard",
             "--out", str(plan)],
            capture_output=True, text=True, timeout=600, env=env,
        )
        assert plan.is_file() and plan.stat().st_size > 0, (
            f"no plan was written:\n{drafted.stdout}\n{drafted.stderr}"
        )

        out_dir = tmp_path / "deck"
        subprocess.run(
            [sys.executable, str(helper), "start", "--plan", str(plan), "--out-dir", str(out_dir)],
            capture_output=True, text=True, timeout=120, env=env, check=True,
        )

        deadline = time.time() + 900
        state: dict = {}
        while time.time() < deadline:
            polled = subprocess.run(
                [sys.executable, str(helper), "status", "--out-dir", str(out_dir)],
                capture_output=True, text=True, timeout=120, env=env,
            )
            state = json.loads(polled.stdout)
            if state.get("done") or state.get("state") == "error":
                break
            time.sleep(20)

        assert state.get("verified") is True, f"no verified deck: {state}"
        pptx = Path(state["pptx"])
        assert pptx.stat().st_size > 100_000, "a real deck is far larger than this"

        import zipfile

        with zipfile.ZipFile(pptx) as archive:
            slides = [n for n in archive.namelist() if n.startswith("ppt/slides/slide")]
            assert slides, "the .pptx contains no slides"
            first = archive.read("ppt/slides/slide1.xml").decode("utf-8", "replace")

        # Palette's renderer forces IBM Plex, so a deck hand-written with
        # pptxgenjs defaults could not carry it. This is what separates "a deck
        # exists" from "*Palette* built this deck".
        assert "IBM Plex" in first, "the deck does not carry Palette's typeface"
