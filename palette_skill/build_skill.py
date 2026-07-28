"""Regenerate the machine-written parts of SKILL.md.

The skill is not hand-maintained prose about an API somewhere else. The
volatile parts — the endpoint table, the model menu, the example plans, the
connection defaults — are rendered from the code that defines them:

    contract.py   ->  endpoints, defaults, terminal stages
    config.py     ->  planner / designer / editor model menus, example plans

Each generated region is fenced by ``<!-- BEGIN GENERATED: id -->`` and
``<!-- END GENERATED: id -->``. Everything outside those fences is authored by
hand and is never touched.

    python -m palette_skill.build_skill            # rewrite in place
    python -m palette_skill.build_skill --check    # exit 1 if stale

``--check`` is what the test suite and CI run: add a model to config.py or a
route to contract.py without regenerating, and the check fails loudly instead
of the skill quietly telling an agent something untrue.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Callable

from palette_skill import contract, hosts

REPO_ROOT = Path(__file__).resolve().parent.parent
PAYLOAD_DIR = Path(__file__).resolve().parent / "payload"
SKILL_MD = PAYLOAD_DIR / "SKILL.md"
REFERENCE_MD = PAYLOAD_DIR / "reference.md"

# `\n?` on both sides: without it a region that is currently empty
# (`-->\n<!-- END`) cannot match, because `open` consumes the only newline and
# `close` then has none to take. That silently left the region blank instead of
# failing, which is the worst of both.
_BLOCK_RE_TEMPLATE = (
    r"(?P<open><!-- BEGIN GENERATED: {name} -->\n?)"
    r"(?P<body>.*?)"
    r"(?P<close>\n?<!-- END GENERATED: {name} -->)"
)


class ServerConfigUnavailable(RuntimeError):
    """The server's config.py is not importable — we are not in a checkout."""


def _load_server_config():
    """Import the server's ``config`` module from the repo root.

    Only reachable from a Palette checkout. A *released* skill is installed
    from a wheel with no server beside it, and the regions that need config.py
    (the model menus, the example plans) were already frozen into the payload
    when the release was built — see ``frozen_regions``.
    """
    if not (REPO_ROOT / "config.py").is_file():
        raise ServerConfigUnavailable(
            f"no config.py under {REPO_ROOT} — this is a released package, not a checkout"
        )
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    import config  # noqa: PLC0415 — deliberately late and path-dependent

    return config


def in_checkout() -> bool:
    """Whether the server sources sit beside this package."""
    return (REPO_ROOT / "config.py").is_file() and (REPO_ROOT / "app.py").is_file()


#: Regions that describe the *server* rather than the agent. They are rendered
#: when releasing from a checkout and then frozen into the payload, because a
#: released skill describes the Palette version it shipped with. Re-rendering
#: them on a consumer's machine is impossible and would be wrong anyway.
SERVER_REGIONS = frozenset({"models", "examples"})


# -- renderers -------------------------------------------------------------


#: Host the generated regions are rendered for. Set by main()/run(); the
#: renderers read it so their signatures stay uniform for RENDERERS.
_HOST = hosts.get(hosts.DEFAULT_HOST)

#: Base URL written into the generated `defaults` region. None means "use the
#: contract default and tell the agent to read $PALETTE_URL". Set it when
#: installing against a fixed deployment, so agents need no environment at all.
_BASE_URL: str | None = None

#: Whether the installed skill ships a wheel beside it. Release tarballs do
#: (the agent installs offline); a PyPI install does not, and the agent pulls
#: the client from the index instead.
_VENDORED: bool = True


