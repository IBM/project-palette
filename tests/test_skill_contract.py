"""The skill must describe the server that actually exists.

Two tiers, deliberately:

* **Source tier** — reads ``app.py`` and ``config.py`` with the ``ast`` module.
  Needs no server dependencies, so it runs anywhere, including a bare checkout.
  This is the tier that catches drift in practice.
* **Live tier** — imports the real FastAPI app and walks its routes. Stronger,
  but needs ``requirements.txt`` installed, so it skips when they are absent.

Between them: add a route to app.py, rename a request field, add a model to
config.py, or change an example plan, and something here goes red before an
agent is ever told the wrong thing.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from palette_skill import build_skill, contract  # noqa: E402

APP_PY = REPO_ROOT / "app.py"
SKILL_MD = REPO_ROOT / "palette_skill" / "payload" / "SKILL.md"

#: Routes that exist for the browser UI and are deliberately outside the skill
#: contract — an agent never calls them.
UI_ONLY_PATHS = frozenset({"/", "/asset/{name}"})


# -- source-tier helpers ---------------------------------------------------


def _app_module() -> ast.Module:
    return ast.parse(APP_PY.read_text(encoding="utf-8"))


def _routes_from_source() -> dict[tuple[str, str], str]:
    """``{(METHOD, path): handler_name}`` for every ``@app.<verb>`` decorator."""
    routes: dict[tuple[str, str], str] = {}
    for node in ast.walk(_app_module()):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            target = decorator.func.value
            if not isinstance(target, ast.Name) or target.id != "app":
                continue
            method = decorator.func.attr.upper()
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
                continue
            routes[(method, str(decorator.args[0].value))] = node.name
    return routes


def _model_fields(class_name: str) -> set[str]:
    """Annotated field names of a pydantic model declared in app.py."""
    for node in _app_module().body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            }
    raise AssertionError(f"{class_name} is no longer declared in app.py")


def _handler_params(handler_name: str) -> set[str]:
    for node in ast.walk(_app_module()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == handler_name:
            args = node.args
            return {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    raise AssertionError(f"handler {handler_name!r} is gone from app.py")


# -- source tier -----------------------------------------------------------


class TestRoutesMatchContract:
    def test_every_baseline_endpoint_still_exists(self) -> None:
        routes = _routes_from_source()
        missing = [
            f"{e.method} {e.path}" for e in contract.BASELINE if (e.method, e.path) not in routes
        ]
        assert not missing, (
            f"contract.py promises routes app.py no longer has: {missing}. "
            "Either restore the route or update the contract and regenerate the skill."
        )

    def test_every_route_is_in_the_contract(self) -> None:
        """A new endpoint must be added to contract.py, not left undocumented."""
        known = {(e.method, e.path) for e in contract.ENDPOINTS}
        undocumented = [
            f"{method} {path}"
            for (method, path) in _routes_from_source()
            if path not in UI_ONLY_PATHS and (method, path) not in known
        ]
        assert not undocumented, (
            f"app.py exposes routes the skill knows nothing about: {undocumented}. "
            "Add them to palette_skill/contract.py and run `make skill-build`."
        )

    @pytest.mark.parametrize(
        ("model", "endpoint"),
        [("BuildReq", contract.BUILD), ("EditReq", contract.EDIT)],
    )
    def test_request_model_fields_match(self, model: str, endpoint) -> None:
        assert _model_fields(model) == set(endpoint.fields), (
            f"{model} in app.py and contract.{endpoint.name.upper()}.fields disagree."
        )

    def test_build_async_reuses_the_build_request_model(self) -> None:
        """Background and blocking builds must accept identical payloads."""
        assert set(contract.BUILD.fields) == set(contract.BUILD_ASYNC.fields)

    def test_draft_form_fields_match(self) -> None:
        routes = _routes_from_source()
        handler = routes[("POST", contract.DRAFT.path)]
        assert _handler_params(handler) == set(contract.DRAFT.fields)

    def test_terminal_stages_are_stages_app_actually_sets(self) -> None:
        source = APP_PY.read_text(encoding="utf-8")
        for stage in contract.TERMINAL_STAGES:
            assert f'"stage": "{stage}"' in source or f"'stage': '{stage}'" in source, (
                f"contract.TERMINAL_STAGES lists {stage!r}, but app.py never sets it. "
                "The client would poll forever."
            )


class TestModelMenus:
    """config.py owns the model roster; the skill must not invent names."""

    def _config(self):
        return build_skill._load_server_config()

    @pytest.mark.parametrize("field", ["planner", "designer_coder", "critic"])
    def test_every_model_choice_is_documented(self, field: str) -> None:
        config = self._config()
        menus = {
            "planner": config.PLANNER_MODELS,
            "designer_coder": config.DESIGNER_MODELS,
            "critic": config.CORRECTION_MODELS,
        }
        rendered = build_skill.render_models()
        missing = [name for name in menus[field] if f"`{name}`" not in rendered]
        assert not missing, f"{field}: models in config.py absent from SKILL.md: {missing}"

    def test_request_defaults_are_accepted_values(self) -> None:
        """A default the server would silently ignore is a real bug.

        ``config.apply_models`` skips names it does not recognise, so a bad
        default leaves the role on whatever the previous build set.
        """
        config = self._config()
        defaults = {
            "planner": ("gpt-oss-120b", config.PLANNER_MODELS),
            "designer_coder": ("palette-lora", config.DESIGNER_MODELS),
            "critic": ("gpt-oss-120b", config.CORRECTION_MODELS),
        }
        for field, (value, menu) in defaults.items():
            assert value in menu, (
                f"the client's default {field}={value!r} is not in config.py's menu "
                f"({sorted(menu)}), so the server would ignore it."
            )

    def test_model_roles_map_to_real_roster_keys(self) -> None:
        config = self._config()
        for role in contract.MODEL_ROLES:
            for key in role.role.split("+"):
                assert key in config.ROSTER, f"contract names roster role {key!r}, config.ROSTER has no such key"

    def test_example_plans_exist_on_disk(self) -> None:
        config = self._config()
        rendered = build_skill.render_examples()
        for fname, _label in config.USER_FACING_EXAMPLES:
            if (config.REFERENCE_PLANS / fname).is_file():
                assert f"`{fname}`" in rendered, f"{fname} ships but SKILL.md does not list it"


class TestGeneratedContent:
    def test_skill_markdown_is_current(self) -> None:
        assert build_skill.run(check=True) == 0, (
            "SKILL.md / reference.md are stale against contract.py or config.py. "
            "Run `make skill-build` and commit the result."
        )

    def test_skill_frontmatter_is_wellformed(self) -> None:
        text = SKILL_MD.read_text(encoding="utf-8")
        assert text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
        frontmatter = text.split("---\n", 2)[1]
        assert "name: palette" in frontmatter
        assert "description:" in frontmatter
        # CUGA strips Jinja delimiters out of name/description as an injection
        # guard; anything caught here would be silently mangled in the prompt.
        for token in ("{{", "}}", "{%", "%}"):
            assert token not in frontmatter, f"frontmatter contains {token!r}, which the host will strip"

    def test_every_documented_endpoint_appears_in_the_table(self) -> None:
        rendered = build_skill.render_endpoints()
        for endpoint in contract.ENDPOINTS:
            assert f"{endpoint.method} {endpoint.path}" in rendered

    def test_skill_forbids_claiming_an_unbuilt_deck(self) -> None:
        """Observed failure: an agent announced a finished deck eighteen
        seconds after starting a *draft*, having never called start-build.
        The verification gate that prevents it must stay in SKILL.md.
        """
        text = SKILL_MD.read_text(encoding="utf-8")
        assert "Never claim a deck that does not exist" in text
        assert "A plan is not a deck" in text
        # Prose alone failed twice. The gate now points at a machine-checked
        # flag, so the claim of completion is not the model's to make.
        assert '"verified": true' in text, (
            "the gate must anchor on the filesystem-verified flag, not on an instruction to look"
        )
        assert "ls -l ./deck/deck.pptx" in text, "keep the manual check for the granular path"

    def test_skill_distinguishes_remote_from_local_when_unreachable(self) -> None:
        """`serve ensure` is wrong advice for a deployed Palette — it starts a
        second local one, which has neither the user's data nor their models."""
        text = SKILL_MD.read_text(encoding="utf-8")
        assert "A remote deployment" in text and "A local service" in text
        assert "second, local" in text, (
            "the skill must say why serve commands are wrong for a remote deployment"
        )

    def test_skill_never_asks_the_user_for_a_url(self) -> None:
        """Observed failure: the agent opened with "Could you provide the
        Palette server URL?" instead of building a deck.

        It was obeying an instruction to check whether $PALETTE_URL was set —
        which it cannot do, because `os` is not in the host's allowed imports.
        The client already resolves explicit -> env -> default, so the only
        correct guidance is to omit the flag and run the command.
        """
        text = SKILL_MD.read_text(encoding="utf-8")
        assert "do not ask the user for a URL" in text
        assert "palette-skill --base-url <URL>" not in text, (
            "examples must not carry a placeholder URL — the agent copies them literally"
        )

    def test_skill_forbids_batching_commands_into_one_step(self) -> None:
        """Observed failure: a model put install + fetch + start-build in one
        code block. Hosts time-limit the *block*, so the commands accumulated
        past the limit and the step was killed mid-build. The instruction that
        prevents it must stay in SKILL.md.
        """
        text = SKILL_MD.read_text(encoding="utf-8")
        assert "One command per" in text
        assert "WRONG" in text and "RIGHT" in text, "the rule needs the worked example to stick"

    def test_serve_commands_named_in_the_skill_exist(self) -> None:
        """SKILL.md tells the user how to start a local service — those
        commands must be real, or the agent hands out a broken instruction."""
        import re

        from palette_skill.cli import build_parser

        serve_action = next(
            action
            for action in build_parser()._subparsers._group_actions[0].choices["serve"]._actions
            if action.dest == "action"
        )
        available = set(serve_action.choices)

        text = SKILL_MD.read_text(encoding="utf-8")
        referenced = set(re.findall(r"palette-skill serve (\w[\w-]*)", text))
        unknown = referenced - available
        assert not unknown, (
            f"SKILL.md references `palette-skill serve` actions that do not exist: {sorted(unknown)}. "
            f"Available: {sorted(available)}."
        )
        assert referenced, "SKILL.md should tell the user how to start a local service"


