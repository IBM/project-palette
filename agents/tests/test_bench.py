"""The third host has to be comparable with the other two, or it is decoration.

Comparable means one thing concretely: the same `judge`. Not an equivalent
judge, not a judge that agrees on the cases tried so far — the same function
object, imported from `benchmark/run.py`. The benchmark's own suite makes this
assertion about the Claude runner; this is the same assertion for this one.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

AGENTS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = AGENTS_DIR.parent
BENCHMARK_DIR = REPO_ROOT / "benchmark"
sys.path.append(str(BENCHMARK_DIR))

#: The runner lives beside the benchmark's other two hosts; the agent it drives
#: lives in agents/. These tests cover the runner.
RUNNER = BENCHMARK_DIR / "react_run.py"
SOURCE = RUNNER.read_text(encoding="utf-8")

bench = pytest.importorskip("react_run")


class TestOneJudgeForEveryHost:
    def test_it_imports_the_judge_rather_than_writing_one(self) -> None:
        import run  # the benchmark's CUGA runner

        assert bench.judge is run.judge
        assert bench.inspect_deck is run.inspect_deck
        assert bench.newest_deck is run.newest_deck

    def test_it_defines_no_scoring_of_its_own(self) -> None:
        """A local `judge` would shadow the import and the drift would be
        invisible — both hosts would still print a number."""
        tree = ast.parse(SOURCE)
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in ("judge", "inspect_deck", "newest_deck"):
            assert name not in defined, f"react_run.py defines its own {name}()"

    def test_it_runs_the_same_cases(self) -> None:
        import cases

        assert bench.CASES is cases.CASES

    def test_a_case_that_forbids_a_deck_is_still_in_the_set(self) -> None:
        """A suite that can be passed by always building is worse than none."""
        assert any(not c.expect_deck for c in bench.CASES)


class TestItKeepsItsOwnColumn:
    def test_the_host_has_its_own_directory(self) -> None:
        assert bench.HOST == "react"
        source = SOURCE
        assert 'out_root / HOST' in source

    def test_it_does_not_write_into_the_other_hosts(self) -> None:
        source = SOURCE
        assert '"cuga"' not in source
        assert '"claude"' not in source

    def test_inputs_are_saved_next_to_outputs(self) -> None:
        source = SOURCE
        assert '"input"' in source and '"output"' in source
        for name in ("request.txt", "replies.txt", "context.md"):
            assert name in source, f"{name} is never written"


class TestTheReportSaysWhatRan:
    def test_the_model_is_recorded_not_hardcoded(self) -> None:
        """This host exists because the model is a variable. A report that does
        not name the model it ran cannot be compared with anything."""
        source = SOURCE
        assert '"model": options.model' in source

    def test_the_step_budget_is_reported(self) -> None:
        """Otherwise a failure is indistinguishable from exhausting it."""
        source = SOURCE
        assert '"recursion_limit"' in source

    def test_the_skill_loading_mode_is_reported(self) -> None:
        source = SOURCE
        assert '"skill_loading"' in source


class TestTheTrace:
    def test_it_sets_the_variable_the_skill_reads(self) -> None:
        """Tracing is off unless `$PALETTE_TRACE` is set, so the runner sets it
        per case — without which the trace is silently empty and every
        edit-case verdict is unprovable."""
        source = SOURCE
        assert 'os.environ["PALETTE_TRACE"]' in source
        assert "palette-calls.jsonl" in source

    def test_it_clears_the_variable_afterwards(self) -> None:
        """Cases run in one process; a leaked path appends the next case's calls
        to the previous case's trace."""
        source = SOURCE
        assert 'os.environ.pop("PALETTE_TRACE"' in source


class TestOneBadCaseDoesNotEndTheRun:
    def test_a_case_that_cannot_start_is_recorded_as_a_failure(self, monkeypatch, tmp_path) -> None:
        """Building the agent reaches watsonx. A credential or quota problem on
        case 4 must not throw away cases 5 through 33."""
        monkeypatch.setattr(bench.model_factory, "build", lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("watsonx said no")
        ))

        case = next(c for c in bench.CASES if c.name == "plain_request")
        options = type("O", (), {
            "model": "openai/gpt-oss-120b", "eager": False, "timeout": 5,
            "recursion_limit": 30, "turn_timeout": 5,
        })()
        result = bench.run_case(case, None, tmp_path, options)

        assert not result.ok
        assert any("could not start" in f for f in result.failures)
        assert (tmp_path / "react" / "plain_request" / "input" / "request.txt").is_file(), (
            "the inputs were not saved, so the failure cannot be reproduced"
        )
