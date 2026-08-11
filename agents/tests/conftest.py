"""Fixtures for the ReAct host's tests.

Every test here is offline. Nothing reaches watsonx, nothing renders a deck —
those cost minutes and money, and a suite you will not run is a suite that
catches nothing. What is checked is the wiring: that the skill arrives intact,
that the shell lands in the right directory, and that the benchmark runner
scores with the shared judge rather than one of its own.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any

import pytest

AGENTS_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = AGENTS_DIR.parent
SKILLS_ROOT = REPO_ROOT / "skills"

sys.path.insert(0, str(AGENTS_DIR))

from langchain_core.callbacks import CallbackManagerForLLMRun  # noqa: E402
from langchain_core.language_models import BaseChatModel  # noqa: E402
from langchain_core.messages import AIMessage, BaseMessage  # noqa: E402
from langchain_core.outputs import ChatGeneration, ChatResult  # noqa: E402

from palette_react import skill as skill_loader  # noqa: E402


class ScriptedModel(BaseChatModel):
    """A chat model that replays a fixed list of replies.

    `create_react_agent` calls `bind_tools`, so that is overridden to return
    self; everything else is a normal BaseChatModel. Each call records what it
    was sent, which is how the prompt assertions are made.
    """

    replies: list[Any] = []
    seen: list[list[BaseMessage]] = []
    bound: list[str] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001, ARG002
        self.bound = [getattr(t, "name", str(t)) for t in tools]
        return self

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,  # noqa: ARG002
        run_manager: CallbackManagerForLLMRun | None = None,  # noqa: ARG002
        **kwargs: Any,  # noqa: ARG002
    ) -> ChatResult:
        self.seen.append(list(messages))
        reply = self.replies.pop(0) if self.replies else AIMessage(content="done")
        if isinstance(reply, str):
            reply = AIMessage(content=reply)
        return ChatResult(generations=[ChatGeneration(message=reply)])


def tree_digest(root: Path) -> dict[str, str]:
    """Content hash of every file under `root`. Byte-level, so mtime is irrelevant."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }


@pytest.fixture(scope="session")
def skills_root() -> Path:
    assert SKILLS_ROOT.is_dir(), f"no skills folder at {SKILLS_ROOT}"
    return SKILLS_ROOT


@pytest.fixture(scope="session")
def skill_dir(skills_root: Path) -> Path:
    return skills_root / "palette"


@pytest.fixture
def card(skills_root: Path):
    """The real palette skill, read from the checkout."""
    return skill_loader.load("palette", skills_root)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    place = tmp_path / "work"
    place.mkdir()
    return place


@pytest.fixture
def scripted() -> type[ScriptedModel]:
    return ScriptedModel