def render_execution() -> str:
    """The one genuinely host-specific part: how the agent runs anything.

    Everything else in SKILL.md describes a service and is true anywhere. This
    describes the agent's own tools and limits, so it is generated per host
    rather than written once and hedged.
    """
    h = _HOST
    install_cmd = (
        f"uv pip install {h.skill_path}/vendor/palette_skill-*.whl"
        if _VENDORED
        else "uv pip install palette-skill"
    )
    lines: list[str] = []

    if h.inline_python:
        lines += [
            f"Two ways to drive Palette, both fine on {h.label}:",
            "",
            f"- **The CLI** via `{h.shell}` — `palette-skill <subcommand>`, one JSON object on",
            "  stdout. Simplest, and what the examples below use.",
            "- **The Python API** — `from palette_skill import PaletteClient`, for multi-step",
            "  logic in one go. See [reference.md](reference.md).",
        ]
    else:
        lines += [
            f"Everything goes through **`{h.shell}`**. Do **not** write",
            "`from palette_skill import PaletteClient` in a plain code block — code blocks",
            "run under a restricted import allowlist with no `httpx`, so the import fails.",
            "The client lives in the sandbox venv and the shell is how you reach it.",
            "",
            "Two shapes:",
            "",
            f"- **The CLI** — `palette-skill <subcommand>`, one JSON object on stdout. Use this",
            "  for everything below.",
            f"- **A script** — for multi-step logic, `{h.write}` a `.py` that imports",
            f"  `palette_skill`, then `{h.shell}(\"python ./script.py\")`. Inside a script the",
            "  full Python API is available; see [reference.md](reference.md).",
        ]

    if h.step_limit:
        lines += [
            "",
            f"### One command per {h.shell} call",
            "",
            f"**Put exactly one `{h.shell}` in each code block.** The step limit "
            f"({h.step_limit}) applies to the *whole block*, not to each command in it, so two",
            "or three chained commands add up and the block is killed part-way — losing the",
            "work and telling you nothing about which command was slow.",
            "",
            "This is the single most common way to get stuck here. Do not batch the install,",
            "the plan fetch, and the build start together, however small each looks:",
            "",
            "```python",
            "# WRONG — one block, four commands, killed before it finishes",
            f'await {h.shell}("{install_cmd}")',
            f"await {h.shell}('python -c \"import palette_skill\"')",
            f'await {h.shell}("palette-skill example --name x.md --dest ./plan.md")',
            f'await {h.shell}("palette-skill start-build --plan-file ./plan.md")',
            "```",
            "",
            "```python",
            "# RIGHT — one command, one block. The next block does the next command.",
            f'out = await {h.shell}("{install_cmd}")',
            "print(out)",
            "```",
        ]
    else:
        lines += [
            "",
            "### Pacing",
            "",
            f"{h.label} does not cap a step tightly enough to matter here, so you can let a",
            "bounded poll run for a minute or two at a time rather than every 25 seconds.",
            "Still poll rather than blocking on a whole build: progress you can report beats",
            "a silent four-minute gap, and a blocking call tells you nothing until it ends.",
        ]

    if _VENDORED:
        lines += [
            "",
            f"Install the client once per session from the wheel in {h.skill_dir}:",
            "",
            "```bash",
            install_cmd,
            "```",
            "",
            "If the wheel is missing, fall back to `uv pip install palette-skill`.",
        ]
    else:
        lines += [
            "",
            "Install the client once per session:",
            "",
            "```bash",
            install_cmd,
            "```",
        ]
    return "\n".join(lines)


def _is_remote(url: str | None) -> bool:
    """A pinned URL that is not loopback — i.e. a deployment nobody here runs."""
    if not url:
        return False
    return not any(h in url for h in ("127.0.0.1", "localhost", "0.0.0.0", "::1"))


def render_unreachable() -> str:
    """What to tell the user when the server does not answer.

    Pinned to a remote deployment, the local `serve` commands are not merely
    unhelpful — following them starts a *second*, local Palette with none of
    the user's data or models. So a remote-pinned artifact does not carry them
    at all, rather than carrying them behind a caveat an agent may skip.
    """
    remote_only = _is_remote(_BASE_URL)
    lines = [
        "Palette is a separate service. **You cannot start it from here — the user must.**",
        "Do not try: a sandbox confines writes to its own workspace, a supervised child",
        "holding the shell's pipe blocks until the step limit kills it, and the server",
        "needs Node, LibreOffice and Poppler that live outside the sandbox.",
        "",
    ]
    if remote_only:
        lines += [
            f"This skill was built for **{_BASE_URL}**, a remote deployment. Neither you",
            "nor a local install can fix it being down. Report the URL you tried and the",
            "exact error, and ask the user to confirm the deployment is up and the URL is",
            "current. There is nothing to start locally.",
        ]
    else:
        lines += [
            "So when `health` fails with `PaletteUnavailable`, check the URL in the error",
            "before you answer — the right advice depends on it.",
            "",
            "**A remote deployment** (`https://…`, anything that is not loopback). You",
            "cannot fix this and neither can a local install. Report the URL and the error,",
            "and ask the user to confirm it. Never suggest `serve` commands here — those",
            "start a *second, local* Palette, which is not the one they asked for and will",
            "not have their data or models.",
            "",
            "**A local service** (`127.0.0.1` or `localhost`):",
            "",
            "```bash",
            "palette-skill serve doctor    # what is missing, per mode",
            "palette-skill serve ensure    # start it and wait until healthy",
            "palette-skill serve status    # is it up, which mode, which workspace",
            "```",
            "",
            "First run on a machine also needs `palette-skill serve init`, which writes",
            "`~/.config/palette/env` for the `RITS_API_KEY`.",
        ]
    lines += [
        "",
        "Either way: say what you were trying to do, and wait. Do not poll in a loop",
        "hoping it appears, do not switch to a different host, and never fall back to",
        "building slides yourself.",
    ]
    return "\n".join(lines)


