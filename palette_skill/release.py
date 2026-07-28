"""Build shippable skill artifacts.

Produces, into ``dist/``:

  * ``palette_skill-<version>-py3-none-any.whl`` — the client, for PyPI or a
    direct install. Carries the payload as package data, so
    ``pip install palette-skill && python -m palette_skill.install --host X``
    works with no Palette checkout anywhere.

  * ``palette-skill-<version>-<host>.tar.gz`` — a ready-to-drop skill folder,
    one per host. Untar it into a skills root and the agent has everything,
    including the client wheel, with no network and no build step. This is the
    shape skills.sh installs take, and it is what a consumer wants.

A release is pinned to the Palette it was built from. The model menus and
example plans inside SKILL.md are rendered from that checkout's ``config.py``
and then frozen — a skill advertising ``palette-qwen-32b`` has to ship with a
server that actually serves it. The version therefore tracks Palette, not the
client.

Two modes, matching the two loops:

    python -m palette_skill.release                     # local build, overwrites
    python -m palette_skill.release --version 0.2.0     # a release others rely on

Without ``--version`` this is a throwaway build for your own testing. With one,
it writes ``__version__``, refuses a dirty tree, and refuses to reuse a version
already present in ``dist/`` — because the version is in every artifact's
filename, and two releases sharing one cannot be told apart by the person you
hand them to.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from palette_skill import __version__, build_skill, hosts, install

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST = REPO_ROOT / "dist"
INIT_PY = Path(__file__).resolve().parent / "__init__.py"

#: X.Y.Z. The version ends up in every artifact filename, so it is the only
#: thing distinguishing two releases before anyone opens them.
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def set_version(version: str) -> None:
    """Rewrite ``__version__`` in place.

    The version is not metadata here — it is in every artifact's filename, so
    two releases that share one are indistinguishable in ``dist/`` and the
    manifest's "package version moved" check can never fire.
    """
    if not VERSION_RE.match(version):
        raise SystemExit(f"version must look like 1.2.3, got {version!r}")
    text = INIT_PY.read_text(encoding="utf-8")
    updated, count = re.subn(
        r'^__version__ = "[^"]+"$', f'__version__ = "{version}"', text, count=1, flags=re.M
    )
    if count != 1:
        raise SystemExit(f"could not find __version__ in {INIT_PY}")
    INIT_PY.write_text(updated, encoding="utf-8")


def _assert_publishable(version: str) -> None:
    """Guard the things that make a release reproducible for someone else.

    Both failures here are silent otherwise: a dirty tree produces an artifact
    whose recorded commit does not describe its contents, and a repeated
    version overwrites `dist/` with different bytes under the same name.

    Cheapest check first: a typo'd version should be reported straight away,
    not held back until you have committed everything for an unrelated reason.
    """
    if not VERSION_RE.match(version):
        raise SystemExit(f"version must look like 1.2.3, got {version!r}")
    dirty = _git("status", "--porcelain")
    if dirty:
        raise SystemExit(
            "working tree is dirty — a released artifact records a commit that "
            "would not describe its contents.\n"
            f"{dirty}\n\nCommit or stash, then retry."
        )
    existing = sorted(DIST.glob(f"*-{version}-*.tar.gz")) + sorted(
        DIST.glob(f"palette_skill-{version}-*.whl")
    )
    if existing:
        names = ", ".join(p.name for p in existing)
        raise SystemExit(
            f"{version} is already built: {names}\n"
            "Bump VERSION, or delete those files if you are certain they were never shared."
        )


def _skill_dir(staging: Path, host_key: str, wheel: Path, base_url: str | None) -> Path:
    """Assemble one host's skill folder, exactly as it should land on disk."""
    build_skill.run(check=False, host=host_key, base_url=base_url, vendored=True)

    target = staging / install.SKILL_NAME
    (target / "vendor").mkdir(parents=True, exist_ok=True)
    for markdown in sorted(install.PAYLOAD_DIR.glob("*.md")):
        shutil.copy2(markdown, target / markdown.name)
    shutil.copy2(wheel, target / "vendor" / wheel.name)

    manifest = install.build_manifest(wheel.name, host_key, base_url)
    manifest["release"] = True
    (target / install.MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return target


def build(base_url: str | None = None, host_keys: list[str] | None = None) -> list[Path]:
    if not build_skill.in_checkout():
        raise SystemExit(
            "releases must be built from a Palette checkout — the model menus and "
            "example plans come from the server's config.py."
        )

    DIST.mkdir(exist_ok=True)
    artifacts: list[Path] = []

    wheel = install.build_wheel(DIST)
    artifacts.append(wheel)
    print(f"wheel    {wheel.name}")

    for host_key in host_keys or sorted(hosts.HOSTS):
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            _skill_dir(staging, host_key, wheel, base_url)
            tarball = DIST / f"palette-skill-{__version__}-{host_key}.tar.gz"
            with tarfile.open(tarball, "w:gz") as archive:
                archive.add(staging / install.SKILL_NAME, arcname=install.SKILL_NAME)
            artifacts.append(tarball)
            size = tarball.stat().st_size
            print(f"skill    {tarball.name}  ({size / 1024:.0f} KB)")

    # Leave the working payload as the default host rendered it, so a release
    # never silently changes what the next `make skill-install` would ship.
    build_skill.run(check=False, host=hosts.DEFAULT_HOST)
    return artifacts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Pin a deployment URL into every artifact, so consumers need no env var.",
    )
    parser.add_argument(
        "--host",
        action="append",
        dest="host_keys",
        choices=sorted(hosts.HOSTS),
        help="Build only this host (repeatable). Default: all of them.",
    )
    parser.add_argument(
        "--version",
        default=None,
        help=(
            "Cut a real release at this version (X.Y.Z). Writes __version__, "
            "requires a clean tree, and refuses to overwrite existing artifacts. "
            "Omit for throwaway local builds."
        ),
    )
    args = parser.parse_args(argv)

    if args.version:
        _assert_publishable(args.version)
        set_version(args.version)
        # __version__ was read at import; re-read so filenames use the new one.
        import importlib

        import palette_skill

        importlib.reload(palette_skill)
        globals()["__version__"] = palette_skill.__version__
        print(f"version  {args.version}")

    artifacts = build(args.base_url, args.host_keys)
    version = globals()["__version__"]
    print(f"\n{len(artifacts)} artifact(s) in {DIST}")
    print("\nConsume a tarball the way you would a skills.sh skill:")
    print(f"  tar xzf {DIST.name}/palette-skill-{version}-cuga.tar.gz -C <skills-root>/")
    if args.version:
        print(f"\nCommit the bump:  git commit -am 'release {version}' && git tag v{version}")
    else:
        print("\nLocal build — pass --version X.Y.Z to cut one others can rely on.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
