"""Install the Palette skill into an agent's skills root, and detect drift.

The skill is generated and installed from this repo — it is never hand-authored
on the agent side. That is the whole anti-drift story:

    palette repo  ──build_skill──►  payload/*.md
                  ──wheel───────►  vendor/palette_skill-*.whl
                  ──install─────►  <agent>/.cuga/skills/palette/
                                   + .palette-skill.json  (what was installed,
                                                           from which commit)

``--check`` compares three things and names whichever moved:

  * the installed markdown against the manifest — did someone edit the copy?
  * the current client sources against the manifest — has the client moved on?
  * the current generated payload against the manifest — has the contract or
    config moved on?

Usage::

    python -m palette_skill.install --into ~/code/cuga-agent
    python -m palette_skill.install --into ~/code/cuga-agent --check
    python -m palette_skill.install --skills-root ~/.config/agents/skills

Symlinking the skill folder instead would look tempting and is a trap:
``Path.rglob`` stopped following directory symlinks in Python 3.13, so a
symlinked skill is discovered on 3.12 and silently vanishes on an interpreter
upgrade. Copy, and record what was copied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from palette_skill import __version__, build_skill, hosts

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent
PAYLOAD_DIR = PACKAGE_DIR / "payload"

SKILL_NAME = "palette"
MANIFEST_NAME = ".palette-skill.json"

#: Client sources whose content defines the installed wheel. Hashed together
#: into `client_fingerprint` because wheel files themselves are not
#: reproducible byte-for-byte across builds.
CLIENT_SOURCES = ("__init__.py", "contract.py", "client.py", "cli.py")

#: Default skills root relative to an agent project root, matching CUGA's
#: `[skills] root = "cuga"` preset. `--skills-root` overrides it for the
#: `agents` / `global_agents` layouts.
DEFAULT_SKILLS_SUBPATH = Path(".cuga") / "skills"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def client_fingerprint() -> str:
    """One hash over every client source file, in a fixed order."""
    digest = hashlib.sha256()
    for name in CLIENT_SOURCES:
        digest.update(name.encode())
        digest.update((PACKAGE_DIR / name).read_bytes())
    return digest.hexdigest()


def payload_hashes() -> dict[str, str]:
    """Hash of each markdown file that will be installed."""
    return {p.name: _sha256(p.read_bytes()) for p in sorted(PAYLOAD_DIR.glob("*.md"))}


def build_wheel(out_dir: Path) -> Path:
    """Build the client wheel from the checkout. Prefers uv; falls back to build."""
    out_dir.mkdir(parents=True, exist_ok=True)
    commands = [
        ["uv", "build", "--wheel", "--out-dir", str(out_dir)],
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir)],
    ]
    errors: list[str] = []
    for command in commands:
        try:
            subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True, check=True)
        except FileNotFoundError:
            errors.append(f"{command[0]}: not installed")
            continue
        except subprocess.CalledProcessError as exc:
            errors.append(f"{' '.join(command[:2])}: {exc.stderr.strip()[-400:]}")
            continue
        wheels = sorted(out_dir.glob("palette_skill-*.whl"))
        if wheels:
            return wheels[-1]
        errors.append(f"{' '.join(command[:2])}: produced no wheel")
    raise SystemExit("could not build the client wheel:\n  " + "\n  ".join(errors))


def resolve_target(into: Path | None, skills_root: Path | None, host=None) -> Path:
    """Where the skill lands, per host.

    A host's default root may be absolute (Claude Code installs globally under
    ~/.claude/skills) or relative to an agent project (CUGA uses .cuga/skills),
    so `--into` is only meaningful for the latter.
    """
    if skills_root is not None:
        return skills_root.expanduser().resolve() / SKILL_NAME

    root_spec = (host.install_root if host else str(DEFAULT_SKILLS_SUBPATH))
    if root_spec.startswith("~") or Path(root_spec).is_absolute():
        return Path(root_spec).expanduser().resolve() / SKILL_NAME

    if into is None:
        raise SystemExit(
            f"host {host.key!r} installs under <project>/{root_spec} — "
            "pass --into <agent project root>, or --skills-root <dir>"
        )
    root = into.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"--into: not a directory: {root}")
    return root / root_spec / SKILL_NAME


def build_manifest(
    wheel_name: str | None, host_key: str | None = None, base_url: str | None = None
) -> dict[str, Any]:
    return {
        "skill": SKILL_NAME,
        "host": host_key or hosts.DEFAULT_HOST,
        # Recorded so `--check` re-renders the same way it installed; a
        # deployment-pinned skill would otherwise look stale against a
        # freshly rendered unpinned one.
        "base_url": base_url,
        "package_version": __version__,
        "client_fingerprint": client_fingerprint(),
        "payload": payload_hashes(),
        "wheel": wheel_name,
        "vendored": wheel_name is not None,
        "source": {
            "repo": _git("config", "--get", "remote.origin.url") or str(REPO_ROOT),
            "commit": _git("rev-parse", "HEAD"),
            "dirty": bool(_git("status", "--porcelain")),
        },
        "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def install(
    target: Path,
    *,
    force_regenerate: bool,
    host_key: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    # Always regenerate when a non-default host is requested: the payload on
    # disk is rendered for one host at a time, so installing for another
    # without regenerating would ship the wrong execution section.
    can_vendor = build_skill.in_checkout()

    # payload/ holds exactly one host's rendering at a time. Installing for a
    # *different* host — or pinning a base URL — therefore leaves the checkout
    # describing something it should not: `make skill-check` fails, and
    # committing it ships the wrong execution section to everyone else.
    # `check()` already snapshots for this reason; installing needs it just as
    # much, and did not have it. Restore whenever we diverged.
    diverges = bool(base_url) or bool(host_key and host_key != hosts.DEFAULT_HOST)
    snapshot = {p: p.read_bytes() for p in PAYLOAD_DIR.glob("*.md")} if diverges else {}
    try:
        return _install_unguarded(
            target,
            force_regenerate=force_regenerate,
            host_key=host_key,
            base_url=base_url,
            can_vendor=can_vendor,
        )
    finally:
        for path, content in snapshot.items():
            path.write_bytes(content)


def _install_unguarded(
    target: Path,
    *,
    force_regenerate: bool,
    host_key: str | None,
    base_url: str | None,
    can_vendor: bool,
) -> dict[str, Any]:
    """The install proper. Callers go through :func:`install`, which restores payload/."""
    if force_regenerate or base_url or not can_vendor or (host_key and host_key != hosts.DEFAULT_HOST):
        build_skill.run(check=False, host=host_key, base_url=base_url, vendored=can_vendor)
    elif build_skill.run(check=True, host=host_key) != 0:
        raise SystemExit(
            "refusing to install stale content — run `make skill-build` first, "
            "or pass --regenerate to do it now."
        )

    target.mkdir(parents=True, exist_ok=True)
    vendor = target / "vendor"
    if vendor.exists():
        shutil.rmtree(vendor)

    for markdown in sorted(PAYLOAD_DIR.glob("*.md")):
        shutil.copy2(markdown, target / markdown.name)

    # A checkout can build the wheel and ship it beside the skill, so the agent
    # installs the client offline. A released package cannot build anything —
    # it *is* the client — so the skill tells the agent to pull it from an index.
    if can_vendor:
        vendor.mkdir(parents=True)
        with tempfile.TemporaryDirectory() as tmp:
            wheel = build_wheel(Path(tmp))
            shutil.copy2(wheel, vendor / wheel.name)
            manifest = build_manifest(wheel.name, host_key, base_url)
    else:
        manifest = build_manifest(None, host_key, base_url)

    (target / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def uninstall(target: Path) -> int:
    """Remove an installed skill.

    Refuses to touch a directory that has no manifest. `--skills-root` takes an
    arbitrary path, and a typo there should not delete somebody's folder — the
    manifest is the proof that we put the directory here in the first place.
    """
    manifest_path = target / MANIFEST_NAME
    if not target.exists():
        print(f"not installed: {target}")
        return 0
    if not manifest_path.is_file():
        print(
            f"refusing to remove {target}: no {MANIFEST_NAME}, so this was not installed "
            "by palette-skill. Delete it by hand if you are sure.",
            file=sys.stderr,
        )
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    shutil.rmtree(target)
    print(f"removed palette skill from {target} (host {manifest.get('host', 'unknown')})")
    return 0


@contextmanager
def _payload_rendered_for(host_key: str, base_url: str | None = None):
    """Render payload/ for one host, then put it back exactly as it was.

    payload/ holds a single host's rendering at a time, so checking a
    claude-code install means re-rendering — and leaving it that way would
    silently swap the working copy under whoever looks next (it broke the
    default-host tests exactly once). Snapshot, render, restore.
    """
    snapshot = {p: p.read_bytes() for p in PAYLOAD_DIR.glob("*.md")}
    try:
        build_skill.run(check=False, host=host_key, base_url=base_url)
        yield
    finally:
        for path, content in snapshot.items():
            path.write_bytes(content)


def check(target: Path) -> int:
    """Report drift between this repo and an installed copy. 0 = in sync."""
    manifest_path = target / MANIFEST_NAME
    if not manifest_path.is_file():
        print(f"not installed: no {MANIFEST_NAME} under {target}", file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    installed_host = manifest.get("host", hosts.DEFAULT_HOST)
    installed_base_url = manifest.get("base_url")

    with _payload_rendered_for(installed_host, installed_base_url):
        problems.extend(_compare(target, manifest, installed_host))

    if problems:
        print(f"palette skill at {target} is out of date:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("\nRun: make skill-install CUGA=<agent project root>", file=sys.stderr)
        return 1

    commit = (manifest.get("source") or {}).get("commit", "")[:9] or "unknown"
    print(
        f"palette skill at {target} is in sync "
        f"(v{__version__}, host {installed_host}, commit {commit})"
    )
    return 0


def _compare(target: Path, manifest: dict[str, Any], installed_host: str) -> list[str]:
    """Everything that can have moved, against the freshly rendered payload."""
    problems: list[str] = []

    # 1. Was the installed copy edited by hand?
    for name, expected in (manifest.get("payload") or {}).items():
        installed = target / name
        if not installed.is_file():
            problems.append(f"installed file missing: {name}")
        elif _sha256(installed.read_bytes()) != expected:
            problems.append(f"installed file edited since install: {name}")

    # 2. Has the source payload moved on?
    if payload_hashes() != (manifest.get("payload") or {}):
        problems.append("SKILL.md / reference.md changed in the Palette repo since install")

    # 3. Has the client code moved on?
    if client_fingerprint() != manifest.get("client_fingerprint"):
        problems.append("client sources changed in the Palette repo since install")

    if __version__ != manifest.get("package_version"):
        problems.append(
            f"package version moved: installed {manifest.get('package_version')}, repo {__version__}"
        )

    # 4. Would regenerating change anything? (contract.py / config.py drift)
    #    Same host as the install, or a cuga-rendered payload would look stale
    #    to a claude-code copy and vice versa.
    if build_skill.run(check=True, host=installed_host, base_url=manifest.get("base_url")) != 0:
        problems.append("generated regions are stale against contract.py / config.py")

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--into", type=Path, default=None, help="Agent project root (for hosts with a project-relative skills root)")
    parser.add_argument(
        "--host",
        default=hosts.DEFAULT_HOST,
        choices=sorted(hosts.HOSTS),
        help="Agent host to generate and install for.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Pin a deployment URL into the skill, so agents need no PALETTE_URL set.",
    )
    parser.add_argument("--skills-root", type=Path, default=None, help="Explicit skills directory")
    parser.add_argument("--check", action="store_true", help="Report drift; do not write")
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove the installed skill. Only touches directories carrying our manifest.",
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Regenerate the payload before installing instead of failing on stale content",
    )
    args = parser.parse_args(argv)

    host = hosts.get(args.host)
    target = resolve_target(args.into, args.skills_root, host)
    if args.uninstall:
        return uninstall(target)
    if args.check:
        return check(target)

    manifest = install(
        target, force_regenerate=args.regenerate, host_key=host.key, base_url=args.base_url
    )
    commit = (manifest["source"]["commit"] or "unknown")[:9]
    dirty = " (working tree dirty)" if manifest["source"]["dirty"] else ""
    print(f"installed palette skill -> {target}")
    print(f"  host    {host.label} — {host.install_note}")
    print(f"  palette {manifest['base_url'] or 'not pinned (agents read $PALETTE_URL)'}")
    print(f"  version {manifest['package_version']}  commit {commit}{dirty}")
    print(f"  wheel   {manifest['wheel'] or 'not vendored — agents pull palette-skill from an index'}")
    print(f"  files   {', '.join(sorted(manifest['payload']))}, {MANIFEST_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