def render_defaults() -> str:
    if _BASE_URL:
        first = (
            f"- **Base URL** — `{_BASE_URL}`. This skill was built for that deployment; "
            f"use it unless the user names a different one. `${contract.BASE_URL_ENV}` overrides it."
        )
    else:
        first = (
            f"- **Base URL** — `${contract.BASE_URL_ENV}` if set, otherwise "
            f"`{contract.DEFAULT_BASE_URL}`."
        )
    lines = [
        first,
        f"- **Auth** — `${contract.TOKEN_ENV}` is sent as a bearer token when set. Usually unset.",
        "- **Terminal build stages** — "
        + ", ".join(f"`{stage}`" for stage in sorted(contract.TERMINAL_STAGES))
        + ". Anything else means the build is still running.",
    ]
    return "\n".join(lines)


#: endpoint name -> (CLI subcommand, Python method). Both surfaces are
#: documented from one place so they cannot describe different things.
_CALLS: dict[str, tuple[str, str]] = {
    "health": ("health", "pal.health()"),
    "examples": ("examples", "pal.examples()"),
    "example": ("example --name X", "pal.example(name)"),
    "draft": ("draft --request X", "pal.draft(request, files=[...])"),
    "draft_async": ("start-draft --request X", "pal.start_draft(request, files=[...])"),
    "draft_result": ("draft-result", "pal.draft_result(tid)"),
    "build": ("build --plan-file X", "pal.build_and_wait(plan)"),
    "build_async": ("start-build --plan-file X", "pal.start_build(plan)"),
    "result": ("result", "pal.result(tid)"),
    "progress": ("progress / wait", "pal.progress(tid) / pal.wait(tid)"),
    "deck": ("deck", "pal.deck(tid)"),
    "edit": ("edit --slide N --instruction X", "pal.edit(tid, n, instruction)"),
    "retry": ("retry --slide N", "pal.retry(tid, n)"),
    "preview": ("previews --dest-dir X", "pal.preview(tid, n, dest)"),
    "download": ("download --dest X", "pal.download(tid, dest)"),
    "clear": ("clear", "pal.clear(tid)"),
    "abort": ("abort", "pal.abort(tid)"),
}


def render_endpoints() -> str:
    rows = [
        "| Endpoint | `palette-skill` command | Python | Budget | What it does |",
        "|---|---|---|---|---|",
    ]
    for endpoint in contract.ENDPOINTS:
        cli, python = _CALLS.get(endpoint.name, ("—", "—"))
        flag = "" if endpoint.baseline else " *(feature-detected)*"
        rows.append(
            f"| `{endpoint.method} {endpoint.path}`{flag} "
            f"| `{cli}` "
            f"| `{python}` "
            f"| ~{endpoint.budget_seconds}s "
            f"| {endpoint.summary} |"
        )
    rows.append("")
    rows.append(
        "Commands that act on a session need `--thread-id <TID>`. None of them need "
        "`--base-url` — the client resolves the server itself (see Connecting)."
    )
    return "\n".join(rows)


