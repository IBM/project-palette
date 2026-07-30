"""Run Palette as a local service, and tell you honestly when it is not.

Palette is a server: a FastAPI app wrapping a three-stage pipeline that needs
Node, LibreOffice, Poppler, and a RITS key. This module is the host-side
supervisor for it — start, stop, health, logs, and a preflight check.

Three backends:

``process``
    A detached ``python app.py`` from a checkout, with a pid file and a log
    file under the state directory. Fastest to start and stop.

``container``
    ``docker``/``podman run -d`` against the repo's Dockerfile. No native
    toolchain needed and byte-identical to the Code Engine deployment.

``launchd``
    A macOS LaunchAgent wrapping ``process``: starts at login, restarts on
    crash. Container mode does not need this — container runtimes have their
    own restart policy, and ``start`` sets one.

``start`` with no mode picks ``container`` when an image is available and
``process`` otherwise.

**This is deliberately host-side.** An agent sandbox is the wrong place to
start Palette: Seatbelt-style policies confine writes to the sandbox workspace,
a supervised child holding the shell's stdout pipe blocks until the step limit
kills it, and the native toolchain is outside the sandbox anyway. The skill
detects the service and tells the user how to start it; it never tries itself.

Secrets stay in an env file (``~/.config/palette/env``, mode 600) rather than
the launchd plist or your shell history, so one file serves every backend.
"""

from __future__ import annotations

import json
import os
import platform
import plistlib
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from palette_skill import contract
from palette_skill.client import PaletteClient, PaletteError

Mode = Literal["process", "container", "launchd"]

DEFAULT_ENV_FILE = Path("~/.config/palette/env").expanduser()
DEFAULT_STATE_DIR = Path("~/.local/state/palette").expanduser()
LAUNCHD_LABEL = "com.ibm.palette"
DEFAULT_IMAGE = "palette:latest"
DEFAULT_CONTAINER_NAME = "palette"
CONTAINER_PORT = 8080  # what the Dockerfile EXPOSEs

#: Marker files that identify a Palette checkout.
CHECKOUT_MARKERS = ("app.py", "config.py", "pipeline.py")


class ServiceError(PaletteError):
    """Something about the local service is wrong, with an actionable message.

    Subclasses PaletteError so one ``except PaletteError`` covers both the
    client and the supervisor.
    """


# -- configuration ---------------------------------------------------------


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a ``KEY=VALUE`` env file. Missing file is not an error."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _looks_like_checkout(path: Path) -> bool:
    return all((path / marker).is_file() for marker in CHECKOUT_MARKERS)


def find_home(explicit: str | Path | None = None, env: dict[str, str] | None = None) -> Path | None:
    """Locate a Palette checkout: explicit, then PALETTE_HOME, then this
    package's parent (editable installs), then upward from the cwd."""
    env = env or {}
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    for source in (env.get("PALETTE_HOME"), os.environ.get("PALETTE_HOME")):
        if source:
            candidates.append(Path(source).expanduser())
    candidates.append(Path(__file__).resolve().parent.parent)
    cwd = Path.cwd().resolve()
    candidates.extend([cwd, *cwd.parents])

    for candidate in candidates:
        try:
            if _looks_like_checkout(candidate.resolve()):
                return candidate.resolve()
        except OSError:
            continue
    return None


@dataclass(frozen=True)
class ServiceConfig:
    home: Path | None
    port: int
    url: str
    state_dir: Path
    env_file: Path
    workspace: Path
    image: str
    container_name: str
    env: dict[str, str] = field(default_factory=dict)

    @property
    def pid_file(self) -> Path:
        return self.state_dir / "server.pid"

    @property
    def log_file(self) -> Path:
        return self.state_dir / "server.log"

    @property
    def plist_path(self) -> Path:
        return Path("~/Library/LaunchAgents").expanduser() / f"{LAUNCHD_LABEL}.plist"

    def child_env(self) -> dict[str, str]:
        """Environment for the server process: inherited, then env-file, then
        the resolved workspace and port."""
        merged = dict(os.environ)
        merged.update(self.env)
        merged["PALETTE_WORKSPACE"] = str(self.workspace)
        merged["PORT"] = str(self.port)
        # The server's stdout is a log file in every mode, and Python
        # block-buffers when stdout is not a tty. Without this the log sits
        # hours behind the running server -- which matters because the log is
        # how anyone checks whether a build actually started. Seen in the
        # wild: a deck rendered fine and `grep draft_async server.log` showed
        # nothing, because the line was still in an 8 KB buffer.
        merged["PYTHONUNBUFFERED"] = "1"
        return merged


