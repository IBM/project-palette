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
            def invoke(self, state, config):  # noqa: ANN001, ARG002
                seen.update(config)
                return {"messages": [AIMessage(content="ok")]}

        session = Session(graph=Graph(), thread_id="t-1", recursion_limit=77)
        assert session.send("hello") == "ok"
        assert seen["recursion_limit"] == 77
        assert seen["configurable"]["thread_id"] == "t-1"

    def test_turns_are_recorded_for_the_transcript(self) -> None:
        class Graph:
            def invoke(self, state, config):  # noqa: ANN001, ARG002
                return {"messages": [AIMessage(content="answered")]}

        session = Session(graph=Graph(), thread_id="t")
        session.send("first")
        session.send("second")
        assert [t["sent"] for t in session.turns] == ["first", "second"]
        assert all(t["answer"] == "answered" for t in session.turns)

    def test_content_returned_as_parts_is_flattened(self) -> None:
        """Some providers return content as a list of blocks rather than a
        string; a host that stores the list writes an unreadable transcript."""
        class Graph:
            def invoke(self, state, config):  # noqa: ANN001, ARG002
                return {"messages": [AIMessage(content=[{"type": "text", "text": "hi"}])]}

        assert Session(graph=Graph(), thread_id="t").send("x") == "hi"


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

    def test_building_an_agent_does_not_touch_the_skill(
        self, workspace: Path, card, skills_root: Path, scripted
    ) -> None:
        before = tree_digest(skills_root)
        build_agent(workspace, [card], scripted(replies=[], seen=[], bound=[]))
        assert tree_digest(skills_root) == before
