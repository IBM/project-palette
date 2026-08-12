"""This host is a guest in the repository.

The skill is what is being measured and the benchmark is what does the
measuring; a host that adjusts either to suit itself has invalidated both. So
nothing outside `agents/` changed to make this work, and these tests fail if
that stops being true.

The practical version of the rule: `agents/` may *read* anything and *write*
only inside its own workspace.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from conftest import tree_digest
from palette_react import skill as skill_loader
from palette_react.agent import build_agent, system_prompt
from palette_react.tools import build_tools

AGENTS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = AGENTS_DIR.parent
SOURCES = sorted((AGENTS_DIR / "palette_react").glob("*.py"))


class TestTheSkillIsLeftAlone:
    """The rule that did not change.

    The benchmark and the Makefile now know about this host — that was asked
    for, and `benchmark/react_run.py` is the runner. `skills/` is the one place
    still off limits, because it is the thing being measured: a skill adjusted
    to suit a host is a skill that no longer tells you anything about the host.
    """

    def test_the_skill_does_not_mention_this_host(self) -> None:
        done = subprocess.run(
            ["git", "grep", "-lE", "palette_react|react_run|langgraph", "--", "skills/"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert not done.stdout.strip(), (
            "the skill now references this host, which means it was adapted to "
            "one of the agents it is meant to measure:\n" + done.stdout
        )

    def test_the_skill_has_no_uncommitted_changes(self) -> None:
        """`skills/` dirty means this host edited the thing it exists to measure.

        Only `skills/` — `benchmark/` is under active development by its owner
        and is legitimately dirty, so its cleanliness proves nothing either way.
        What covers the benchmark instead is the runtime check below, which
        hashes it before and after a full run.
        """
        done = subprocess.run(
            ["git", "status", "--porcelain", "--", "skills/"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        assert not done.stdout.strip(), (
            "the skill has uncommitted changes:\n" + done.stdout
        )

    def test_no_test_module_shares_a_basename_with_the_repo_suite(self) -> None:
        """Two `test_skill.py` files broke collection whenever both suites ran
        in one pytest invocation — neither directory is a package, so pytest
        cannot tell the modules apart. Individually each suite passed, which is
        why it went unnoticed until someone ran `pytest tests agents/tests`.
        """
        mine = {p.name for p in (AGENTS_DIR / "tests").glob("test_*.py")}
        theirs = {p.name for p in (REPO_ROOT / "tests").glob("test_*.py")}
        clash = mine & theirs
        assert not clash, (
            f"these basenames exist in both test suites and will break "
            f"collection when both are run together: {sorted(clash)}"
        )

    def test_the_runner_is_registered_with_the_benchmark(self) -> None:
        """The other half of the same rule: integration belongs in the
        benchmark, not in the skill."""
        assert (REPO_ROOT / "benchmark" / "react_run.py").is_file()
        assert "bench-react" in (REPO_ROOT / "Makefile").read_text(encoding="utf-8")


class TestItGoesThroughTheSkillLikeAnyHost:
    def test_it_imports_none_of_palette_internals(self) -> None:
        """Reaching into `render`, `pipeline` or `palette.py` would build decks
        by a route no real host has, and the results would describe nothing."""
        forbidden = {"render", "pipeline", "palette", "app", "postprocess",
                     "refine", "prompts", "harness_prompts", "detector", "intake"}
        for source in SOURCES:
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = {a.name.split(".")[0] for a in node.names}
                elif isinstance(node, ast.ImportFrom):
                    names = {(node.module or "").split(".")[0]}
                else:
                    continue
                leaked = names & forbidden
                assert not leaked, f"{source.name} imports Palette internals: {leaked}"

    def test_the_only_executable_it_knows_is_the_skills_script(self) -> None:
        """Nothing here should name palette.py — deck.py is the skill's own
        entry point and the skill is explicit that hosts do not bypass it."""
        for source in SOURCES:
            text = source.read_text(encoding="utf-8")
            assert "palette.py" not in text or source.name in {"skill.py", "cli.py", "bench.py"}, (
                f"{source.name} references palette.py directly"
            )

    def test_no_module_writes_to_the_skill_or_the_benchmark(self) -> None:
        mutators = (".write_text(", ".write_bytes(", ".unlink(", "shutil.copy",
                    "shutil.rmtree", "open(")
        for source in SOURCES:
            for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
                if not any(m in line for m in mutators):
                    continue
                target = line.lower()
                assert "skills" not in target and "skill_dir" not in target, (
                    f"{source.name}:{number} writes into the skill folder: {line.strip()}"
                )
                assert "benchmark_dir" not in target, (
                    f"{source.name}:{number} writes into benchmark/: {line.strip()}"
                )


class TestARunLeavesTheRepositoryAlone:
    def test_the_full_offline_flow_changes_nothing(self, workspace: Path, scripted) -> None:
        """Load the skill, build the agent, run its tools — then check that the
        skill and the benchmark are byte-identical to before."""
        skills = REPO_ROOT / "skills"
        benchmark = REPO_ROOT / "benchmark"
        before_skills, before_bench = tree_digest(skills), tree_digest(benchmark)

        card = skill_loader.load("palette", skills)
        system_prompt([card], lazy=False)
        for tool in build_tools(workspace, [card]):
            if tool.name == "bash":
                tool.invoke({"command": "echo touched > out.txt"})
            else:
                tool.invoke({"name": "palette"})
        build_agent(workspace, [card], scripted(replies=[], seen=[], bound=[]))

        assert tree_digest(skills) == before_skills, "the skill changed during a run"
        assert tree_digest(benchmark) == before_bench, "the benchmark changed during a run"
        assert (workspace / "out.txt").is_file(), "the run did not actually do anything"

    def test_writes_land_only_in_the_workspace(self, workspace: Path, card) -> None:
        bash = next(t for t in build_tools(workspace, [card]) if t.name == "bash")
        bash.invoke({"command": "mkdir -p deck && echo x > deck/plan.md"})
        assert sorted(p.name for p in workspace.rglob("*") if p.is_file()) == ["plan.md"]
