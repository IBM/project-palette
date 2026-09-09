"""A LangGraph ReAct host for the Palette skill benchmark.

The thinnest scaffold that can use a skill: a model, a shell, and a loader that
hands over `SKILL.md` when the agent asks for it. No planner, no todo list, no
subagents — so a case it passes was passed by the instructions, not by the
harness around them.

It reads the skill where the skill already lives and modifies nothing in
`skills/` or `benchmark/`.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["agent", "model", "skill", "tools"]
