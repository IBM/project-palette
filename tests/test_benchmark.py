"""The benchmark has to be trustworthy before its results mean anything.

None of this needs a model. It checks the parts that decide whether a run is
honest: that the cases cover the interactions they claim to, and that the
verdict is computed from artifacts rather than from anything the agent said.

A benchmark that passes a case it should fail is worse than no benchmark — it
converts an unnoticed regression into evidence that there is not one.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "benchmark"))

from cases import CASES, by_tag  # noqa: E402
from run import CaseResult, judge  # noqa: E402


class TestTheCases:
    def test_there_are_enough_to_be_a_benchmark(self) -> None:
        assert len(CASES) >= 15, "too few conversations to call this a benchmark"

    def test_names_are_unique(self) -> None:
        names = [c.name for c in CASES]
        assert len(names) == len(set(names)), "two cases share a name; runs would overwrite"

    @pytest.mark.parametrize("case", CASES, ids=lambda c: c.name)
    def test_each_case_says_what_it_is_for(self, case) -> None:
        """A case whose purpose is not written down cannot be maintained."""
        assert case.covers, f"{case.name} does not say what it covers"
        assert case.tags, f"{case.name} has no tags, so no group will run it"
        assert case.replies, f"{case.name} never replies, so the gate is never exercised"

    def test_the_interactions_that_matter_are_covered(self) -> None:
        """The four reply kinds, plus context and the gate."""
        tags = {t for c in CASES for t in c.tags}
        for required, why in (
            ("approval", "nothing checks that a plain yes builds"),
            ("edit", "nothing checks that a change routes to edit-plan"),
            ("context", "nothing checks that pasted material reaches --context"),
            ("trap", "nothing checks the replies that are easy to misread"),
            ("gate", "nothing checks that an unapproved plan is NOT built"),
        ):
            assert required in tags, why

    def test_at_least_one_case_forbids_a_deck(self) -> None:
        """Without this the suite can be passed by always building."""
        refusing = [c for c in CASES if not c.expect_deck]
        assert refusing, "every case expects a deck, so skipping the gate would score full marks"

    def test_context_cases_carry_real_material(self) -> None:
        for case in CASES:
            if "context" in case.tags:
                assert len(case.context) > 200, (
                    f"{case.name} is tagged context but pastes almost nothing"
                )

    def test_multi_turn_cases_actually_take_multiple_turns(self) -> None:
        for case in CASES:
            if "multi" in case.tags:
                assert len(case.replies) >= 2, f"{case.name} is tagged multi but replies once"

    def test_by_tag_filters(self) -> None:
        assert by_tag() == CASES
        core = by_tag("core")
        assert core and all("core" in c.tags for c in core)
        assert not by_tag("nonexistent-tag")


class TestTheHarnessMeasuresSomething:
    """A benchmark that offers the model no skill measures nothing.

    This is the failure it actually had: `discover_skills` reads `$CUGA_FOLDER`
    from the environment, while the harness was passing `cuga_folder=` to
    `CugaAgent` — which is the *policies* folder. The scan ran against the
    harness's own directory, found nothing, and the model wrote a deck by hand
    because it had not been offered an alternative. Every case failed, and it
    read as gpt-oss-120b refusing to route to the skill.
    """

    def test_the_run_sets_the_variable_discovery_reads(self) -> None:
        source = (REPO_ROOT / "benchmark" / "run.py").read_text(encoding="utf-8")
        assert 'os.environ["CUGA_FOLDER"]' in source, (
            "nothing sets $CUGA_FOLDER, so no skill will be discovered and every "
            "case will fail for a reason that has nothing to do with the skill"
        )

    def test_preflight_refuses_when_the_skill_is_not_discoverable(self) -> None:
        """Installed and discoverable are different; only one was checked."""
        source = (REPO_ROOT / "benchmark" / "run.py").read_text(encoding="utf-8")
        preflight = source[source.index("def preflight") : source.index("def configure_cuga")]
        assert "discover_skills" in preflight, (
            "preflight checks the skill is installed but never that CUGA can find it"
        )
        assert "would never be offered" in preflight

    def test_the_benchmark_directory_cannot_shadow_the_stdlib(self) -> None:
        """`benchmark/inspect.py` shadowed `inspect` and broke `dataclasses`."""
        source = (REPO_ROOT / "benchmark" / "run.py").read_text(encoding="utf-8")
        assert "sys.path.append(str(BENCHMARK_DIR))" in source, (
            "putting benchmark/ first on sys.path lets any file here shadow a "
            "stdlib module of the same name"
        )
        import sys as _sys

        stdlib = set(getattr(_sys, "stdlib_module_names", ()))
        clashes = {
            p.stem for p in (REPO_ROOT / "benchmark").glob("*.py") if p.stem in stdlib
        }
        assert not clashes, f"these shadow stdlib modules: {sorted(clashes)}"


def _case(name: str = "x", **kwargs):
    from cases import Case

    return Case(name=name, request="build a deck", **kwargs)


class TestTheVerdict:
    """Judgement reads artifacts. It must not be satisfiable by assertion."""

    def test_a_missing_deck_fails(self) -> None:
        result = CaseResult(case=_case())
        judge(result.case, result)
        assert not result.ok and "no .pptx" in result.failures[0]

    def test_a_deck_without_ibm_plex_fails(self) -> None:
        """The check that catches a hand-written deck.

        An agent that builds slides with python-pptx produces a valid .pptx of
        a plausible size. Palette's renderer forces IBM Plex, so that is what
        separates "a deck exists" from "Palette made this".
        """
        result = CaseResult(case=_case(), pptx=Path("/tmp/x.pptx"), slides=5, has_plex=False)
        judge(result.case, result)
        assert not result.ok
        assert any("IBM Plex" in f for f in result.failures)

    def test_the_wrong_slide_count_fails(self) -> None:
        case = _case(expect_slides=3)
        result = CaseResult(case=case, pptx=Path("/tmp/x.pptx"), slides=5, has_plex=True)
        judge(case, result)
        assert any("asked for 3 slides, got 5" in f for f in result.failures)

    def test_an_edit_case_fails_when_edit_plan_was_never_called(self) -> None:
        """The commonest real failure: "make it 3 slides" read as approval."""
        case = _case(tags=("edit",))
        result = CaseResult(
            case=case, pptx=Path("/tmp/x.pptx"), slides=5, has_plex=True,
            palette_calls=[{"command": "plan"}, {"command": "start"}, {"command": "status"}],
        )
        judge(case, result)
        assert any("edit-plan was never called" in f for f in result.failures)

    def test_an_edit_case_passes_when_it_was(self) -> None:
        case = _case(tags=("edit",))
        result = CaseResult(
            case=case, pptx=Path("/tmp/x.pptx"), slides=5, has_plex=True,
            palette_calls=[{"command": "plan"}, {"command": "edit"}, {"command": "start"}],
        )
        judge(case, result)
        assert result.ok, result.failures

    def test_a_context_case_fails_when_nothing_was_passed_as_context(self) -> None:
        case = _case(context="x" * 300)
        result = CaseResult(
            case=case, pptx=Path("/tmp/x.pptx"), slides=5, has_plex=True,
            palette_calls=[{"command": "plan", "args": {"request": "everything crammed in here"}}],
        )
        judge(case, result)
        assert any("--context" in f for f in result.failures)

    def test_a_context_case_passes_when_it_was(self) -> None:
        case = _case(context="x" * 300)
        result = CaseResult(
            case=case, pptx=Path("/tmp/x.pptx"), slides=5, has_plex=True,
            palette_calls=[{"command": "plan", "args": {"request": "r", "context": "the notes"}}],
        )
        judge(case, result)
        assert result.ok, result.failures

    def test_building_an_unapproved_plan_fails(self) -> None:
        """The gate. A deck here is a failure however good the deck is."""
        case = _case(expect_deck=False)
        result = CaseResult(case=case, pptx=Path("/tmp/x.pptx"), slides=5, has_plex=True)
        judge(case, result)
        assert not result.ok
        assert any("never approved" in f for f in result.failures)

    def test_declining_to_build_passes_that_case(self) -> None:
        case = _case(expect_deck=False)
        result = CaseResult(case=case, pptx=None)
        judge(case, result)
        assert result.ok, result.failures


class TestTheJudgeCatchesWastefulRuns:
    """A deck of the right shape can still come from a bad conversation.

    Both of these passed the first version of the judge: one re-planned after
    editing (discarding the revision the user had approved), and the same run
    spent forty-three turns polling. The artifact was fine; the behaviour was
    not, and behaviour is what a skill benchmark is for.
    """

    def test_replanning_after_an_edit_fails(self) -> None:
        case = _case(tags=("edit",))
        result = CaseResult(
            case=case, pptx=Path("/tmp/x.pptx"), slides=3, has_plex=True,
            palette_calls=[{"command": c} for c in
                           ["find", "plan", "edit", "plan", "start", "status"]],
        )
        judge(case, result)
        assert any("drafted again from scratch" in f for f in result.failures)

    def test_editing_then_building_passes(self) -> None:
        case = _case(tags=("edit",))
        result = CaseResult(
            case=case, pptx=Path("/tmp/x.pptx"), slides=3, has_plex=True,
            palette_calls=[{"command": c} for c in
                           ["find", "plan", "edit", "start", "status"]],
        )
        judge(case, result)
        assert result.ok, result.failures

    def test_a_run_that_spins_on_polls_fails(self) -> None:
        case = _case()
        result = CaseResult(
            case=case, pptx=Path("/tmp/x.pptx"), slides=5, has_plex=True,
            palette_calls=[{"command": "plan"}] + [{"command": "plan-status"}] * 40,
        )
        judge(case, result)
        assert any("status polls" in f for f in result.failures)

    def test_a_reasonable_number_of_polls_passes(self) -> None:
        case = _case()
        result = CaseResult(
            case=case, pptx=Path("/tmp/x.pptx"), slides=5, has_plex=True,
            palette_calls=[{"command": "plan"}] + [{"command": "status"}] * 6,
        )
        judge(case, result)
        assert result.ok, result.failures


class TestTheCorpus:
    """The input documents are the benchmark's data, so they must be real.

    Thirteen came out of actual Palette use — all-hands decks, a Q3 review, a
    competitive brief, an architecture. They live in `benchmark/inputs/` as
    files rather than string constants so you can drop your own in beside them.
    """

    def test_the_documents_are_present(self) -> None:
        from corpus import documents

        found = documents()
        assert len(found) >= 10, f"only {len(found)} input documents; the corpus is the dataset"

    def test_every_corpus_case_points_at_a_real_file(self) -> None:
        """A case built from a document that has been deleted is a silent gap."""
        from corpus import INPUTS

        corpus_cases = [c for c in CASES if "corpus" in c.tags]
        assert corpus_cases, "no case uses the input corpus"
        for case in corpus_cases:
            assert case.context, f"{case.name} is tagged corpus but pastes nothing"
            assert len(case.context) > 500, (
                f"{case.name} pastes {len(case.context)} chars — too little to be one of these documents"
            )
        assert INPUTS.is_dir()

    def test_there_are_enough_data_points(self) -> None:
        """The ask was 15-20 decks from real material."""
        assert len([c for c in CASES if "corpus" in c.tags]) >= 12

    def test_reading_a_missing_document_says_what_exists(self) -> None:
        from corpus import read

        with pytest.raises(FileNotFoundError, match="Available:"):
            read("no-such-file.md")


class TestTheSandboxPolicyIsRebuiltPerCase:
    """CUGA's native sandbox reads the working directory once and caches it.

    Cases run in one process and chdir between them, so a cached policy stays
    pinned to case 1 and every later case is denied writes to its own
    workspace. The agent then falls back to /private/tmp, builds a real deck
    where the judge does not look, and the case is scored "no .pptx produced ·
    palette calls: (none)" while the model reports success. Measured once as
    1/5 before this existed.
    """

    def test_the_policy_names_the_current_workspace(self, tmp_path, monkeypatch) -> None:
        """The whole point: after a chdir, the policy must permit writes here."""
        pytest.importorskip(
            "cuga.backend.cuga_graph.nodes.cuga_lite.executors.native.native_sandbox_executor",
            reason="CUGA is not installed in this interpreter",
        )
        import run

        policy = tmp_path / ".cuga_sandbox.sb"
        policy.write_text('(subpath "/some/earlier/case/cuga_workspace")')
        monkeypatch.setattr(run, "SANDBOX_POLICY", policy)
        monkeypatch.chdir(tmp_path)

        run.reset_sandbox_policy()
        written = policy.read_text()
        assert str(tmp_path.resolve() / "cuga_workspace") in written
        assert "/some/earlier/case" not in written, "the stale policy survived"

    def test_the_policy_is_written_not_deleted(self) -> None:
        """Deleting it and letting CUGA rebuild looks right and is wrong.

        `_ensure_policy` sets an *instance* attribute, so a live executor never
        sees a class-level reset; the file then stays missing and every
        `sandbox-exec -f <profile>` fails instantly. Tried, measured, worse than
        the stale policy it replaced.
        """
        source = (REPO_ROOT / "benchmark" / "run.py").read_text(encoding="utf-8")
        body = source[source.index("def reset_sandbox_policy"):]
        body = body[: body.index("\n# ---")]
        assert "SANDBOX_POLICY.write_text" in body
        assert "unlink" not in body, "back to deleting the policy; see the docstring"

    def test_it_survives_a_cuga_without_seatbelt(self, tmp_path, monkeypatch) -> None:
        """Another sandbox mode has no policy to write; that is not an error."""
        import run

        monkeypatch.setattr(run, "SANDBOX_POLICY", tmp_path / "unused.sb")
        monkeypatch.setitem(
            __import__("sys").modules,
            "cuga.backend.cuga_graph.nodes.cuga_lite.executors.native.native_sandbox_executor",
            None,
        )
        run.reset_sandbox_policy()  # must not raise

    def test_it_is_called_after_chdir(self) -> None:
        """Order matters: `_build_policy` reads os.getcwd() at call time, so
        running it before the chdir would authorise the previous case."""
        source = (REPO_ROOT / "benchmark" / "run.py").read_text(encoding="utf-8")
        chdir = source.index("os.chdir(workspace)")
        reset = source.index("reset_sandbox_policy()", chdir)
        assert chdir < reset


#: One file per host. Adding a host means adding it here, which is how these
#: tests stay true as the set grows.
RUNNERS = ("run.py", "claude_run.py", "react_run.py")


class TestEveryHostIsScoredTheSameWay:
    """Three hosts, one corpus, one judge — otherwise the comparison is theatre."""

    def test_every_runner_exists(self) -> None:
        for name in RUNNERS:
            assert (REPO_ROOT / "benchmark" / name).is_file(), f"no benchmark/{name}"

    @pytest.mark.parametrize("name", RUNNERS[1:])
    def test_the_other_runners_import_the_shared_judge(self, name: str) -> None:
        """run.py owns `judge`; every other host has to import it rather than
        write one, or its numbers cannot be compared with CUGA's."""
        source = (REPO_ROOT / "benchmark" / name).read_text(encoding="utf-8")
        assert "from run import" in source and "judge" in source, (
            f"{name} scores with its own rules"
        )

    @pytest.mark.parametrize("name", RUNNERS[1:])
    def test_no_runner_redefines_the_verdict(self, name: str) -> None:
        """A local `def judge` shadows the import silently — the run still
        prints a number, and the number means something else."""
        source = (REPO_ROOT / "benchmark" / name).read_text(encoding="utf-8")
        assert "\ndef judge(" not in source, f"{name} defines its own judge()"

    def test_each_host_gets_its_own_directory(self) -> None:
        benchmark = REPO_ROOT / "benchmark"
        assert 'out_root / "cuga"' in (benchmark / "run.py").read_text(encoding="utf-8")
        assert 'out_root / "claude"' in (benchmark / "claude_run.py").read_text(encoding="utf-8")

        react = (benchmark / "react_run.py").read_text(encoding="utf-8")
        assert 'HOST = "react"' in react and "out_root / HOST" in react

    @pytest.mark.parametrize("name", RUNNERS)
    def test_inputs_are_saved_next_to_outputs(self, name: str) -> None:
        """A number without the question attached cannot be reproduced."""
        source = (REPO_ROOT / "benchmark" / name).read_text(encoding="utf-8")
        assert '"input"' in source, f"{name} does not save the input it used"
        assert '"output"' in source, f"{name} does not collect the deck it produced"

    @pytest.mark.parametrize("name", RUNNERS)
    def test_every_runner_can_be_imported_without_credentials(self, name: str) -> None:
        """Importing must not need a key or a live provider — only the host's
        own library, which is optional and not in Palette's venv.

        Skipped rather than failed when that library is absent, because this
        suite runs under Palette's interpreter and a host's dependency is not
        Palette's problem. Any *other* ImportError is a real break and fails.
        """
        optional = {"langgraph", "langchain", "langchain_core", "langchain_ibm"}
        try:
            __import__(name.removesuffix(".py"))
        except ModuleNotFoundError as exc:
            if exc.name in optional:
                pytest.skip(
                    f"{name} needs {exc.name}, which this interpreter does not have "
                    f"(see agents/requirements.txt)"
                )
            raise