def render_models() -> str:
    config = _load_server_config()
    menus = {
        "planner": config.PLANNER_MODELS,
        "designer_coder": config.DESIGNER_MODELS,
        "critic": config.CORRECTION_MODELS,
    }
    rows = [
        "| Request field | Pipeline role | Accepted values | Default |",
        "|---|---|---|---|",
    ]
    defaults = {"planner": "gpt-oss-120b", "designer_coder": "palette-lora", "critic": "gpt-oss-120b"}
    for role in contract.MODEL_ROLES:
        choices = menus.get(role.field, {})
        values = ", ".join(f"`{name}`" for name in choices)
        rows.append(
            f"| `{role.field}` | {role.role} — {role.summary} | {values} | `{defaults[role.field]}` |"
        )
    rows.append("")
    rows.append(
        "Anything not in these lists is **silently ignored** by the server "
        "(`config.apply_models` skips unknown names), leaving that role on its current model. "
        "Pass a value from the table or omit the field."
    )
    return "\n".join(rows)


def render_examples() -> str:
    config = _load_server_config()
    available = [
        (fname, label)
        for fname, label in config.USER_FACING_EXAMPLES
        if (config.REFERENCE_PLANS / fname).is_file()
    ]
    if not available:
        return "_No example plans are currently shipped._"
    rows = ["| `name` | Deck |", "|---|---|"]
    rows.extend(f"| `{fname}` | {label} |" for fname, label in available)
    return "\n".join(rows)


#: Region id -> renderer. Adding a region means adding the fence to the
#: markdown and an entry here; --check covers it automatically.
RENDERERS: dict[str, Callable[[], str]] = {
    "execution": render_execution,
    "unreachable": render_unreachable,
    "defaults": render_defaults,
    "endpoints": render_endpoints,
    "models": render_models,
    "examples": render_examples,
}


# -- rewriting -------------------------------------------------------------


def apply(text: str, path: Path) -> str:
    """Replace every generated region present in ``text``."""
    server_available = in_checkout()
    for name, renderer in RENDERERS.items():
        pattern = re.compile(_BLOCK_RE_TEMPLATE.format(name=re.escape(name)), re.DOTALL)
        if not pattern.search(text):
            continue
        if name in SERVER_REGIONS and not server_available:
            # Frozen at release time. Leave whatever the payload carries.
            continue
        rendered = renderer()
        text = pattern.sub(
            lambda m, body=rendered: f"<!-- BEGIN GENERATED: {name} -->\n{body}\n<!-- END GENERATED: {name} -->",
            text,
            count=1,
        )
    _assert_fences_balanced(text, path)
    return text


def _assert_fences_balanced(text: str, path: Path) -> None:
    opens = set(re.findall(r"<!-- BEGIN GENERATED: (\S+) -->", text))
    closes = set(re.findall(r"<!-- END GENERATED: (\S+) -->", text))
    if opens != closes:
        raise SystemExit(f"{path}: unbalanced generated fences: {opens ^ closes}")
    unknown = opens - set(RENDERERS)
    if unknown:
        raise SystemExit(f"{path}: no renderer for generated region(s): {sorted(unknown)}")


def targets() -> list[Path]:
    return [p for p in (SKILL_MD, REFERENCE_MD) if p.is_file()]


def run(
    check: bool = False,
    host: str | None = None,
    base_url: str | None = None,
    vendored: bool = True,
) -> int:
    global _HOST, _BASE_URL, _VENDORED
    _HOST = hosts.get(host)
    _BASE_URL = base_url
    _VENDORED = vendored
    stale: list[Path] = []
    for path in targets():
        current = path.read_text(encoding="utf-8")
        updated = apply(current, path)
        if current == updated:
            continue
        if check:
            stale.append(path)
        else:
            path.write_text(updated, encoding="utf-8")
            print(f"regenerated {path.relative_to(REPO_ROOT)}")

    if check and stale:
        names = ", ".join(str(p.relative_to(REPO_ROOT)) for p in stale)
        print(
            f"stale generated content in: {names}\n"
            "Palette's code moved but the skill did not. "
            "Run `make skill-build` and commit the result.",
            file=sys.stderr,
        )
        return 1
    if check:
        print("skill content is up to date with contract.py and config.py")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Do not write; exit 1 if any generated region is stale.",
    )
    parser.add_argument(
        "--host",
        default=hosts.DEFAULT_HOST,
        choices=sorted(hosts.HOSTS),
        help="Agent host to generate the execution section for.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Pin a deployment URL into the skill (e.g. a Code Engine app), so agents need no env var.",
    )
    args = parser.parse_args(argv)
    return run(check=args.check, host=args.host, base_url=args.base_url)


if __name__ == "__main__":
    raise SystemExit(main())
