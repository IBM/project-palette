"""The shell is where a host quietly ruins a benchmark run.

Three ways, all of which have a test here: it runs somewhere other than the
case directory, so the deck the judge looks for is not there; it truncates the
plan the agent has to show the user; or it kills a command that the skill
explicitly says cannot be killed, and reports the timeout as a failure.
"""

from __future__ import annotations

import json
from pathlib import Path

from palette_react.tools import (
    DEFAULT_MAX_OUTPUT,
    DEFAULT_TIMEOUT,
    build_tools,
    make_bash,
    make_load_skill,
)


class TestTheShellStaysInTheWorkspace:
    def test_commands_run_in_the_given_directory(self, workspace: Path) -> None:
        bash = make_bash(workspace)
        assert str(workspace.resolve()) in bash.invoke({"command": "pwd"})

    def test_a_file_written_lands_in_the_workspace(self, workspace: Path) -> None:
        """The judge reads the deck off the case directory. A shell that wrote
        elsewhere would produce a real deck scored as missing."""
        make_bash(workspace).invoke({"command": "echo hello > out.txt"})
        assert (workspace / "out.txt").read_text().strip() == "hello"

    def test_each_call_starts_from_the_workspace_again(self, workspace: Path) -> None:
        bash = make_bash(workspace)
        bash.invoke({"command": "mkdir -p sub && cd sub"})
        assert str(workspace.resolve()) in bash.invoke({"command": "pwd"})


class TestTheEnvironmentReachesTheSkill:
    def test_palette_trace_is_inherited(self, workspace: Path, monkeypatch) -> None:
        """The benchmark reads `palette-calls.jsonl`, and deck.py writes it only
        when this variable is set. A shell with a scrubbed environment produces
        a run whose trace is silently empty."""
        monkeypatch.setenv("PALETTE_TRACE", "/tmp/some-trace.jsonl")
        assert "/tmp/some-trace.jsonl" in make_bash(workspace).invoke(
            {"command": "echo $PALETTE_TRACE"}
        )

    def test_palette_home_is_inherited(self, workspace: Path, monkeypatch) -> None:
        monkeypatch.setenv("PALETTE_HOME", "/somewhere/palette")
        assert "/somewhere/palette" in make_bash(workspace).invoke(
            {"command": "echo $PALETTE_HOME"}
        )


class TestOutputHandling:
    def test_stderr_and_exit_code_are_reported(self, workspace: Path) -> None:
        out = make_bash(workspace).invoke({"command": "echo bad >&2; exit 3"})
        assert "bad" in out
        assert "exit code 3" in out

    def test_long_output_is_truncated_and_says_so(self, workspace: Path) -> None:
        out = make_bash(workspace, max_output=200).invoke(
            {"command": "python3 -c \"print('x' * 5000)\""}
        )
        assert len(out) < 400
        assert "truncated" in out

    def test_the_default_ceiling_fits_a_whole_plan(self) -> None:
        """A plan for a long deck comes back on stdout and is the thing the user
        approves. Truncating it mid-plan turns a good run into a puzzling one."""
        assert DEFAULT_MAX_OUTPUT >= 20_000

    def test_empty_output_is_explicit(self, workspace: Path) -> None:
        assert make_bash(workspace).invoke({"command": "true"}) == "[no output]"


class TestTimeoutsDoNotLie:
    def test_the_default_outlasts_the_holding_commands(self) -> None:
        """`plan` holds up to 90s and `status` up to 60s by design. A 30s default
        would fail every case and look like Palette breaking."""
        assert DEFAULT_TIMEOUT >= 180

    def test_a_timeout_says_it_is_not_a_failure(self, workspace: Path) -> None:
        """The skill is explicit: a cut-short step is never evidence of failure,
        because the work runs in its own process and finishes anyway. The tool
        has to say so, or the model infers a crash and gives up."""
        out = make_bash(workspace, timeout=1).invoke({"command": "sleep 5"})
        assert "not evidence" in out
        assert "poll" in out.lower()

    def test_a_timeout_does_not_raise(self, workspace: Path) -> None:
        assert isinstance(make_bash(workspace, timeout=1).invoke({"command": "sleep 5"}), str)


class TestLoadSkill:
    def test_it_returns_the_body_verbatim(self, card) -> None:
        assert make_load_skill(card).invoke({"name": "palette"}) == card.body

    def test_an_unknown_name_lists_what_exists(self, card) -> None:
        out = make_load_skill(card).invoke({"name": "powerpoint"})
        assert "powerpoint" in out
        assert "palette" in out

    def test_whitespace_around_the_name_is_tolerated(self, card) -> None:
        assert make_load_skill(card).invoke({"name": " palette "}) == card.body


class TestTheToolset:
    def test_lazy_mode_offers_both_tools(self, workspace: Path, card) -> None:
        assert [t.name for t in build_tools(workspace, [card])] == ["load_skill", "bash"]

    def test_eager_mode_drops_load_skill(self, workspace: Path, card) -> None:
        """In eager mode the body is already in the prompt; keeping the tool
        would let the agent load it twice."""
        assert [t.name for t in build_tools(workspace, [card], lazy=False)] == ["bash"]

    def test_the_tools_describe_themselves_to_a_model(self, workspace: Path, card) -> None:
        for tool in build_tools(workspace, [card]):
            assert tool.description, f"{tool.name} has no description"
            assert tool.args_schema is not None

    def test_the_bash_description_names_the_working_directory(
        self, workspace: Path, card
    ) -> None:
        """The skill says 'do not cd anywhere'. The tool has to tell the model
        where 'here' is, or that instruction has no referent."""
        bash = next(t for t in build_tools(workspace, [card]) if t.name == "bash")
        assert str(workspace.resolve()) in bash.description


class TestItIsUsableByAModel:
    def test_the_tools_serialise_to_json_schema(self, workspace: Path, card) -> None:
        """Whatever the provider, the toolset ends up as JSON schema on the wire."""
        for tool in build_tools(workspace, [card]):
            json.dumps(tool.args_schema.model_json_schema())