class TestTheReactHostKeepsTheModelHonest:
    """The third host exists because the first two confound scaffold and model.

    CUGA runs gpt-oss-120b, Claude Code runs Claude, so nothing that differs
    between their columns can be attributed to either. That only holds if this
    host reports which model actually answered.
    """

    def test_the_model_is_reported_rather_than_assumed(self) -> None:
        source = (REPO_ROOT / "benchmark" / "react_run.py").read_text(encoding="utf-8")
        assert '"model": options.model' in source

    def test_the_step_budget_is_reported(self) -> None:
        """A run that exhausted its recursion limit looks exactly like an agent
        that gave up. The report has to distinguish them."""
        source = (REPO_ROOT / "benchmark" / "react_run.py").read_text(encoding="utf-8")
        assert '"recursion_limit"' in source

    def test_the_skill_loading_mode_is_reported(self) -> None:
        """Eager loading answers the routing question before the model sees the
        request, which quietly retires the case tagged `routing`. Which mode ran
        has to be on the record."""
        source = (REPO_ROOT / "benchmark" / "react_run.py").read_text(encoding="utf-8")
        assert '"skill_loading"' in source

    def test_the_agent_it_drives_lives_outside_the_benchmark(self) -> None:
        """The runner belongs to the benchmark; the agent does not. Keeping the
        scaffold in agents/ is what lets it be swapped without touching this."""
        assert (REPO_ROOT / "agents" / "palette_react" / "agent.py").is_file()

    def test_the_claude_runner_detects_rather_than_assumes_a_cli(self) -> None:
        """There is no `claude` binary on this machine; pretending otherwise
        would produce a runner that silently does nothing."""
        source = (REPO_ROOT / "benchmark" / "claude_run.py").read_text(encoding="utf-8")
        assert "shutil.which" in source
        assert "falling back to the manual run sheet" in source
