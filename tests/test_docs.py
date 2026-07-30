"""The documentation has to stay true, like everything else here.

Docs rot in ways nobody notices until a reader follows them and fails. These
check the claims that can be verified mechanically:

  * every module has a row in the reference tables
  * every `make` target a doc tells you to run actually exists
  * every relative link resolves
  * no doc still teaches guidance we have since reversed

They deliberately do **not** check line counts or test counts. Numbers like
that go stale on the very next commit and train you to ignore the failure —
so the docs no longer carry them.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PACKAGE = REPO_ROOT / "palette_skill"

DOCS = [
    REPO_ROOT / "README.md",
    PACKAGE / "README.md",
    PACKAGE / "GUIDE.md",
    PACKAGE / "TESTING.md",
    PACKAGE / "CHEATSHEET.md",
    PACKAGE / "payload" / "SKILL.md",
    PACKAGE / "payload" / "reference.md",
]

#: Docs carrying a "| `module.py` | role |" reference table.
TABLE_DOCS = [PACKAGE / "README.md", PACKAGE / "GUIDE.md"]


def make_targets() -> set[str]:
    return set(re.findall(r"^([a-z][\w-]*):.*?##", (REPO_ROOT / "Makefile").read_text(), re.M))


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_doc_exists(doc: Path) -> None:
    assert doc.is_file()


@pytest.mark.parametrize("doc", TABLE_DOCS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_module_table_covers_every_module(doc: Path) -> None:
    """A new module with no row is a module nobody discovers."""
    listed = set(re.findall(r"\| `(\w+\.py)` \|", doc.read_text(encoding="utf-8")))
    actual = {p.name for p in PACKAGE.glob("*.py")} - {"__init__.py"}
    missing = actual - listed
    assert not missing, f"{doc.name} has no row for {sorted(missing)}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_make_targets_in_fenced_commands_exist(doc: Path) -> None:
    """Only inside code fences — prose says things like "make sure" and "make slides"."""
    text = doc.read_text(encoding="utf-8")
    fenced = "\n".join(re.findall(r"```[a-z]*\n(.*?)```", text, re.DOTALL))
    referenced = set(re.findall(r"^\s*make ([a-z][\w-]+)", fenced, re.M))
    unknown = referenced - make_targets()
    assert not unknown, f"{doc.name} tells you to run non-existent target(s): {sorted(unknown)}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_relative_links_resolve(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    broken = []
    for target in re.findall(r"\]\(([^)#:]+\.md)[^)]*\)", text):
        if not (doc.parent / target).resolve().is_file():
            broken.append(target)
    assert not broken, f"{doc.name} links to missing file(s): {broken}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_reversed_guidance(doc: Path) -> None:
    """Advice we removed must not survive in a doc somewhere.

    Each of these was a real failure: an agent given a `--base-url <URL>`
    placeholder copies it literally, and one told to check `$PALETTE_URL`
    stops and asks for a URL it could have resolved itself.
    """
    text = doc.read_text(encoding="utf-8")
    for banned, why in (
        ("--base-url <URL>", "placeholder URL — agents copy it literally"),
        ("--base-url <PALETTE_URL>", "placeholder URL — agents copy it literally"),
        ("pip install -e '.[server]'   # server + client dependencies",
         "[server] omits pytest; the dev-machine install is .[dev]"),
        ("two to four minutes",
         "measured builds run to fifteen-plus; under-promising makes a normal "
         "build look like a stall and agents give up on it"),
        ("--max-seconds 25",
         "25s makes a long build cost twenty-odd polls, which agents abandon; "
         "the window comes from the host profile's poll_seconds"),
    ):
        assert banned not in text, f"{doc.name} still teaches: {banned!r} ({why})"


def test_skill_forbids_the_two_ways_a_deck_goes_missing() -> None:
    """Both were observed in live runs, and both read as reasonable behaviour.

    An agent that hand-drives ``start-draft``/``wait-draft`` loses the session
    on retry and reports a deck nobody built. An agent that stops mid-build to
    ask whether to keep polling ends the run on hosts that treat plain prose as
    a final answer — leaving a deck finished on the server and never collected.
    Neither is a lie, which is why only an explicit prohibition catches them.
    """
    skill = (PACKAGE / "payload" / "SKILL.md").read_text(encoding="utf-8")
    assert "Never assemble" in skill, "nothing stops the agent hand-driving the primitives"
    assert "every turn you take must contain a `deck` call" in skill, (
        "nothing stops the agent narrating progress instead of polling for it"
    )
    assert "--pause-after-plan" in skill, (
        "'show me the plan first' must route through deck, not the raw primitives"
    )
    assert "Do **not** pause" in skill, (
        "nothing distinguishes 'show me, then build it' (permission already given) "
        "from 'let me approve first' (a gate) — an agent that pauses on the former "
        "waits forever for a confirmation nobody intended to withhold"
    )


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_referenced_skill_sections_exist(doc: Path) -> None:
    """"reread the `## Workflow` section" is useless once that section is renamed.

    Cross-references to SKILL.md headings are exactly the kind of detail nobody
    re-checks during a rewrite, and a reader following one lands nowhere.
    """
    def flat(value: str) -> str:
        # A reference wrapped across two lines is still the same reference.
        return " ".join(value.split())

    skill = (PACKAGE / "payload" / "SKILL.md").read_text(encoding="utf-8")
    headings = {flat(h) for h in re.findall(r"^(##+ .+)$", skill, re.M)}
    text = doc.read_text(encoding="utf-8")
    referenced = {flat(r) for r in re.findall(r"`(##+ [^`]+)`", text)}
    missing = referenced - headings
    assert not missing, f"{doc.name} points at SKILL.md section(s) that do not exist: {sorted(missing)}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_documented_imports_actually_import(doc: Path) -> None:
    """Run every `from palette_skill import ...` the docs print.

    `run_deck` was documented in reference.md before it was exported, so the
    first line of the example a reader would copy raised ImportError. Nothing
    else here could have caught that: the symbol existed, the prose was right,
    and only the import path was wrong.
    """
    text = doc.read_text(encoding="utf-8")
    for names in re.findall(r"^from palette_skill import ([^\n(]+)$", text, re.M):
        wanted = [n.strip() for n in names.split(",") if n.strip()]
        # Do the import for real rather than probing with hasattr: a submodule
        # like `build_skill` imports fine but is not an attribute of the
        # package until something has imported it, so hasattr answers a
        # different question than the reader's copy-paste asks.
        module = __import__("palette_skill", fromlist=wanted)
        for name in wanted:
            assert getattr(module, name, None) is not None, (
                f"{doc.name} shows `from palette_skill import {name}`, "
                f"but that import fails — the example breaks on line one"
            )


def test_reference_documents_the_orchestrator() -> None:
    """reference.md claims to be the full client surface, so it must hold `run_deck`.

    It was absent for a while, which left the one function an agent should
    reach for first documented only in SKILL.md prose — and reference.md is
    what a reader consults when SKILL.md is not enough.
    """
    text = (PACKAGE / "payload" / "reference.md").read_text(encoding="utf-8")
    for expected in ("run_deck", "pause_after_plan", "approve", ".palette-deck.json", "verified"):
        assert expected in text, f"reference.md does not document {expected!r}"


def test_cheatsheet_covers_the_preset_settings_cuga_changes() -> None:
    """A reader who does not know these two are raised cannot reproduce a run.

    Both were paid for in failed runs, and both are invisible unless documented
    — nothing in the UI says the step length or the auto-continue rule changed.
    The cheatsheet is where operational facts live; the guide links to it.
    """
    sheet = (PACKAGE / "CHEATSHEET.md").read_text(encoding="utf-8")
    for setting in ("sandbox_execution_timeout", "cuga_lite_nl_auto_continue"):
        assert setting in sheet, f"CHEATSHEET.md never mentions {setting}, which demo_palette raises"


def test_makefile_never_shells_out_to_a_bare_interpreter() -> None:
    """A recipe running bare `pip`/`python` uses whatever env happens to be active.

    `uv venv` creates a venv with no `pip` of its own, so a bare `pip install`
    resolves to Homebrew's — or to another project's activated venv — and puts
    Palette's dependencies somewhere nobody intended. That is not hypothetical:
    `make install` did exactly this, and failed for someone who had a different
    project's environment active.

    Recipes must go through `$(PY)`, an explicit `.venv/bin/...`, or `uv pip
    --python`. Comment lines are exempt; they discuss the problem.
    """
    recipes = [
        line
        for line in (REPO_ROOT / "Makefile").read_text(encoding="utf-8").splitlines()
        if line.startswith("\t") and not line.lstrip("\t@").startswith("#")
    ]
    offenders = [
        line.strip()
        for line in recipes
        if re.search(r"(?<![\w./$(-])(pip|python3?)\b", line)
        and "$(PY)" not in line
        and ".venv/bin/" not in line
        and "uv pip" not in line
        and "uv venv" not in line
    ]
    assert not offenders, (
        "Makefile recipes invoke a bare interpreter, which resolves outside "
        f".venv: {offenders}"
    )


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_doc_teaches_an_install_that_omits_the_package(doc: Path) -> None:
    """`-r requirements.txt` installs the deps but not this package.

    The console scripts then never appear and every documented `palette-skill`
    command fails with "command not found". The root README taught this for a
    while, and also described `make install` as running it — a claim that went
    stale the moment the target was fixed. Docs that describe *what a target
    does* rot silently; docs that just name the target do not.
    """
    text = doc.read_text(encoding="utf-8")
    for banned, why in (
        ("uv pip install -r requirements.txt",
         "installs dependencies but not the package, so palette-skill is missing"),
        ("pip install -r requirements.txt && npm install",
         "describes a `make install` that no longer exists"),
    ):
        assert banned not in text, f"{doc.name} teaches: {banned!r} — {why}"


def test_clean_targets_point_at_a_rebuild_that_works() -> None:
    """The line printed after distclean is the next thing anyone types.

    It used to say `uv pip install -e '.[server]'` — the wrong extra (no
    pytest) and a command that never installs the package itself, so
    `palette-skill` stays missing. Someone following it lands on
    "command not found" with no idea why.
    """
    makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
    tail = makefile[makefile.index("distclean:") :]
    assert "'.[server]'" not in tail, (
        "distclean tells you to rebuild with the extra that omits pytest"
    )
    assert "make install" in tail, "distclean does not say how to rebuild"


def test_poll_window_fits_inside_every_host_step() -> None:
    """The poll must fit one step, with room for interpreter start-up.

    Too large and every call is killed mid-poll; too small and a ten-minute
    build costs twenty-odd steps, which agents abandon around forty. Both were
    observed. CUGA's 100s pairs with the 120s step that
    `_apply_palette_supervisor_env` sets — change one and this fails.
    """
    from palette_skill import hosts

    assert hosts.get("cuga").poll_seconds == 100, (
        "CUGA's poll must stay matched to the preset's 120s sandbox_execution_timeout"
    )
    for host in hosts.HOSTS.values():
        assert 25 <= host.poll_seconds <= 240, f"{host.key}: implausible poll window"


def test_docs_that_install_agree_on_the_extra() -> None:
    """Every doc that names an extra must name the same one.

    `.[server]` omits pytest, so a reader who follows the wrong doc gets a
    working server and no test runner, and discovers it much later. Only docs
    that actually carry install commands are checked — the guide now delegates
    those to the cheatsheet and legitimately names none.
    """
    for doc in (PACKAGE / "CHEATSHEET.md", PACKAGE / "TESTING.md", REPO_ROOT / "README.md"):
        text = doc.read_text(encoding="utf-8")
        assert "'.[dev]'" in text, f"{doc.name} does not tell you to install .[dev]"


def test_every_doc_is_linked_from_somewhere() -> None:
    """An unlinked doc is one nobody finds."""
    corpus = "\n".join(d.read_text(encoding="utf-8") for d in DOCS)
    for doc in (
        PACKAGE / "GUIDE.md",
        PACKAGE / "TESTING.md",
        PACKAGE / "README.md",
        PACKAGE / "CHEATSHEET.md",
    ):
        assert doc.name in corpus, f"{doc.name} is not linked from any other doc"


def test_only_the_cheatsheet_carries_the_full_run_sequence() -> None:
    """One runnable copy of the loop, so a changed command is one edit.

    Before this, `cuga start demo_palette` appeared in five documents and every
    core instruction in four to seven. Nothing was wrong on the day it was
    written; the drift arrives later, one doc at a time, and the existing gates
    cannot see it — they check that a command *exists*, not that two docs agree
    on which command to run. The root README described a `make install` that
    had been changed an hour earlier, and that is the mild version.

    So: exactly one doc may print the start command inside a code fence.
    Others describe the preset, link here, and stay true for free.
    """
    offenders = []
    for doc in DOCS:
        fenced = "\n".join(
            re.findall(r"```[a-z]*\n(.*?)```", doc.read_text(encoding="utf-8"), re.DOTALL)
        )
        if "cuga start demo_palette" in fenced and doc.name != "CHEATSHEET.md":
            offenders.append(doc.name)
    assert not offenders, (
        f"{offenders} print the run command in a code fence. Keep runnable "
        "sequences in CHEATSHEET.md and link to it, or they drift apart."
    )


HANDBOOK = REPO_ROOT / "docs" / "skill-handbook.body.html"
HANDBOOK_PAGE = REPO_ROOT / "docs" / "skill-handbook.html"


class TestHandbook:
    """The one-page HTML overview links to GitHub by branch, so links can rot.

    A relative markdown link fails visibly the moment a file moves. A GitHub
    URL keeps rendering and quietly 404s for whoever clicks it, which is worse,
    and neither the browser nor a reader can tell from the page itself.
    """

    BRANCH = "palette_skill"
    BASE = f"https://github.com/IBM/project-palette/blob/{BRANCH}/"

    def test_it_exists_and_is_outside_the_wheel(self) -> None:
        assert HANDBOOK.is_file()
        # package-data ships palette_skill/payload/*.md only; a 44 KB page has
        # no business in a wheel that lands in every agent sandbox.
        assert "palette_skill" not in HANDBOOK.relative_to(REPO_ROOT).parts

    def test_every_palette_link_points_at_a_file_that_exists(self) -> None:
        text = HANDBOOK.read_text(encoding="utf-8")
        missing = [
            path for path in re.findall(re.escape(self.BASE) + r"([\w./-]+)", text)
            if not (REPO_ROOT / path).exists()
        ]
        assert not missing, f"handbook links to files not in this repo: {sorted(set(missing))}"

    def test_no_link_points_at_the_wrong_branch(self) -> None:
        """This work lives on a branch; /blob/main/ would 404 for every reader."""
        text = HANDBOOK.read_text(encoding="utf-8")
        assert "project-palette/blob/main/" not in text
        assert "cuga-agent/blob/main/" not in text

    def test_it_is_linked_from_the_readme(self) -> None:
        readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert "docs/skill-handbook.html" in readme


class TestHandbookIsServable:
    """The page in docs/ has to work when a static host serves it directly.

    The source fragment carries no doctype, charset or viewport, because the
    Artifact publisher supplies those. Serve that same file from nginx and you
    get quirks mode, mojibake in place of the arrows and em-dashes, and a
    desktop-width page on a phone. So the fragment is the source and the
    servable page is generated from it — one copy of the prose, two outputs.
    """

    def test_the_generated_page_is_current(self) -> None:
        """Same contract as `make skill-check`: regenerate, compare, fail if stale."""
        # Imported by path: the filename has a hyphen, so it is not importable
        # as a module name.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "build_handbook", REPO_ROOT / "scripts" / "build-handbook.py"
        )
        build_handbook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build_handbook)

        assert HANDBOOK_PAGE.read_text(encoding="utf-8") == build_handbook.render(), (
            "docs/skill-handbook.html is stale — run `make handbook` and commit the result"
        )

    def test_it_carries_what_a_browser_needs(self) -> None:
        page = HANDBOOK_PAGE.read_text(encoding="utf-8")
        for needed, why in (
            ("<!doctype html>", "without it the page renders in quirks mode"),
            ('charset="utf-8"', "the arrows and em-dashes become mojibake"),
            ("width=device-width", "a phone gets a desktop-width page"),
            ("<title>", "the browser tab is otherwise the filename"),
        ):
            assert needed in page, f"servable handbook is missing {needed!r} — {why}"

    def test_the_fragment_stays_a_fragment(self) -> None:
        """Adding a skeleton to the source would nest one inside the publisher's."""
        fragment = HANDBOOK.read_text(encoding="utf-8")
        assert "<!doctype" not in fragment.lower()
        assert "<body>" not in fragment.lower()


@pytest.mark.parametrize("doc", DOCS + [REPO_ROOT / "docs" / "skill-handbook.body.html"],
                         ids=lambda p: p.name)
def test_no_doc_tells_you_to_grep_the_wrong_log(doc: Path) -> None:
    """Build events live in per-session logs, not server.log.

    app.py attaches a FileHandler per session; server.log only ever gets
    startup and shutdown. Every doc used to say
    `grep draft_async … /server.log`, which reports nothing for a deck that
    rendered fine — a verification step that lies is worse than none, and this
    one was load-bearing in four documents at once.
    """
    fenced = "\n".join(
        re.findall(r"```[a-z]*\n(.*?)```", doc.read_text(encoding="utf-8"), re.DOTALL)
    ) + doc.read_text(encoding="utf-8")
    for line in fenced.splitlines():
        if "draft_async" in line or "build_async" in line:
            assert "server.log" not in line, (
                f"{doc.name} greps server.log for build events, which never land there: "
                f"{line.strip()[:90]}"
            )
