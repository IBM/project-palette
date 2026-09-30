"""The scaffold — and what it must not quietly do for the model.

The value of this host is that it is thin: no planner, no todo list, nothing
that could pass a case on the skill's behalf. Most of these tests exist to keep
it that way, and the sharpest one is the lazy/eager split — inlining the skill
body answers the routing question before the model sees the request, which
would silently retire a case the benchmark deliberately includes.
"""

from __future__ import annotations

from pathlib import Path

from langchain_core.messages import AIMessage

from conftest import tree_digest
from palette_react.agent import (
    DEFAULT_RECURSION_LIMIT,
    Session,
    build_agent,
    system_prompt,
)


class TestLazyLoadingKeepsRoutingAMeasurement:
    def test_lazy_offers_the_description_but_not_the_body(self, card) -> None:
        prompt = system_prompt([card], lazy=True)
        assert card.description in prompt
        assert "load_skill" in prompt
        # The body is 14KB of instructions. Its presence here is the whole
        # difference between the two modes.
        assert card.body not in prompt
        assert "deck.py plan" not in prompt

    def test_eager_inlines_the_body(self, card) -> None:
        prompt = system_prompt([card], lazy=False)
        assert card.body in prompt
        assert "deck.py plan" in prompt

    def test_eager_is_not_the_default(self, card) -> None:
        """A case tagged `routing` tests whether the agent recognises a deck
        request with no deck vocabulary in it. Eager loading tells it in
        advance, so lazy has to be what runs unless asked otherwise."""
        assert system_prompt([card]) == system_prompt([card], lazy=True)


class TestTheHostSuppliesLocationNotBehaviour:
    def test_the_prompt_names_the_skills_folder(self, card, skills_root: Path) -> None:
        """SKILL.md refers to its own script as `skills/palette/scripts/deck.py`
        — a path relative to a skills root it cannot know. Supplying that root
        is the loader's job; editing the skill to hardcode a path is not."""
        prompt = system_prompt([card])
        assert str(skills_root) in prompt

    def test_the_prompt_does_not_teach_palette(self, card) -> None:
        """Everything the agent knows about building a deck must come from the
        skill. A host prompt that mentioned the commands would be coaching, and
        the benchmark would be measuring this file.

        Checked against a neutral card, so that the skill's own description —
        which a loader is supposed to surface verbatim, and which does mention
        .pptx — cannot be mistaken for the host adding it.
        """
        from dataclasses import replace

        neutral = replace(card, description="does a thing", body="THE BODY")
        prompt = system_prompt([neutral], lazy=True).lower()
        for leak in ("plan.md", "--context", "build-deck", "pptx", "approve the plan",
                     "slide", "deck"):
            assert leak not in prompt, f"the host prompt coaches the model about {leak!r}"

    def test_it_survives_having_no_skills(self) -> None:
        assert "none" in system_prompt([]).lower()


class TestTheStepBudgetIsExplicit:
    def test_the_limit_is_raised_above_langgraph_default(self) -> None:
        """LangGraph defaults to 25, roughly twelve model turns — less than one
        deck, given the judge alone tolerates twelve status polls. Hitting the
        ceiling is indistinguishable from the agent giving up."""
        assert DEFAULT_RECURSION_LIMIT > 25
        assert DEFAULT_RECURSION_LIMIT >= 100

    def test_send_passes_the_limit_and_the_thread(self) -> None:
        seen = {}

        class Graph:
            def stream(self, state, config, stream_mode):  # noqa: ANN001, ARG002
                seen.update(config)
                yield {"agent": {"messages": [AIMessage(content="ok")]}}

        session = Session(graph=Graph(), thread_id="t-1", recursion_limit=77)
        assert session.send("hello") == "ok"
        assert seen["recursion_limit"] == 77
        assert seen["configurable"]["thread_id"] == "t-1"

    def test_turns_are_recorded_for_the_transcript(self) -> None:
        class Graph:
            def stream(self, state, config, stream_mode):  # noqa: ANN001, ARG002
                yield {"agent": {"messages": [AIMessage(content="answered")]}}

        session = Session(graph=Graph(), thread_id="t")
        session.send("first")
        session.send("second")
        assert [t["sent"] for t in session.turns] == ["first", "second"]
        assert all(t["answer"] == "answered" for t in session.turns)

    def test_content_returned_as_parts_is_flattened(self) -> None:
        """Some providers return content as a list of blocks rather than a
        string; a host that stores the list writes an unreadable transcript."""
        class Graph:
            def stream(self, state, config, stream_mode):  # noqa: ANN001, ARG002
                yield {"agent": {"messages": [
                    AIMessage(content=[{"type": "text", "text": "hi"}])
                ]}}

        assert Session(graph=Graph(), thread_id="t").send("x") == "hi"


