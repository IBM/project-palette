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
    assert "never end your turn to ask whether to keep" in skill, (
        "nothing stops the agent handing the polling back to the user mid-build"
    )
    assert "--pause-after-plan" in skill, (
        "'show me the plan first' must route through deck, not the raw primitives"
    )


def test_guide_and_testing_agree_on_the_install_extra() -> None:
    """Both walkthroughs must name the same extra, or one of them wastes an hour."""
    for doc in (PACKAGE / "GUIDE.md", PACKAGE / "TESTING.md"):
        text = doc.read_text(encoding="utf-8")
        assert "'.[dev]'" in text, f"{doc.name} does not tell you to install .[dev]"


def test_every_doc_is_linked_from_somewhere() -> None:
    """An unlinked doc is one nobody finds."""
    corpus = "\n".join(d.read_text(encoding="utf-8") for d in DOCS)
    for doc in (PACKAGE / "GUIDE.md", PACKAGE / "TESTING.md", PACKAGE / "README.md"):
        assert doc.name in corpus, f"{doc.name} is not linked from any other doc"
