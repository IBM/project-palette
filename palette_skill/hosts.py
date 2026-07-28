"""Agent hosts the skill can be generated for.

Almost all of SKILL.md is host-neutral: what Palette is, the workflow, the
endpoints, the model roster, the failure modes, the verification gate. Those
describe a service and are true wherever the agent runs.

What is *not* neutral is how an agent executes anything — the tool it shells
out with, the tool it reads a file with, whether it may import the client
in-process, and how long a single step may take. Those differ per host and are
exactly the details an agent needs to be correct rather than merely informed.

So the execution section is generated from these profiles, and everything else
is written once. Adding a host means adding a profile here, not forking the
skill.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Host:
    key: str
    label: str

    #: Tool the agent shells out with.
    shell: str
    #: Tools for reading, writing and editing workspace files.
    read: str
    write: str
    edit: str

    #: How to *name* the skill's directory in prose.
    skill_dir: str
    #: How to *path* it in a shell command. Distinct from skill_dir because a
    #: readable phrase ("this skill's own directory") is not something you can
    #: interpolate into `uv pip install`.
    skill_path: str

    #: Per-step wall clock, phrased for prose. None when the host does not
    #: meaningfully cap a step.
    step_limit: str | None

    #: Whether the agent may `import palette_skill` directly in its own code.
    #: False where code runs under a restricted import allowlist.
    inline_python: bool

    #: Default install root, relative to an agent project root. Absolute paths
    #: (a leading ~) install globally for that host.
    install_root: str

    #: Whether the host follows a symlinked skill directory. When it does, the
    #: skill can point straight at this repo and never go stale.
    follows_symlinks: bool

    #: One line on why the install root is what it is.
    install_note: str


CUGA = Host(
    key="cuga",
    label="CUGA",
    shell="run_command",
    read="read_file",
    write="write_file",
    edit="edit_file",
    skill_dir="`./skills/palette`",
    skill_path="./skills/palette",
    step_limit="about 30 seconds",
    # cuga_lite executes code blocks in-process under an allowlist that has no
    # httpx, so an import of the client fails there — the shell is the only way
    # to reach it.
    inline_python=False,
    install_root=".cuga/skills",
    # Path.rglob stopped following directory symlinks in Python 3.13, so a
    # symlinked skill is discovered on 3.12 and silently vanishes on upgrade.
    follows_symlinks=False,
    install_note="CUGA scans one skills root; `[skills] root` in settings.toml selects it.",
)

CLAUDE_CODE = Host(
    key="claude-code",
    label="Claude Code",
    shell="Bash",
    read="Read",
    write="Write",
    edit="Edit",
    skill_dir="this skill's own directory",
    skill_path="~/.claude/skills/palette",
    # Bash defaults to a 2 minute timeout and accepts up to 10, so a bounded
    # poll is a convenience here rather than a necessity.
    step_limit=None,
    inline_python=True,
    install_root="~/.claude/skills",
    follows_symlinks=True,
    install_note="Claude Code resolves symlinked skill directories, so the install can point at this repo.",
)

GENERIC = Host(
    key="generic",
    label="a generic agent host",
    shell="your shell-execution tool",
    read="your file-read tool",
    write="your file-write tool",
    edit="your file-edit tool",
    skill_dir="this skill's directory",
    skill_path="./skills/palette",
    step_limit=None,
    inline_python=True,
    install_root="skills",
    follows_symlinks=False,
    install_note="Point your host's skill loader at the installed directory.",
)


HOSTS: dict[str, Host] = {h.key: h for h in (CUGA, CLAUDE_CODE, GENERIC)}
DEFAULT_HOST = CUGA.key


def get(key: str | None) -> Host:
    resolved = (key or DEFAULT_HOST).strip().lower()
    if resolved not in HOSTS:
        raise SystemExit(f"unknown host {resolved!r}; expected one of: {', '.join(sorted(HOSTS))}")
    return HOSTS[resolved]
