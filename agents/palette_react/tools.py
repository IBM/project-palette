"""The two tools a skill-using agent needs: load the instructions, run a shell.

Nothing here knows what Palette is. That is the point — the skill's own text is
what teaches the agent the commands, so this host stays a generic skill runner
and the thing under measurement stays the skill.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from langchain_core.tools import StructuredTool

from .skill import SkillCard

#: `plan` holds its call open for up to 90 seconds and `status` for 60, by
#: design — the skill says so, and the alternative is a poll per round trip out
#: of a finite step budget. A 30s default would fail every case and look like
#: Palette breaking.
DEFAULT_TIMEOUT = 300

#: A plan for a long deck comes back as JSON on stdout and is the thing the
#: agent has to show the user; truncating it mid-plan turns a good run into a
#: mysterious one. Generous, but bounded — a build log tail should not be able
#: to fill the context window.
DEFAULT_MAX_OUTPUT = 24_000


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [output truncated, {len(text) - limit} more characters]"


def make_bash(
    workspace: Path,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_output: int = DEFAULT_MAX_OUTPUT,
) -> StructuredTool:
    """A shell pinned to `workspace`.

    Pinned because every host in this benchmark runs each case in its own
    directory and the verdict is read from that directory. An agent that
    wandered elsewhere would produce a real deck that the judge scores as
    missing.
    """
    workspace = Path(workspace).expanduser().resolve()

    def bash(command: str) -> str:
        """Run a shell command in the working directory and return its output."""
        try:
            done = subprocess.run(
                command,
                shell=True,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=timeout,
                # The whole environment, so $PALETTE_HOME reaches deck.py and
                # $PALETTE_TRACE reaches the trace the benchmark reads.
                env=os.environ.copy(),
            )
        except subprocess.TimeoutExpired:
            # The skill is explicit that a cut-short step is not evidence of
            # failure — the work runs in its own process and finishes anyway.
            # Say that here rather than let the model infer a crash.
            return (
                f"[the command was still running after {timeout}s and was stopped]\n"
                "This is not evidence that it failed. The work runs in its own "
                "process and continues. Poll for the result instead of re-running it."
            )
        except OSError as exc:
            return f"[could not run the command: {exc}]"

        parts = []
        if done.stdout:
            parts.append(done.stdout)
        if done.stderr:
            parts.append(done.stderr if not parts else f"[stderr]\n{done.stderr}")
        if done.returncode != 0:
            parts.append(f"[exit code {done.returncode}]")
        return _clip("\n".join(parts).strip() or "[no output]", max_output)

    return StructuredTool.from_function(
        func=bash,
        name="bash",
        description=(
            "Run a shell command. The working directory is fixed at "
            f"{workspace} — do not cd elsewhere. Returns stdout, stderr and the "
            "exit code."
        ),
    )


def make_load_skill(*cards: SkillCard) -> StructuredTool:
    """A tool that hands back a skill's full instructions, verbatim.

    This is the second half of progressive disclosure: the system prompt offers
    the name and description, and the body arrives only once the model decides
    the skill applies. Loading the body up front would answer the routing
    question on the model's behalf — and whether an agent recognises that a
    request needs the skill is one of the things being measured.
    """
    by_name = {card.name: card for card in cards}

    def load_skill(name: str) -> str:
        """Load the full instructions for an available skill, by name."""
        card = by_name.get(name.strip())
        if card is None:
            return (
                f"no skill named {name!r}. Available: "
                f"{', '.join(sorted(by_name)) or 'none'}"
            )
        # Verbatim. A host that edits the instructions on the way through is
        # measuring itself; tests/test_skill.py pins this against the file.
        return card.body

    return StructuredTool.from_function(
        func=load_skill,
        name="load_skill",
        description=(
            "Load the full instructions for one of the available skills. "
            "Call this before using a skill, then follow what it says."
        ),
    )


def build_tools(
    workspace: Path,
    cards: list[SkillCard],
    *,
    timeout: int = DEFAULT_TIMEOUT,
    max_output: int = DEFAULT_MAX_OUTPUT,
    lazy: bool = True,
) -> list[StructuredTool]:
    """The toolset. `lazy=False` drops `load_skill` — the body is in the prompt."""
    tools = [make_bash(workspace, timeout=timeout, max_output=max_output)]
    if lazy:
        tools.insert(0, make_load_skill(*cards))
    return tools
