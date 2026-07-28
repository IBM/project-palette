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

    python -m palette_skill.release
    python -m palette_skill.release --base-url https://palette.example.cloud
"""

from __future__ import annotations

import argparse
import json
import shutil
import tarfile
import tempfile
from pathlib import Path

from palette_skill import __version__, build_skill, hosts, install

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST = REPO_ROOT / "dist"


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
    args = parser.parse_args(argv)

    artifacts = build(args.base_url, args.host_keys)
    print(f"\n{len(artifacts)} artifact(s) in {DIST}")
    print("\nConsume a tarball the way you would a skills.sh skill:")
    print(f"  tar xzf {DIST.name}/palette-skill-{__version__}-cuga.tar.gz -C <skills-root>/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
