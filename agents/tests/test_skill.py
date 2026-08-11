"""The skill has to arrive exactly as it was written.

This host exists to measure `SKILL.md`. If the loader reformats it, trims it,
or helpfully appends a hint, the run measures the loader instead — and it does
so invisibly, because the deck still comes out. These tests are the guard.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import tree_digest
from palette_react import skill as skill_loader


class TestTheSkillArrivesUnchanged:
    def test_the_body_is_the_file_minus_its_frontmatter(self, skill_dir: Path, card) -> None:
        """Byte-for-byte. Not 'equivalent', not 'stripped' — identical."""
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        _, expected = skill_loader.parse_frontmatter(text)

        assert card.body == expected
        assert card.body == text[len(text) - len(card.body) :], (
            "the body is not a verbatim tail of SKILL.md — something rewrote it"
        )

    def test_frontmatter_and_body_reconstruct_the_file(self, skill_dir: Path, card) -> None:
        """The strongest form of "nothing was added": the parts put back
        together are the file. The obvious way to 'fix' the relative script path
        in SKILL.md is to append a note to the body — this fails if anyone does,
        which is why the path note lives in the host's own system prompt.
        """
        text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        frontmatter = text[: len(text) - len(card.body)]

        assert frontmatter + card.body == text
        assert frontmatter.startswith("---")
        assert frontmatter.rstrip().endswith("---")

    def test_loading_does_not_write_to_the_skill(self, skills_root: Path) -> None:
        before = tree_digest(skills_root)
        skill_loader.load("palette", skills_root)
        assert tree_digest(skills_root) == before


class TestTheFrontmatter:
    def test_the_routing_fields_are_read(self, card) -> None:
        assert card.name == "palette"
        assert card.description, "no description — a host has nothing to route on"
        # The description is the entire basis for routing in lazy mode, so the
        # trigger vocabulary has to survive the parse.
        assert "deck" in card.description.lower()
        assert "presentation" in card.description.lower()

    def test_the_description_is_one_unwrapped_line(self, card) -> None:
        """SKILL.md folds it across several lines with `>-`; a host that keeps
        the newlines puts a ragged block in the system prompt."""
        assert "\n" not in card.description
        assert "  " not in card.description

    def test_a_folded_scalar_is_joined(self) -> None:
        fields, body = skill_loader.parse_frontmatter(
            "---\nname: demo\ndescription: >-\n  one two\n  three\n---\nbody here\n"
        )
        assert fields == {"name": "demo", "description": "one two three"}
        assert body == "body here\n"

    def test_a_file_without_frontmatter_is_all_body(self) -> None:
        fields, body = skill_loader.parse_frontmatter("# Just a document\n")
        assert fields == {}
        assert body == "# Just a document\n"


class TestWhatTheAgentWillActuallyRun:
    def test_the_scripts_folder_is_found(self, card) -> None:
        assert card.scripts.is_dir()
        assert (card.scripts / "deck.py").is_file(), (
            "the skill's only executable is missing; every case would fail at the "
            "first command"
        )

    def test_the_body_still_teaches_the_commands(self, card) -> None:
        """Not a test of the skill's wording — a test that the parse did not eat
        the half of the file the agent needs."""
        for command in ("plan", "edit", "start", "status", "find"):
            assert f"deck.py {command}" in card.body, (
                f"the {command!r} command is not in the loaded body"
            )


class TestFailuresAreActionable:
    def test_a_missing_palette_home_names_the_variable(self, monkeypatch) -> None:
        monkeypatch.delenv("PALETTE_HOME", raising=False)
        with pytest.raises(skill_loader.SkillNotFound) as caught:
            skill_loader.load("palette")
        assert "PALETTE_HOME" in str(caught.value)
        assert "export" in str(caught.value), "no suggestion of how to fix it"

    def test_a_missing_skill_lists_what_is_there(self, skills_root: Path) -> None:
        with pytest.raises(skill_loader.SkillNotFound) as caught:
            skill_loader.load("nonexistent", skills_root)
        message = str(caught.value)
        assert "nonexistent" in message
        assert "palette" in message, "the error does not say which skills exist"

    def test_palette_home_is_used_when_no_root_is_given(
        self, monkeypatch, skills_root: Path
    ) -> None:
        monkeypatch.setenv("PALETTE_HOME", str(skills_root.parent))
        assert skill_loader.load("palette").directory == skills_root / "palette"
        assert os.environ["PALETTE_HOME"] == str(skills_root.parent)