class TestALongTurnShowsProgress:
    """A turn that builds a deck spends minutes inside `status` holds.

    Reported from a real run: after "yes" the CLI printed nothing until the
    deck was finished, roughly two minutes later. The deck was fine; the run
    looked hung, and the obvious reading of a hung run is a crashed one.
    """

    def test_tool_calls_are_emitted_as_they_happen(
        self, workspace: Path, card, scripted
    ) -> None:
        seen: list[str] = []
        model = scripted(
            replies=[
                AIMessage(content="", tool_calls=[
                    {"name": "bash",
                     "args": {"command": "deck.py status --out-dir ./deck"}, "id": "1"}
                ]),
                AIMessage(content="built"),
            ],
            seen=[], bound=[],
        )
        session = build_agent(workspace, [card], model, thread_id="t-progress")
        assert session.send("build it", on_event=seen.append) == "built"

        joined = "\n".join(seen)
        assert "bash" in joined
        assert "deck.py status" in joined, "the command being run is not shown"
        assert any(line.startswith("←") for line in joined.splitlines()), (
            "the result of the command is never shown"
        )

    def test_events_arrive_before_the_turn_returns(
        self, workspace: Path, card, scripted
    ) -> None:
        """Emitting them all at the end would leave the hang exactly as it was."""
        order: list[str] = []
        model = scripted(
            replies=[
                AIMessage(content="", tool_calls=[
                    {"name": "bash", "args": {"command": "echo one"}, "id": "1"}
                ]),
                AIMessage(content="done"),
            ],
            seen=[], bound=[],
        )
        session = build_agent(workspace, [card], model, thread_id="t-order")
        session.send("go", on_event=lambda line: order.append(f"event:{line[:6]}"))
        order.append("returned")

        assert order[-1] == "returned"
        assert len(order) > 1, "no events were emitted at all"

    def test_steps_are_kept_on_the_turn(self, workspace: Path, card, scripted) -> None:
        """The transcript should show what the agent did, not only what it said."""
        model = scripted(
            replies=[
                AIMessage(content="", tool_calls=[
                    {"name": "load_skill", "args": {"name": "palette"}, "id": "1"}
                ]),
                AIMessage(content="ready"),
            ],
            seen=[], bound=[],
        )
        session = build_agent(workspace, [card], model, thread_id="t-steps")
        session.send("hello")
        assert session.turns[-1]["steps"], "the turn recorded no steps"

    def test_a_tool_call_does_not_overwrite_the_answer(
        self, workspace: Path, card, scripted
    ) -> None:
        """Tool-call messages carry empty content. Taking the last AI message
        blindly would return "" for every turn that ended in a tool call."""
        model = scripted(
            replies=[
                AIMessage(content="", tool_calls=[
                    {"name": "bash", "args": {"command": "true"}, "id": "1"}
                ]),
                AIMessage(content="the real answer"),
            ],
            seen=[], bound=[],
        )
        session = build_agent(workspace, [card], model, thread_id="t-answer")
        assert session.send("go") == "the real answer"

    def test_on_event_is_optional(self, workspace: Path, card, scripted) -> None:
        """bench.py does not pass one; a missing callback must not crash a run."""
        model = scripted(replies=[AIMessage(content="fine")], seen=[], bound=[])
        session = build_agent(workspace, [card], model, thread_id="t-none")
        assert session.send("go") == "fine"


