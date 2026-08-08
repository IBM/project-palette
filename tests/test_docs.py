"""The docs have to stay true, like everything else here.

Most of what this file used to guard is gone with the HTTP-era skill: there is
no generated payload, no per-host rendering, no client package to document. The
two guards that still earn their keep are the ones that caught real breakage —
a `make` target a doc tells you to run but which does not exist, and a relative
link that resolves to nothing.

Deliberately no line or test counts. Numbers like that go stale on the next
commit and train you to ignore the failure.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

DOCS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "SKILL.md",
    REPO_ROOT / "skills" / "palette" / "SKILL.md",
]


def make_targets() -> set[str]:
    return set(re.findall(r"^([a-z][\w-]*):.*?##", (REPO_ROOT / "Makefile").read_text(), re.M))


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_doc_exists(doc: Path) -> None:
    assert doc.is_file()


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_make_targets_in_fenced_commands_exist(doc: Path) -> None:
    """Only inside code fences — prose says things like "make sure" and "make slides"."""
    text = doc.read_text(encoding="utf-8")
    fenced = "\n".join(re.findall(r"```[a-z]*\n(.*?)```", text, re.DOTALL))
    unknown = set(re.findall(r"^\s*make ([a-z][\w-]+)", fenced, re.M)) - make_targets()
    assert not unknown, f"{doc.name} tells you to run non-existent target(s): {sorted(unknown)}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_relative_links_resolve(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    broken = [
        target
        for target in re.findall(r"\]\(([^)#:]+\.md)[^)]*\)", text)
        if not (doc.parent / target).resolve().is_file()
    ]
    assert not broken, f"{doc.name} links to missing file(s): {broken}"


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_doc_still_describes_the_http_skill(doc: Path) -> None:
    """Palette is driven as a CLI now; the HTTP client and its server are gone.

    An agent told to poll `/progress` or install a `palette_skill` wheel follows
    instructions for software that no longer exists, and the failure looks like
    the agent's fault rather than the doc's.
    """
    text = doc.read_text(encoding="utf-8")
    for banned, why in (
        ("palette-skill deck", "the orchestrator was part of the deleted HTTP client"),
        ("PALETTE_URL", "there is no server for the skill to point at"),
        ("build_async", "the async HTTP routes are not how the skill works"),
        ("vendor/palette_skill", "the skill is a folder; there is no wheel to vendor"),
    ):
        assert banned not in text, f"{doc.name} still describes the HTTP skill: {banned!r} ({why})"


def test_the_two_skill_files_do_not_contradict_each_other() -> None:
    """`SKILL.md` at the root and `skills/palette/SKILL.md` are both real.

    The root one is the reference copy; the folder is what ships. They may
    differ in detail — only the packaged one carries the host-specific
    long-build guidance — but they must not disagree about the commands, or
    whichever a reader finds first is a coin flip.
    """
    root = (REPO_ROOT / "SKILL.md").read_text(encoding="utf-8")
    shipped = (REPO_ROOT / "skills" / "palette" / "SKILL.md").read_text(encoding="utf-8")
    for command in ("build-plan", "edit-plan", "build-deck"):
        assert (f"palette.py {command}" in root) == (f"palette.py {command}" in shipped), (
            f"the two SKILL.md files disagree about {command!r}"
        )
    for text, name in ((root, "SKILL.md"), (shipped, "skills/palette/SKILL.md")):
        assert "--context" in text, f"{name} omits --context, so pasted material gets mangled"
