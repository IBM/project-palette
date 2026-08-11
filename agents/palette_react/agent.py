"""A LangGraph ReAct agent that uses a skill it did not write.

`create_react_agent` has no notion of a skill: its whole surface is `model`,
`tools`, `prompt` and a checkpointer. So the loader lives here, in about forty
lines, and it does what a real one does — offer the name and description, hand
over the body on request, and give the agent a shell to run the scripts with.

Deliberately the *thinnest* scaffold in the benchmark. It has no planner, no
todo list, no subagents, nothing that could take credit for a result. If this
host passes a case the others fail, the instructions were never the problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

# LangGraph 1.0 deprecates this in favour of `langchain.agents.create_agent`
# (same shape; `prompt` becomes `system_prompt`), and will drop it in 2.0. It is
# pinned here rather than shimmed on purpose: a host that silently swaps
# implementations depending on what is installed produces results that cannot be
# compared across machines, which is the one thing a benchmark host must not do.
# When 2.0 lands, change these two lines deliberately and re-run the suite.
from langgraph.prebuilt import create_react_agent

from .skill import SkillCard
from .tools import build_tools

#: LangGraph's default is 25, which is roughly twelve model turns — less than
#: one deck. A build is `find` + `plan` + approval + `start` + polls, and the
#: benchmark's own judge tolerates up to twelve polls on its own. Hitting the
#: ceiling looks exactly like the agent giving up, so it is set high and
#: reported with every run rather than left implicit.
DEFAULT_RECURSION_LIMIT = 120

_HOST_PROMPT = """\
You are an agent with a shell and a set of skills.

A skill is a folder of instructions and scripts, written for the job it names.
When a request matches one, load it and follow it exactly as written — the
instructions know things about the tools that you do not, and they are the
supported way to do that job.

Available skills:
{catalogue}

{location}
Work in the current directory. Do not cd elsewhere.
"""

_LOCATION = """\
Where the instructions refer to a script by a path like
`skills/<name>/scripts/<script>.py`, the skills folder on this host is:

    {root}

so that script is at `{root}/<name>/scripts/<script>.py`. Use that path.
"""


def system_prompt(cards: list[SkillCard], *, lazy: bool = True) -> str:
    """The host's own prompt. Never the skill's text edited — see below.

    In lazy mode the catalogue is name and description only, which is the
    frontmatter a loader reads, and the body arrives through `load_skill`. In
    eager mode the body is inlined and the routing decision disappears, which
    is a different measurement — cheaper, and blind to whether the agent would
    have found the skill at all.

    Either way the skill's text is reproduced unchanged. What this adds is
    *where the folder is*, which is host knowledge: the instructions cannot
    know where a given host put them, and supplying it is the loader's job.
    """
    if not cards:
        return _HOST_PROMPT.format(catalogue="  (none)", location="")

    root = cards[0].directory.parent
    if lazy:
        catalogue = "\n".join(f"  - {c.name}: {c.description}" for c in cards)
        catalogue += (
            "\n\nCall load_skill(name) to read one before you use it."
        )
    else:
        catalogue = "\n\n".join(
            f"  - {c.name}: {c.description}\n\n"
            f"--- begin {c.name} instructions ---\n{c.body}\n"
            f"--- end {c.name} instructions ---"
            for c in cards
        )

    return _HOST_PROMPT.format(
        catalogue=catalogue, location=_LOCATION.format(root=root)
    )


def _text(message: Any) -> str:
    """A message's text. Some providers return content as a list of parts."""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return str(content or "")


def _describe(message: Any) -> str | None:
    """One line of progress for a message, or None if it is not worth showing."""
    calls = getattr(message, "tool_calls", None)
    if calls:
        shown = []
        for call in calls:
            args = call.get("args", {}) or {}
            # The command is the interesting part of a shell call; for anything
            # else the arguments are short enough to show whole.
            detail = args.get("command") or args.get("name") or ", ".join(map(str, args.values()))
            detail = " ".join(str(detail).split())
            shown.append(f"→ {call.get('name', '?')}: {detail[:160]}")
        return "\n".join(shown)

    if getattr(message, "type", "") == "tool":
        first = " ".join(_text(message).split())
        return f"← {first[:160] or '(no output)'}"
    return None


@dataclass
class Session:
    """One conversation. `send` is a turn; state persists across turns.

    The thread id is what makes a multi-turn case possible at all: a deck is
    never one turn, and the interesting failures only appear on the second —
    building an unapproved plan, re-planning instead of editing, losing pasted
    context.
    """

    graph: Any
    thread_id: str
    recursion_limit: int = DEFAULT_RECURSION_LIMIT
    #: Every message in and out, for the transcript a run is judged beside.
    turns: list[dict] = field(default_factory=list)

    def send(self, message: str, on_event: Any = None) -> str:
        """One turn. `on_event(line)` is called as each tool call and result lands.

        Streamed rather than invoked because a turn that builds a deck spends
        minutes inside `status` holds with nothing to show. A caller that prints
        only the final answer looks hung for the entire build, and the obvious
        reading of that is that it has crashed — reported from a real run before
        this existed.
        """
        answer = ""
        steps: list[str] = []
        for chunk in self.graph.stream(
            {"messages": [{"role": "user", "content": message}]},
            config={
                "configurable": {"thread_id": self.thread_id},
                "recursion_limit": self.recursion_limit,
            },
            stream_mode="updates",
        ):
            for update in (chunk or {}).values():
                for produced in (update or {}).get("messages") or []:
                    event = _describe(produced)
                    if event:
                        steps.append(event)
                        if on_event is not None:
                            on_event(event)
                    text = _text(produced)
                    # The last assistant message with prose is the answer; tool
                    # calls carry empty content and must not overwrite it.
                    if getattr(produced, "type", "") == "ai" and text.strip():
                        answer = text

        self.turns.append({"sent": message, "answer": answer, "steps": steps})
        return answer


def build_agent(
    workspace: str | Path,
    cards: list[SkillCard],
    model: Any,
    *,
    lazy: bool = True,
    timeout: int | None = None,
    recursion_limit: int = DEFAULT_RECURSION_LIMIT,
    thread_id: str = "standalone",
) -> Session:
    """Wire model + tools + prompt into a session bound to `workspace`."""
    workspace = Path(workspace).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    kwargs = {} if timeout is None else {"timeout": timeout}
    tools = build_tools(workspace, cards, lazy=lazy, **kwargs)

    graph = create_react_agent(
        model,
        tools,
        prompt=system_prompt(cards, lazy=lazy),
        checkpointer=MemorySaver(),
    )
    return Session(graph=graph, thread_id=thread_id, recursion_limit=recursion_limit)