class TestItAssemblesAndRuns:
    def test_the_graph_is_built_with_both_tools_bound(
        self, workspace: Path, card, scripted
    ) -> None:
        model = scripted(replies=[], seen=[], bound=[])
        build_agent(workspace, [card], model)
        assert model.bound == ["load_skill", "bash"]

    def test_a_scripted_conversation_reaches_the_shell(
        self, workspace: Path, card, scripted
    ) -> None:
        """End to end through the real graph: the model asks for the skill, then
        runs a command, then answers. No network, no deck."""
        model = scripted(
            replies=[
                AIMessage(content="", tool_calls=[
                    {"name": "load_skill", "args": {"name": "palette"}, "id": "1"}
                ]),
                AIMessage(content="", tool_calls=[
                    {"name": "bash", "args": {"command": "echo ran > proof.txt"}, "id": "2"}
                ]),
                AIMessage(content="here is the plan"),
            ],
            seen=[], bound=[],
        )
        session = build_agent(workspace, [card], model, thread_id="t-e2e")

        assert session.send("Build me a deck about caching") == "here is the plan"
        assert (workspace / "proof.txt").read_text().strip() == "ran"

        # The skill body reached the model only after it asked for it.
        rendered = "\n".join(
            str(m.content) for turn in model.seen for m in turn
        )
        assert "deck.py plan" in rendered

    def test_state_persists_across_turns(self, workspace: Path, card, scripted) -> None:
        """A deck is never one turn, and the interesting failures only show up
        on the second. Without a checkpointer the agent starts fresh each time
        and every edit case is unmeasurable."""
        model = scripted(replies=["first", "second"], seen=[], bound=[])
        session = build_agent(workspace, [card], model, thread_id="t-mem")
        session.send("Build a deck about feature flags")
        session.send("make it 3 slides")

        second_turn = model.seen[-1]
        contents = " ".join(str(m.content) for m in second_turn)
        assert "feature flags" in contents, "the second turn lost the first"

    def test_the_workspace_is_created_if_missing(self, tmp_path: Path, card, scripted) -> None:
        place = tmp_path / "nested" / "run"
        build_agent(place, [card], scripted(replies=[], seen=[], bound=[]))
        assert place.is_dir()

    def test_the_skill_is_staged_into_the_workspace_by_default(
        self, workspace: Path, card, scripted
    ) -> None:
        """So that the literal command in SKILL.md resolves from the working
        directory, instead of costing a failed round trip on every run."""
        build_agent(workspace, [card], scripted(replies=[], seen=[], bound=[]))
        assert (workspace / "skills" / "palette" / "scripts" / "deck.py").is_file()

    def test_the_prompt_points_at_the_staged_copy(
        self, workspace: Path, card, scripted
    ) -> None:
        """Not at the checkout — otherwise the two locations disagree and the
        model has to pick."""
        model = scripted(replies=["ok"], seen=[], bound=[])
        session = build_agent(workspace, [card], model, thread_id="t-staged")
        session.send("hello")

        system = str(model.seen[0][0].content)
        assert str(workspace / "skills") in system
        assert str(card.directory.parent) not in system

    def test_staging_can_be_turned_off(self, workspace: Path, card, scripted) -> None:
        build_agent(
            workspace, [card], scripted(replies=[], seen=[], bound=[]), stage_skills=False
        )
        assert not (workspace / "skills").exists()

    def test_building_an_agent_does_not_touch_the_skill(
        self, workspace: Path, card, skills_root: Path, scripted
    ) -> None:
        before = tree_digest(skills_root)
        build_agent(workspace, [card], scripted(replies=[], seen=[], bound=[]))
        assert tree_digest(skills_root) == before
