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
import os
import re
import subprocess
import sys
import time
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

    def test_the_helper_only_calls_commands_palette_has(self) -> None:
        """deck.py fronts palette.py, so *it* is what must stay in step."""
        surface = cli_surface()
        assert surface, "could not read any subcommands out of palette.py"
        called = set(re.findall(r'"(build-plan|edit-plan|build-deck)"', DECK_PY.read_text()))
        assert called, "deck.py calls no palette.py commands at all"
        missing = called - set(surface)
        assert not missing, f"deck.py calls commands palette.py lacks: {sorted(missing)}"

    @pytest.mark.parametrize(
        ("palette_command", "helper_command"),
        [("build-plan", "plan"), ("edit-plan", "edit"), ("build-deck", "start")],
    )
    def test_every_capability_is_reachable(self, palette_command: str, helper_command: str) -> None:
        """A capability with no documented route is one the agent never uses."""
        assert palette_command in cli_surface(), f"palette.py lost {palette_command}"
        assert f"deck.py {helper_command}" in skill_text(), (
            f"{palette_command} has no documented route ({helper_command!r} missing from SKILL.md)"
        )

    def test_the_helper_passes_only_flags_palette_accepts(self) -> None:
        surface = cli_surface()
        source = DECK_PY.read_text()
        problems = []
        for command, flags in surface.items():
            # Only an argv list literal -- `["build-deck", ...]`. The command
            # name also appears as a plain label, and matching that ran on to
            # the next `]` anywhere in the file, borrowing another command's
            # flags and reporting a mismatch that did not exist.
            for match in re.finditer(rf'\["{command}"([^\]]*)\]', source):
                for flag in re.findall(r'"(--[a-z][a-z-]+)"', match.group(1)):
                    if flag not in flags:
                        problems.append(f"{command} {flag}")
        assert not problems, f"deck.py passes flags palette.py rejects: {sorted(set(problems))}"

    def test_the_context_flag_is_documented(self) -> None:
        """Pasted material is the whole point of --context on a chat host with
        no file upload; undocumented, the agent crams it into the request."""
        assert "--context" in cli_surface()["build-plan"]
        assert "--context" in skill_text()
        assert "--context" in DECK_PY.read_text(), "the helper never forwards it"


class TestTheConfirmationGate:
    """Building an unapproved plan wastes minutes and produces the wrong deck."""

    def test_the_gate_is_stated(self) -> None:
        text = skill_text()
        assert "Never start a build until the user has confirmed" in text
        assert "confirm" in text

    def test_the_gate_is_not_optional(self) -> None:
        assert re.search(r"confirmation gate is (required|mandatory)", skill_text(), re.I)


class TestLongBuildsSurviveAStepLimit:
    """build-deck runs for minutes; some hosts kill a step at 120 seconds."""

    def test_the_async_path_is_documented(self) -> None:
        text = skill_text()
        assert "deck.py start" in text and "deck.py status" in text

    def test_status_reports_verified_from_the_filesystem(self) -> None:
        """`done` must never be relayed from an exit code."""
        source = DECK_PY.read_text(encoding="utf-8")
        assert "def verify(" in source
        assert "MIN_PPTX_BYTES" in source
        assert '"verified": true' in skill_text().lower()

    def test_the_skill_forbids_reporting_an_unverified_deck(self) -> None:
        assert "Never report a deck that does not exist" in skill_text()

    def test_a_finished_build_reports_how_long_it_took(self, tmp_path: Path) -> None:
        """Not how long ago it started — polling later would flag a fast build.

        The agent is told to speak up past about fifteen minutes; measuring
        from `now` means revisiting yesterday's deck looks like a stall.
        """
        import json as _json
        import subprocess as _sp
        import time as _time

        import os as _os

        # Built in five minutes, two hours ago, and polled now.
        started = int(_time.time()) - 7200
        pptx = tmp_path / "deck.pptx"
        pptx.write_bytes(b"x" * 60_000)
        _os.utime(pptx, (started + 300, started + 300))
        (tmp_path / ".palette-exit").write_text("0\n")   # the build ended
        (tmp_path / ".palette-build.json").write_text(
            _json.dumps({"state": "running", "pid": 1, "started_at": started,
                         "log": str(tmp_path / "build.log"), "out_dir": str(tmp_path)})
        )
        out = _sp.run([sys.executable, str(DECK_PY), "status", "--out-dir", str(tmp_path), "--hold-seconds", "0"],
                      capture_output=True, text=True)
        payload = _json.loads(out.stdout)
        assert payload["done"] is True
        assert payload["elapsed_seconds"] == 300, (
            f"reported {payload['elapsed_seconds']}s for a five-minute build polled "
            "two hours later — elapsed is measuring wall clock since start, not duration"
        )

    def test_status_reports_something_true_while_running(self) -> None:
        """`progress` is empty for most of a build — build-deck prints at the end.

        Telling the agent to relay a progress line it will never have is an
        instruction it cannot follow, so `elapsed_seconds` and a note carry it.
        """
        source = DECK_PY.read_text(encoding="utf-8")
        assert '"elapsed_seconds": elapsed' in source
        assert "still rendering after" in source
        assert "no per-stage `progress` line" in skill_text()

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