def resolve_config(
    *,
    home: str | Path | None = None,
    port: int | None = None,
    env_file: str | Path | None = None,
    state_dir: str | Path | None = None,
    image: str | None = None,
) -> ServiceConfig:
    """Build a config from arguments, the env file, and the environment."""
    resolved_env_file = Path(env_file).expanduser() if env_file else DEFAULT_ENV_FILE
    env = load_env_file(resolved_env_file)

    resolved_port = int(
        port
        or env.get("PALETTE_PORT")
        or os.environ.get("PALETTE_PORT")
        or contract.DEFAULT_PORT
    )
    resolved_state = Path(
        state_dir or env.get("PALETTE_STATE_DIR") or os.environ.get("PALETTE_STATE_DIR") or DEFAULT_STATE_DIR
    ).expanduser()
    resolved_home = find_home(home, env)
    workspace = Path(
        env.get("PALETTE_WORKSPACE") or os.environ.get("PALETTE_WORKSPACE") or (resolved_state / "workspace")
    ).expanduser()

    return ServiceConfig(
        home=resolved_home,
        port=resolved_port,
        url=f"http://127.0.0.1:{resolved_port}",
        state_dir=resolved_state,
        env_file=resolved_env_file,
        workspace=workspace,
        image=image or env.get("PALETTE_IMAGE") or DEFAULT_IMAGE,
        container_name=env.get("PALETTE_CONTAINER") or DEFAULT_CONTAINER_NAME,
        # Carried through so child_env(), doctor(), and start_container() can
        # reach RITS_API_KEY. Dropping it here silently starts every backend
        # without a key.
        env=env,
    )


# -- runtime discovery -----------------------------------------------------


def container_runtime() -> str | None:
    """First available container CLI. ``docker`` is often podman in disguise."""
    for candidate in ("docker", "podman"):
        if shutil.which(candidate):
            return candidate
    return None


