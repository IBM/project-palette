"""Read a skill folder the way a skill loader does — and change nothing.

A skill is two things on disk: a `SKILL.md` whose YAML frontmatter carries the
`name` and `description` a host uses for routing, and a `scripts/` folder the
agent runs. Hosts that have a loader (CUGA, Claude Code) discover that folder
and hand the model the description first, the body only once it commits.

LangGraph has no such concept, so this module is the loader. It is deliberately
the *only* place that touches the skill, and it opens those files read-only:
the point of benchmarking a skill is measuring the skill as shipped, and a host
that edits it on the way in is measuring itself.

`SkillCard.body` is the file's bytes after the frontmatter, unmodified. Nothing
is appended, rewritten, or reformatted — see `tests/test_skill.py`, which pins
it against the file on disk.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

#: Frontmatter is the leading `---` block. Parsed with a regex rather than a
#: YAML dependency: two scalar fields are needed and a parser would be the only
#: reason this package required PyYAML.
_FRONTMATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


class SkillNotFound(RuntimeError):
    """Raised with the paths that were tried, never a bare failure."""


@dataclass(frozen=True)
class SkillCard:
    """One skill, as a host sees it."""

    name: str
    description: str
    #: SKILL.md after the frontmatter, byte-for-byte.
    body: str
    #: The folder holding SKILL.md. The instructions reference their own
    #: scripts by a path relative to a skills root, so a host that puts the
    #: skill somewhere else has to say where — that is the host's job, not a
    #: change to the skill.
    directory: Path

    @property
    def scripts(self) -> Path:
        return self.directory / "scripts"


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """(fields, body). Body is everything after the frontmatter block, verbatim.

    Only the scalar and folded (`>-`) forms Palette's SKILL.md actually uses are
    supported. An unparseable field is left out rather than guessed at.
    """
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text

    fields: dict[str, str] = {}
    key: str | None = None
    folded: list[str] = []

    def flush() -> None:
        if key is not None:
            fields[key] = " ".join(" ".join(folded).split())

    for line in match.group(1).splitlines():
        if not line.strip():
            continue
        # A continuation line of a folded scalar is indented; a new key is not.
        if line[:1].isspace() and key is not None:
            folded.append(line.strip())
            continue
        if ":" not in line:
            continue
        flush()
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        # `>-` and `|` open a folded block; the value is on the lines below.
        folded = [] if value in {">-", ">", "|", "|-", ""} else [value]
    flush()

    return fields, text[match.end() :]


def skills_root(explicit: str | os.PathLike[str] | None = None) -> Path:
    """Where the skills live: an explicit path, else `$PALETTE_HOME/skills`.

    Note what this does *not* do: install, copy, or stage the skill anywhere.
    It is read where it already is, so it cannot drift from the checkout and
    there is no equivalent of the benchmark's `bench-check` drift guard to run.
    """
    if explicit:
        return Path(explicit).expanduser().resolve()

    home = os.environ.get("PALETTE_HOME", "").strip()
    if not home:
        raise SkillNotFound(
            "cannot locate the palette skill: $PALETTE_HOME is not set and no "
            "--skills-root was given. Set it to the Palette checkout, e.g.\n"
            "  export PALETTE_HOME=~/Documents/GitHub/project-palette-july25"
        )
    return (Path(home).expanduser() / "skills").resolve()


def load(name: str = "palette", root: str | os.PathLike[str] | None = None) -> SkillCard:
    """Load one skill by folder name. Read-only."""
    base = skills_root(root)
    folder = base / name
    manifest = folder / "SKILL.md"
    if not manifest.is_file():
        raise SkillNotFound(
            f"no SKILL.md at {manifest}. Looked under {base}; it holds: "
            f"{', '.join(sorted(p.name for p in base.iterdir())) if base.is_dir() else '(no such directory)'}"
        )

    text = manifest.read_text(encoding="utf-8")
    fields, body = parse_frontmatter(text)
    return SkillCard(
        name=fields.get("name", name),
        description=fields.get("description", ""),
        body=body,
        directory=folder,
    )


def stage(card: SkillCard, workspace: str | os.PathLike[str]) -> SkillCard:
    """Copy the skill into `<workspace>/skills/<name>` and return a card for it.

    SKILL.md refers to its own script as `skills/<name>/scripts/deck.py` — a
    path relative to a skills root. Every host that installs a skill has one;
    this host had the skill sitting outside the working directory, so the
    instructions were literally wrong and the agent spent its first command
    finding that out:

        → python skills/palette/scripts/deck.py find --root .
        ← python: can't open file '.../skills/palette/scripts/deck.py'
        → python /abs/path/to/skills/palette/scripts/deck.py find --root .

    One wasted round trip per run, out of a budget the judge measures. Staging
    a copy makes the instructions true instead of asking the model to
    compensate for them.

    A copy rather than a symlink, and rebuilt per run, for two reasons the other
    hosts get for free: an agent cannot damage the checkout through it, and it
    cannot drift, because nothing survives to go stale.
    """
    root = Path(workspace).expanduser().resolve() / "skills"
    destination = root / card.name
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        card.directory,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return replace(card, directory=destination)