class TestHostPortability:
    """The skill describes a service; only execution is host-specific.

    If host-specific vocabulary leaks outside the generated execution region,
    the skill silently stops being usable anywhere but CUGA.
    """

    #: Tool names that belong to one host and must never appear in the shared prose.
    HOST_VOCABULARY = ("run_command", "write_file", "read_file", "edit_file", "Bash(")

    def _shared_prose(self) -> str:
        """SKILL.md with the generated execution region removed."""
        import re

        return re.sub(
            r"<!-- BEGIN GENERATED: execution -->.*?<!-- END GENERATED: execution -->",
            "",
            SKILL_MD.read_text(encoding="utf-8"),
            flags=re.DOTALL,
        )

    def test_shared_prose_names_no_host_specific_tool(self) -> None:
        prose = self._shared_prose()
        leaked = [token for token in self.HOST_VOCABULARY if token in prose]
        assert not leaked, (
            f"host-specific tool names outside the generated execution region: {leaked}. "
            "Either phrase it neutrally, or move it into build_skill.render_execution()."
        )

    @pytest.mark.parametrize("host_key", ["cuga", "claude-code", "generic"])
    def test_every_host_renders(self, host_key: str) -> None:
        from palette_skill import hosts

        build_skill._HOST = hosts.get(host_key)
        try:
            rendered = build_skill.render_execution()
        finally:
            build_skill._HOST = hosts.get(hosts.DEFAULT_HOST)

        host = hosts.get(host_key)
        assert host.shell in rendered, "the execution section must name the host's shell tool"
        assert f"{host.skill_path}/vendor/palette_skill-" in rendered, (
            "the install command must carry a real path, not a prose description"
        )

    def test_claude_code_variant_drops_cuga_vocabulary(self) -> None:
        """The point of the whole exercise: another host gets its own words."""
        from palette_skill import hosts

        build_skill._HOST = hosts.get("claude-code")
        try:
            rendered = build_skill.render_execution()
        finally:
            build_skill._HOST = hosts.get(hosts.DEFAULT_HOST)

        for token in ("run_command", "write_file", "read_file"):
            assert token not in rendered, f"{token!r} leaked into the Claude Code variant"
        assert "Bash" in rendered