def image_available(cfg: ServiceConfig) -> bool:
    runtime = container_runtime()
    if not runtime:
        return False
    result = subprocess.run(
        [runtime, "image", "inspect", cfg.image],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def detect_mode(cfg: ServiceConfig) -> Mode:
    """Container when an image is ready, else a local process."""
    if image_available(cfg):
        return "container"
    return "process"


def _soffice() -> str | None:
    found = shutil.which("soffice")
    if found:
        return found
    mac_path = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
    return str(mac_path) if mac_path.is_file() else None


# -- process state ---------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def running_pid(cfg: ServiceConfig) -> int | None:
    """PID from the pid file, if that process is still alive."""
    if not cfg.pid_file.is_file():
        return None
    try:
        pid = int(cfg.pid_file.read_text().strip())
    except (ValueError, OSError):
        return None
    if _pid_alive(pid):
        return pid
    cfg.pid_file.unlink(missing_ok=True)
    return None


def running_container(cfg: ServiceConfig) -> str | None:
    """Container id if one is running under our name."""
    runtime = container_runtime()
    if not runtime:
        return None
    result = subprocess.run(
        [runtime, "ps", "--filter", f"name=^{cfg.container_name}$", "--format", "{{.ID}}"],
        capture_output=True,
        text=True,
    )
    container_id = result.stdout.strip()
    return container_id or None


def launchd_loaded(cfg: ServiceConfig) -> bool:
    if platform.system() != "Darwin" or not cfg.plist_path.is_file():
        return False
    result = subprocess.run(["launchctl", "list", LAUNCHD_LABEL], capture_output=True, text=True)
    return result.returncode == 0


# -- health ----------------------------------------------------------------


def probe(cfg: ServiceConfig, timeout: float = 3.0) -> dict[str, Any] | None:
    """One /health call. ``None`` when the server is not answering."""
    try:
        return PaletteClient(cfg.url, timeout=timeout).health()
    except PaletteError:
        return None


def wait_until_healthy(cfg: ServiceConfig, timeout: float = 90.0) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        health = probe(cfg, timeout=2.0)
        if health is not None:
            return health
        time.sleep(1.0)
    return None


def status(cfg: ServiceConfig) -> dict[str, Any]:
    """Everything known about the local service, in one payload."""
    health = probe(cfg)
    pid = running_pid(cfg)
    container = running_container(cfg)
    return {
        "url": cfg.url,
        "running": health is not None,
        "mode": "container" if container else ("process" if pid else None),
        "pid": pid,
        "container": container,
        "launchd": launchd_loaded(cfg),
        "rits_key_set": bool(health.get("rits_key_set")) if health else None,
        "roster": health.get("roster") if health else None,
        "home": str(cfg.home) if cfg.home else None,
        "workspace": str(cfg.workspace),
        "env_file": str(cfg.env_file),
        "log": str(cfg.log_file),
    }


# -- preflight -------------------------------------------------------------


def doctor(cfg: ServiceConfig) -> dict[str, Any]:
    """Check everything a local build needs, before a deck fails halfway.

    Reports per-mode readiness so the caller knows which backends will work.
    """
    node = shutil.which("node")
    soffice = _soffice()
    pdftoppm = shutil.which("pdftoppm")
    runtime = container_runtime()
    has_image = image_available(cfg) if runtime else False
    pptxgenjs = bool(cfg.home and (cfg.home / "node_modules" / "pptxgenjs").is_dir())
    key = bool(cfg.env.get("RITS_API_KEY") or os.environ.get("RITS_API_KEY"))

    checks = {
        "checkout": {"ok": cfg.home is not None, "detail": str(cfg.home or "not found — set PALETTE_HOME")},
        "rits_key": {"ok": key, "detail": f"from {cfg.env_file}" if cfg.env.get("RITS_API_KEY") else
                     ("from environment" if key else f"missing — add RITS_API_KEY to {cfg.env_file}")},
        "node": {"ok": bool(node), "detail": node or "missing — brew install node"},
        "pptxgenjs": {"ok": pptxgenjs, "detail": "installed" if pptxgenjs else
                      f"missing — run `npm install` in {cfg.home or '<checkout>'}"},
        "libreoffice": {"ok": bool(soffice), "detail": soffice or "missing — brew install --cask libreoffice"},
        "poppler": {"ok": bool(pdftoppm), "detail": pdftoppm or "missing — brew install poppler"},
        "container_runtime": {"ok": bool(runtime), "detail": runtime or "missing — docker or podman"},
        "container_image": {"ok": has_image, "detail": f"{cfg.image} present" if has_image else
                            f"{cfg.image} not built — run `make docker-build`"},
    }

    process_blockers = [n for n in ("checkout", "node", "pptxgenjs", "libreoffice", "poppler")
                        if not checks[n]["ok"]]
    container_blockers = [n for n in ("container_runtime", "container_image") if not checks[n]["ok"]]

    return {
        "checks": checks,
        "modes": {
            "process": {"ready": not process_blockers, "blockers": process_blockers},
            "container": {"ready": not container_blockers, "blockers": container_blockers},
            "launchd": {
                "ready": platform.system() == "Darwin" and not process_blockers,
                "blockers": (["not macOS"] if platform.system() != "Darwin" else []) + process_blockers,
            },
        },
        # A build needs the key wherever it runs; call it out separately since
        # the server starts happily without one and only fails at model time.
        "can_build": key,
    }


# -- start / stop ----------------------------------------------------------


def _require_home(cfg: ServiceConfig) -> Path:
    if cfg.home is None:
        raise ServiceError(
            "no Palette checkout found. Set PALETTE_HOME in "
            f"{cfg.env_file}, or run from inside the repository."
        )
    return cfg.home


def start_process(cfg: ServiceConfig) -> dict[str, Any]:
    """Spawn a detached server process and record its pid."""
    home = _require_home(cfg)
    existing = running_pid(cfg)
    if existing:
        return {"started": False, "reason": "already running", "pid": existing, "mode": "process"}

    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.workspace.mkdir(parents=True, exist_ok=True)
    log_handle = cfg.log_file.open("ab")
    try:
        process = subprocess.Popen(
            [sys.executable, "-u", "app.py", "--port", str(cfg.port)],
            cwd=str(home),
            env=cfg.child_env(),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            # Detach into its own session so it outlives the caller's shell —
            # and so it never holds a parent's stdout pipe open.
            start_new_session=True,
        )
    finally:
        log_handle.close()

    cfg.pid_file.write_text(str(process.pid))
    return {"started": True, "pid": process.pid, "mode": "process", "log": str(cfg.log_file)}


def start_container(cfg: ServiceConfig) -> dict[str, Any]:
    """Run the image detached, with a restart policy standing in for launchd."""
    runtime = container_runtime()
    if not runtime:
        raise ServiceError("no container runtime found — install docker or podman.")
    if not image_available(cfg):
        raise ServiceError(f"image {cfg.image!r} not found. Build it with `make docker-build`.")

    existing = running_container(cfg)
    if existing:
        return {"started": False, "reason": "already running", "container": existing, "mode": "container"}

    # Clear a stopped container of the same name; otherwise `run` errors.
    subprocess.run([runtime, "rm", "-f", cfg.container_name], capture_output=True, text=True)

    command = [
        runtime, "run", "-d",
        "--name", cfg.container_name,
        "--restart", "unless-stopped",
        "-p", f"{cfg.port}:{CONTAINER_PORT}",
    ]
    key = cfg.env.get("RITS_API_KEY") or os.environ.get("RITS_API_KEY")
    if key:
        command += ["-e", f"RITS_API_KEY={key}"]
    command.append(cfg.image)

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise ServiceError(f"{runtime} run failed: {result.stderr.strip()[-400:]}")
    return {"started": True, "container": result.stdout.strip()[:12], "mode": "container"}


def start(cfg: ServiceConfig, mode: Mode | None = None) -> dict[str, Any]:
    chosen = mode or detect_mode(cfg)
    if chosen == "container":
        return start_container(cfg)
    if chosen == "process":
        return start_process(cfg)
    if chosen == "launchd":
        return install_launchd(cfg)
    raise ServiceError(f"unknown mode {chosen!r}")


def stop(cfg: ServiceConfig) -> dict[str, Any]:
    """Stop whatever is running, in every backend."""
    stopped: list[str] = []

    container = running_container(cfg)
    if container:
        runtime = container_runtime()
        subprocess.run([runtime, "rm", "-f", cfg.container_name], capture_output=True, text=True)
        stopped.append("container")

    pid = running_pid(cfg)
    if pid:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for _ in range(20):
            if not _pid_alive(pid):
                break
            time.sleep(0.25)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        cfg.pid_file.unlink(missing_ok=True)
        stopped.append("process")

    return {"stopped": stopped, "running": probe(cfg) is not None}


def ensure(cfg: ServiceConfig, *, mode: Mode | None = None, timeout: float = 90.0) -> dict[str, Any]:
    """Start the service if it is not already answering, then wait for health."""
    health = probe(cfg)
    if health is not None:
        return {"action": "already-running", "url": cfg.url, "health": health}

    started = start(cfg, mode)
    health = wait_until_healthy(cfg, timeout=timeout)
    if health is None:
        tail = read_logs(cfg, lines=20)
        raise ServiceError(
            f"started Palette ({started.get('mode')}) but it never became healthy at {cfg.url} "
            f"within {timeout:.0f}s.\nLast log lines:\n{tail}"
        )
    return {"action": "started", "url": cfg.url, "health": health, **started}


def read_logs(cfg: ServiceConfig, lines: int = 50) -> str:
    container = running_container(cfg)
    if container:
        runtime = container_runtime()
        result = subprocess.run(
            [runtime, "logs", "--tail", str(lines), cfg.container_name],
            capture_output=True,
            text=True,
        )
        return (result.stdout + result.stderr).strip()
    if cfg.log_file.is_file():
        return "\n".join(cfg.log_file.read_text(errors="replace").splitlines()[-lines:])
    return "(no logs yet)"


# -- launchd ---------------------------------------------------------------


def _launchd_program(cfg: ServiceConfig) -> list[str]:
    """Prefer the installed console script; fall back to the module."""
    console = shutil.which("palette-serve")
    if console:
        return [console, "--port", str(cfg.port)]
    return [sys.executable, "-m", "palette_skill.server_entry", "--port", str(cfg.port)]


def launchd_plist(cfg: ServiceConfig) -> dict[str, Any]:
    """The LaunchAgent definition.

    The RITS key is deliberately absent — the agent points at the env file and
    ``palette-serve`` reads it, so the secret lives in exactly one place with
    one set of permissions.
    """
    return {
        "Label": LAUNCHD_LABEL,
        "ProgramArguments": _launchd_program(cfg),
        "EnvironmentVariables": {
            "PALETTE_ENV_FILE": str(cfg.env_file),
            "PALETTE_HOME": str(cfg.home) if cfg.home else "",
            "PALETTE_WORKSPACE": str(cfg.workspace),
            "PORT": str(cfg.port),
        },
        "WorkingDirectory": str(cfg.home) if cfg.home else str(cfg.state_dir),
        "StandardOutPath": str(cfg.log_file),
        "StandardErrorPath": str(cfg.log_file),
        "RunAtLoad": True,
        "KeepAlive": True,
    }


def install_launchd(cfg: ServiceConfig) -> dict[str, Any]:
    if platform.system() != "Darwin":
        raise ServiceError("launchd mode is macOS-only; use process or container mode.")
    _require_home(cfg)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    cfg.workspace.mkdir(parents=True, exist_ok=True)
    cfg.plist_path.parent.mkdir(parents=True, exist_ok=True)

    with cfg.plist_path.open("wb") as handle:
        plistlib.dump(launchd_plist(cfg), handle)

    subprocess.run(["launchctl", "unload", str(cfg.plist_path)], capture_output=True, text=True)
    result = subprocess.run(["launchctl", "load", str(cfg.plist_path)], capture_output=True, text=True)
    if result.returncode != 0:
        raise ServiceError(f"launchctl load failed: {result.stderr.strip()[-400:]}")
    return {"started": True, "mode": "launchd", "plist": str(cfg.plist_path), "label": LAUNCHD_LABEL}


def uninstall_launchd(cfg: ServiceConfig) -> dict[str, Any]:
    if platform.system() != "Darwin":
        raise ServiceError("launchd mode is macOS-only.")
    if cfg.plist_path.is_file():
        subprocess.run(["launchctl", "unload", str(cfg.plist_path)], capture_output=True, text=True)
        cfg.plist_path.unlink()
        return {"uninstalled": True, "plist": str(cfg.plist_path)}
    return {"uninstalled": False, "reason": "not installed"}


# -- bootstrap -------------------------------------------------------------


ENV_TEMPLATE = """\
# Palette local service configuration. Keep this file mode 600 — it holds a secret.
# Created by `palette-skill serve init`.

# Required: bearer token for IBM RITS. Without it the server starts but every
# build fails at the first model call.
RITS_API_KEY=

# Where the Palette checkout lives. Used by process and launchd modes.
PALETTE_HOME={home}

# Bind port for the local service.
PALETTE_PORT={port}

# Session decks. Kept out of the source tree so the service is well-behaved.
PALETTE_WORKSPACE={workspace}
"""


def init_env_file(cfg: ServiceConfig, *, force: bool = False) -> dict[str, Any]:
    """Write a starter env file with locked-down permissions."""
    if cfg.env_file.is_file() and not force:
        return {"created": False, "reason": "already exists", "path": str(cfg.env_file)}
    cfg.env_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.env_file.write_text(
        ENV_TEMPLATE.format(
            home=cfg.home or "",
            port=cfg.port,
            workspace=cfg.workspace,
        ),
        encoding="utf-8",
    )
    cfg.env_file.chmod(0o600)
    return {"created": True, "path": str(cfg.env_file), "next": "add your RITS_API_KEY, then `serve start`"}


def format_status(payload: dict[str, Any]) -> str:
    """Human-readable one-liner for shells and log lines."""
    if payload.get("running"):
        mode = payload.get("mode") or "unknown"
        key = "" if payload.get("rits_key_set") else "  [!] RITS_API_KEY not set — builds will fail"
        return f"Palette is up at {payload['url']} (mode={mode}){key}"
    return f"Palette is NOT running at {payload['url']} — start it with `palette-skill serve start`"


def as_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)