class TestReadingTheUsersReply:
    """After the plan is shown, the next message decides everything.

    A deck is inherently multi-turn: plan, ask, and then whatever the user
    says. "Yes" is only one of the things they say, and the expensive mistakes
    are all in the other three — building on a conditional yes, re-planning
    when they wanted an edit, or building a revision nobody approved.
    """

    @pytest.fixture
    def flowed(self) -> str:
        return " ".join(skill_text().split())

    def test_all_four_kinds_of_reply_are_covered(self, flowed: str) -> None:
        for phrase, why in (
            ("Approval", "no rule for the reply that builds"),
            ("A change", "no rule for an edit instruction"),
            ("A question", "a question about the plan has no handling and may build"),
            ("A different deck", "a new topic would be edited into the old plan"),
        ):
            assert phrase in flowed, f"{why}"

    def test_a_conditional_yes_is_an_edit(self, flowed: str) -> None:
        """"Yes, but make it shorter" builds the deck they just rejected."""
        assert "is a change, not an approval" in flowed
        assert "condition" in flowed

    def test_the_instruction_is_passed_verbatim(self, flowed: str) -> None:
        """edit-plan is a model call; a paraphrase loses what they asked for."""
        assert "verbatim" in flowed
        assert "not your summary of it" in flowed

    def test_an_edit_returns_to_the_gate(self, flowed: str) -> None:
        """A revised plan is an unapproved plan."""
        assert "Never build straight after an edit" in flowed
        assert "back to step 2" in flowed

    def test_pasted_material_after_a_plan_is_an_edit(self, flowed: str) -> None:
        """Otherwise the agent re-plans and discards the reviewed version."""
        assert "If they paste material after a plan exists" in flowed

    def test_only_approval_reaches_the_build(self) -> None:
        """The table must not offer `start` as an outcome of anything else."""
        table = skill_text().split("| They said |")[1].split("\n\n")[0]
        rows = [r for r in table.splitlines() if r.strip().startswith("|")]
        starts = [r for r in rows if "`start`" in r]
        assert len(starts) == 1, f"{len(starts)} rows reach the build; exactly one should"
        assert "Approval" in starts[0]


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

    def test_status_on_an_empty_directory_answers_none(self, tmp_path: Path) -> None:
        """"Nothing here" is an answer, because this is also how you ask.

        This used to exit non-zero with an error, which made it unusable as the
        "have I already started one?" check — and without that check an agent
        with no memory of earlier turns re-runs the confirmation gate every
        turn. Observed live: the user said yes repeatedly for 33 minutes while
        a finished deck sat in the workspace and `status` was never called
        again after `start`.
        """
        result = subprocess.run(
            [sys.executable, str(DECK_PY), "status", "--out-dir", str(tmp_path), "--hold-seconds", "0"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, "a check that errors cannot be used as a check"
        payload = json.loads(result.stdout)
        assert payload["state"] == "none" and payload["done"] is False
        assert str(tmp_path) in payload["out_dir"], "say which directory you looked in"

    def test_status_is_safe_to_call_before_anything_exists(self, tmp_path: Path) -> None:
        """Step 0 runs on a workspace that may not have a deck directory yet."""
        result = subprocess.run(
            [sys.executable, str(DECK_PY), "status", "--out-dir", str(tmp_path / "deck"), "--hold-seconds", "0"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout)["state"] == "none"

    def test_find_reports_a_plan_with_no_build(self, tmp_path: Path) -> None:
        """The half of "where am I" that `status` cannot answer.

        `status` only knows about builds. An agent that forgot it drafted a
        plan would see `none`, draft another, and silently discard the version
        the user had already read.
        """
        (tmp_path / "plan.md").write_text("# a plan\n")
        (tmp_path / ".plan.md.plan.json").write_text(json.dumps({"state": "done"}))
        result = subprocess.run(
            [sys.executable, str(DECK_PY), "find", "--root", str(tmp_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        plans = [f for f in payload["found"] if f["kind"] == "plan"]
        assert len(plans) == 1 and plans[0]["written"] is True
        assert "no finished deck" in payload["note"]

    def test_find_reports_a_finished_deck(self, tmp_path: Path) -> None:
        out = tmp_path / "deck"
        out.mkdir()
        (out / "deck.pptx").write_bytes(b"x" * 200_000)
        (out / ".palette-exit").write_text("0\n")
        (out / ".palette-build.json").write_text(json.dumps({"state": "running", "pid": 1}))
        result = subprocess.run(
            [sys.executable, str(DECK_PY), "find", "--root", str(tmp_path)],
            capture_output=True, text=True,
        )
        payload = json.loads(result.stdout)
        builds = [f for f in payload["found"] if f["kind"] == "build"]
        assert builds and builds[0]["state"] == "done"
        assert "1 finished deck" in payload["note"]

    def test_find_is_quiet_about_an_empty_tree(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(DECK_PY), "find", "--root", str(tmp_path)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout)["found"] == []

    def test_find_needs_no_checkout(self, tmp_path: Path) -> None:
        """It reads output directories, so it must work with no $PALETTE_HOME.

        The shell loop this replaces exported PALETTE_HOME as though it were
        needed. It never was, and pretending otherwise makes the check look
        harder to run than it is.
        """
        env = {k: v for k, v in os.environ.items() if k != "PALETTE_HOME"}
        result = subprocess.run(
            [sys.executable, str(DECK_PY), "find", "--root", str(tmp_path)],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, result.stderr

    def test_the_skill_tells_the_agent_to_look_before_it_asks(self) -> None:
        """The fix for the confirmation loop, pinned.

        Nothing in the skill used to describe a build that already exists, so
        every turn started from "ask the user to confirm the plan". The user
        answering "yes" again could not break the loop, because the loop was
        not waiting on them.
        """
        text = skill_text()
        assert "Step 0" in text, "the workflow does not begin by checking for existing work"
        for state in ("none", "running", "done", "error"):
            assert f"`{state}`" in text, f"step 0 does not say what to do when state is {state}"
        # Prose wraps, so compare on collapsed whitespace rather than pinning
        # the line breaks a future edit is free to move.
        flowed = " ".join(text.split())
        assert "never ask for that confirmation twice" in flowed
        assert "repeating themselves means you missed something on disk" in flowed

    def test_start_refuses_a_missing_plan(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(DECK_PY), "start",
             "--plan", str(tmp_path / "nope.md"), "--out-dir", str(tmp_path / "out")],
            capture_output=True, text=True,
        )
        assert result.returncode != 0 and "no plan at" in result.stderr

    def _status_of(self, out_dir: Path, pid: int) -> dict:
        """Run `status` against a hand-written state file naming *pid*."""
        (out_dir / "build.log").write_text("rendered 5 slides\n")
        (out_dir / ".palette-build.json").write_text(json.dumps(
            {"state": "running", "pid": pid, "log": str(out_dir / "build.log"),
             "out_dir": str(out_dir), "started_at": int(time.time()) - 90}
        ))
        result = subprocess.run(
            [sys.executable, str(DECK_PY), "status", "--out-dir", str(out_dir), "--hold-seconds", "0"],
            capture_output=True, text=True,
        )
        return json.loads(result.stdout)

    def test_a_live_build_is_not_done_even_with_a_full_pptx_on_disk(
        self, tmp_path: Path
    ) -> None:
        """The regression this file exists for, in its second form.

        `build-deck` renders, lints the geometry, and re-renders to the *same
        path* until the layout settles — three passes on a plain five-slide
        deck, the last still fixing overflows. So a complete, valid,
        correctly-sized .pptx sits on disk for minutes while the build is
        still working on it.

        Reporting that as done hands the user a pre-repair deck, or a torn
        read of a zip being rewritten underneath them. Liveness wins over the
        filesystem; the filesystem only gets to speak once the process is gone.
        """
        (tmp_path / "deck.pptx").write_bytes(b"x" * 200_000)
        for n in range(1, 6):
            (tmp_path / f"slide-{n}.png").write_bytes(b"\x89PNG")

        # A real live process whose command line looks like a build, so this
        # exercises `_alive` for real rather than mocking the thing under test.
        fake = tmp_path / "palette.py"
        fake.write_text("import time\ntime.sleep(60)\n")
        build = subprocess.Popen([sys.executable, str(fake), "build-deck"])
        try:
            result = self._status_of(tmp_path, build.pid)
        finally:
            build.kill()
            build.wait()

        assert result["state"] == "running", "a live build must never report done"
        assert result["done"] is False
        assert "pptx" not in result, (
            "a path handed over mid-build points at a deck still being rewritten"
        )

    def test_a_finished_build_is_done_once_the_process_is_gone(self, tmp_path: Path) -> None:
        """The other direction: a dead pid must not hold a real deck hostage."""
        (tmp_path / "deck.pptx").write_bytes(b"x" * 200_000)
        result = self._status_of(tmp_path, 999_999_999)  # certainly not running
        assert result["state"] == "done" and result["done"] is True

    def test_a_sandbox_that_hides_processes_does_not_fail_a_live_build(
        self, tmp_path: Path
    ) -> None:
        """The bug that broke every CUGA run, and the reason `.palette-exit` exists.

        CUGA runs each step under Seatbelt with `(allow signal (target self))`.
        `os.kill(pid, 0)` against the detached build therefore raises
        PermissionError, and `ps` cannot exec at all. Both were being read as
        "the process is gone", so `status` returned `state: error` on the first
        poll -- minutes before the .pptx could exist -- and the agent went off
        and invented missing dependencies to explain a failure that had not
        happened. Two real sessions ended that way with a perfectly good
        15-slide deck sitting in the workspace.

        pid 1 stands in for the sandbox: it is alive, and signalling it raises
        PermissionError for anyone not root. Undetermined must mean "keep
        waiting", never "it died".
        """
        module = self._deck_module()
        assert os.getuid() != 0, "run this as a normal user; root can signal pid 1"
        assert module._alive(1) is None, "an undeterminable pid must not read as dead"

        result = self._status_of(tmp_path, 1)   # no .palette-exit, no .pptx yet
        assert result["state"] == "running", (
            "a build whose liveness cannot be probed was declared failed"
        )

    def test_the_exit_file_is_what_ends_the_wait(self, tmp_path: Path) -> None:
        """Same unprobeable pid, but the build has now recorded that it ended."""
        (tmp_path / "deck.pptx").write_bytes(b"x" * 200_000)
        (tmp_path / ".palette-exit").write_text("0\n")
        result = self._status_of(tmp_path, 1)
        assert result["state"] == "done" and result["done"] is True

    def test_a_failed_build_reports_its_exit_code(self, tmp_path: Path) -> None:
        """Ended, nothing usable on disk: that is an error, and the code helps."""
        (tmp_path / ".palette-exit").write_text("1\n")
        result = self._status_of(tmp_path, 1)
        assert result["state"] == "error" and result["exit_code"] == 1
        assert result["verified"] is False

    def test_the_shipped_skill_is_the_same_on_every_machine(self) -> None:
        """No absolute path, no host config, nothing generated at install time.

        This is the line between "configuration" and an anti-pattern. Where
        Palette lives is a per-machine fact, so it belongs in the environment
        (`$PALETTE_HOME`) exactly like `$JAVA_HOME` — never baked into the
        skill. If installing wrote a path in here, every install would produce
        a different folder: the `dirHash` in the cuga-skills catalog could not
        verify, the tarball would be machine-specific, and "one copy, owned by
        Palette" would quietly become one copy per machine.
        """
        for path in sorted(SKILL_DIR.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for marker in ("/Users/", "/home/", "/opt/homebrew", "/private/tmp"):
                assert marker not in text, (
                    f"{path.name} contains the machine-specific path {marker!r}; "
                    "the shipped skill must be byte-identical everywhere"
                )

    def test_the_plan_step_cannot_be_cut_short(self) -> None:
        """`plan` detaches, so a capped step can never kill the work.

        Measured 43-82s locally and 105-170s inside CUGA's sandbox, against a
        120s step. When the step was cut short the agent reported that Palette
        had timed out and gave up — while `plan.md`, a perfectly good 72-line
        plan, was written to the workspace a few seconds later.
        """
        source = DECK_PY.read_text()
        assert "def plan_status" in source, "no way to collect a slow plan"
        assert "_start_plan" in source
        skill = skill_text()
        assert "plan-status" in skill, "SKILL.md never says how to collect a slow plan"

    def test_the_plan_step_does_not_cost_a_poll_per_turn(self) -> None:
        """...but it must not be *polled* either, which is the other failure.

        A plan is one model call. Detaching and polling it costs an agent turn
        per poll — eight round trips for a 160s plan — and those turns come out
        of the same step budget the build needs later. So `plan` holds the call
        open first, and only hands back to `plan-status` if that runs out.
        """
        source = DECK_PY.read_text()
        assert "hold_seconds" in source, "there is no bounded wait; every plan is polled"
        assert "def _start_plan" in source
        body = source[source.index("def _start_plan") : source.index("\ndef plan(")]
        assert "_finished_at" in body, "the hold does not check for completion"
        assert "plan_status(" in body, (
            "a plan that finishes inside the hold should return the same payload "
            "plan-status would, so the agent needs no second call"
        )

        flowed = " ".join(skill_text().split())
        assert "usually answers in one call" in flowed, (
            "SKILL.md still teaches the agent to poll for every plan"
        )

    def test_every_slow_command_records_its_exit_code(self) -> None:
        """Both detached paths must write the sentinel, or polling never ends."""
        source = DECK_PY.read_text()
        assert source.count("echo $? >") >= 1, "no exit sentinel is written at all"
        assert "_detach(" in source, "the detach path is not shared, so one of them will drift"

    def test_a_recycled_pid_does_not_poll_forever(self, tmp_path: Path) -> None:
        """Liveness now gates completion, so `_alive` must mean *our* build.

        A bare `os.kill(pid, 0)` is true for whatever process inherited the
        number, which would leave the agent polling a finished deck forever.
        """
        module = self._deck_module()
        assert module._alive(os.getpid()) is False, (
            "this pytest process is alive but is not a palette build"
        )
        assert module._alive(-1) is False


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

    def test_the_skill_forbids_running_palette_py_directly(self) -> None:
        """An agent that tried it ran `skills/palette/palette.py` — the wrong root.

        palette.py lives in the checkout and only runs with that as its cwd;
        deck.py exists so nobody has to hold both facts at once.
        """
        assert "Do not run `palette.py` yourself" in skill_text()


class TestTheAgentDoesNotStallOnSetup:
    """Observed in the benchmark: the agent asked the user for $PALETTE_HOME
    and $RITS_API_KEY and stopped, with both already set in its environment.

    A sandboxed agent cannot read the parent environment, so "check the
    variables first" is not a thing it can do — it can only ask, which costs
    the user a turn and produces an answer that helps nobody. The command
    itself already fails with the variable named.
    """

    def test_the_skill_forbids_asking_for_the_variables(self) -> None:
        flowed = " ".join(skill_text().split())
        assert "Never ask the user for them" in flowed
        assert "never check them first" in flowed

    def test_the_skill_says_to_just_run_it(self) -> None:
        flowed = " ".join(skill_text().split())
        assert "Just run the command" in flowed, (
            "nothing tells the agent what to do instead of asking"
        )

    def test_the_failure_path_is_still_documented(self) -> None:
        """Not asking is only safe because the command reports it clearly."""
        flowed = " ".join(skill_text().split())
        assert "relay that message and stop" in flowed


class TestASecondPlanIsNeverStarted:
    """Measured: `plan` twice, then `edit` twice, and a 4-slide deck for a
    request that said 3 — two writers racing the same file.

    When the hold expires the agent is told to poll, and re-runs the command
    instead, because re-running is what it just did and knows how to do. The
    build path has guarded against this from the start; this is the same guard
    for the plan.
    """

    def _module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("deck_guard", DECK_PY)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_a_live_plan_is_collected_not_restarted(self, tmp_path: Path) -> None:
        plan = tmp_path / "plan.md"
        # A real process whose command line looks like a build, so _alive says
        # "ours" rather than guessing.
        fake = tmp_path / "palette.py"
        fake.write_text("import time\ntime.sleep(30)\n")
        running = subprocess.Popen([sys.executable, str(fake), "build-plan"])
        try:
            (tmp_path / ".plan.md.plan.json").write_text(
                json.dumps({"state": "running", "pid": running.pid,
                            "plan": str(plan), "started_at": int(time.time())})
            )
            module = self._module()
            import argparse as _ap

            called = []
            module.plan_status = lambda args: called.append(args) or 0
            module._detach = lambda *a, **k: pytest.fail("a second plan was started")

            module._start_plan(tmp_path, ["build-plan", "x"], plan, "build-plan", 0)
            assert called, "a running plan was neither collected nor started"
        finally:
            running.kill()
            running.wait()

    def test_the_handoff_tells_the_agent_not_to_rerun(self) -> None:
        source = DECK_PY.read_text(encoding="utf-8")
        assert "do NOT run" in source, (
            "the running response does not warn against re-running the command"
        )


class TestAWrongPathIsSelfCorrecting:
    """Measured: the agent polled `--out ./skills/palette/SKILL.md` twenty-three
    times. Each call raised instantly, so retrying was free, and nothing in the
    refusal said which path it should have used.

    A wrong argument is a question the tool can answer — it knows where the
    plans are — so it answers instead of crashing.
    """

    def test_it_names_the_plans_that_do_exist(self, tmp_path: Path) -> None:
        real = tmp_path / "work"
        real.mkdir()
        (real / "plan.md").write_text("# plan\n")
        (real / ".plan.md.plan.json").write_text(json.dumps({"state": "done"}))

        result = subprocess.run(
            [sys.executable, str(DECK_PY), "plan-status",
             "--out", str(tmp_path / "SKILL.md"), "--hold-seconds", "0"],
            capture_output=True, text=True, cwd=str(tmp_path),
        )
        assert result.returncode == 0, "a wrong path must not be a hard failure"
        payload = json.loads(result.stdout)
        assert payload["state"] == "none"
        assert any("plan.md" in p for p in payload["plans_here"]), (
            "it did not point at the plan that actually exists"
        )
        assert "plan.md" in payload["next"]

    def test_it_suggests_starting_one_when_there_are_none(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [sys.executable, str(DECK_PY), "plan-status",
             "--out", str(tmp_path / "nope.md"), "--hold-seconds", "0"],
            capture_output=True, text=True, cwd=str(tmp_path),
        )
        payload = json.loads(result.stdout)
        assert payload["plans_here"] == []
        assert "deck.py plan --request" in payload["next"]


class TestTheAgentDoesNotSubstituteItself:
    """Measured: the agent loaded the skill, wrote a two-slide outline in prose,
    asked for approval, and never ran a command — then reported a permission
    error it had invented.

    An outline written in chat is not the plan. `build-deck` renders `plan.md`,
    so the user approves one artifact and receives a different one.
    """

    def test_writing_the_plan_yourself_is_forbidden(self) -> None:
        flowed = " ".join(skill_text().split())
        assert "You do not write the plan" in flowed
        assert "Never draft an outline yourself" in flowed

    def test_it_says_why_rather_than_just_forbidding(self) -> None:
        """A rule with a reason survives paraphrase; a bare prohibition does not."""
        flowed = " ".join(skill_text().split())
        assert "renders whatever is in `plan.md`" in flowed

    def test_inventing_a_failure_is_forbidden(self) -> None:
        flowed = " ".join(skill_text().split())
        assert "relay its error verbatim" in flowed
        assert "Do not describe a failure you did not see" in flowed