class TestClientIsolation:
    """The installed client must not depend on the server's world."""

    def test_client_imports_nothing_heavy(self) -> None:
        source = (REPO_ROOT / "palette_skill" / "client.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        server_only = {
            "config", "pipeline", "render", "detector", "refine", "intake",
            "session", "prompts", "harness_prompts", "postprocess", "ui", "app",
            "fastapi", "uvicorn", "pdfplumber", "fitz", "pptx", "docx",
        }
        leaked = imported & server_only
        assert not leaked, (
            f"client.py imports server-side modules {sorted(leaked)}. "
            "The client ships to agent sandboxes and must stay httpx-only."
        )

    def test_contract_imports_nothing_at_all(self) -> None:
        source = (REPO_ROOT / "palette_skill" / "contract.py").read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name in {"dataclasses", "typing"}, f"contract.py imports {alias.name}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module in {"__future__", "dataclasses", "typing"}, (
                    f"contract.py imports {node.module}; it must stay dependency-free"
                )


# -- live tier -------------------------------------------------------------


@pytest.mark.contract
class TestLiveApp:
    """Stronger checks against the real FastAPI app.

    Skipped unless the server's own dependencies are installed, since importing
    app.py pulls in the whole pipeline.
    """

    @pytest.fixture(scope="class")
    @classmethod
    def routes(cls):
        pytest.importorskip("fastapi", reason="server deps not installed")
        pytest.importorskip("pdfplumber", reason="server deps not installed")
        app_module = pytest.importorskip("app", reason="app.py not importable")
        return {
            (method, route.path)
            for route in app_module.app.routes
            for method in getattr(route, "methods", set()) or set()
            if method not in {"HEAD", "OPTIONS"}
        }

    def test_baseline_routes_exist(self, routes) -> None:
        missing = [f"{e.method} {e.path}" for e in contract.BASELINE if (e.method, e.path) not in routes]
        assert not missing, f"live app is missing contracted routes: {missing}"

    def test_background_build_routes_exist(self, routes) -> None:
        """These are feature-detected at runtime, but this checkout should have them."""
        for endpoint in (contract.BUILD_ASYNC, contract.RESULT):
            assert (endpoint.method, endpoint.path) in routes, (
                f"{endpoint.method} {endpoint.path} is missing from this checkout. "
                "Clients will fall back to blocking builds."
            )

    def test_openapi_advertises_the_contract(self, routes) -> None:
        """Capability detection reads /openapi.json — it must list our routes."""
        app_module = pytest.importorskip("app")
        paths = set(app_module.app.openapi().get("paths", {}))
        for endpoint in contract.ENDPOINTS:
            assert endpoint.path in paths, (
                f"{endpoint.path} is absent from the OpenAPI document, so "
                "PaletteClient.capabilities() cannot detect it."
            )
