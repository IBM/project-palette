"""`pyproject.toml` has to describe code that exists.

Written after the HTTP-era skill was deleted and the packaging was not. The
metadata still named a `palette_skill.cli:main` console script and a
`palette_skill/README.md`, both long gone. Nothing complained: the wheel built,
`uv pip install` succeeded, and the only symptom was that `make install` ended
by running `palette-skill --version`, which exited 1 — the very first command
in the README and the cheatsheet, failing after doing all its work correctly.

None of this is caught by importing the package, because a dead entry point is
only resolved when someone runs it. So resolve them here.
"""

from __future__ import annotations

import importlib
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def console_scripts() -> dict[str, str]:
    return PYPROJECT["project"].get("scripts", {})


@pytest.mark.parametrize("name", sorted(console_scripts()))
def test_console_script_targets_exist(name: str) -> None:
    """`palette-skill = "palette_skill.cli:main"` outlived palette_skill/cli.py."""
    target = console_scripts()[name]
    module_name, _, attribute = target.partition(":")

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:  # pragma: no cover - the failure message is the point
        pytest.fail(f"console script {name!r} points at {module_name!r}, which does not import: {exc}")

    assert hasattr(module, attribute), (
        f"console script {name!r} points at {target!r}, but {module_name} has no {attribute!r}"
    )


def test_the_readme_it_declares_is_really_there() -> None:
    """A missing `readme` breaks the build backend, not the install that precedes it."""
    readme = PYPROJECT["project"].get("readme")
    assert readme, "no readme declared"
    assert (REPO_ROOT / readme).is_file(), f"pyproject declares readme {readme!r}, which is missing"


@pytest.mark.parametrize("package", PYPROJECT["tool"]["setuptools"]["packages"])
def test_declared_packages_exist(package: str) -> None:
    assert (REPO_ROOT / package.replace(".", "/")).is_dir(), (
        f"pyproject packages {package!r}, which is not in the repo"
    )


def test_the_version_attribute_resolves() -> None:
    attr = PYPROJECT["tool"]["setuptools"]["dynamic"]["version"]["attr"]
    module_name, _, name = attr.rpartition(".")
    module = importlib.import_module(module_name)
    assert hasattr(module, name), f"version comes from {attr!r}, which does not exist"


def test_package_data_globs_match_something() -> None:
    """`payload/*.md` kept being declared after the payload directory was deleted."""
    for package, patterns in PYPROJECT["tool"]["setuptools"].get("package-data", {}).items():
        root = REPO_ROOT / package.replace(".", "/")
        for pattern in patterns:
            assert list(root.glob(pattern)), (
                f"package-data {package}:{pattern!r} matches no files"
            )


def test_the_skill_is_not_packaged() -> None:
    """The skill is a folder of plain files, and must stay one.

    If it ever appears in the distribution there are two copies of it — the
    installed one and skills/palette/ — and they drift. Everything that
    consumes it (make skill-install, the tarball, the cuga-skills catalog)
    reads the folder directly.
    """
    packages = PYPROJECT["tool"]["setuptools"]["packages"]
    assert not any("skill" in p and "palette_skill" != p for p in packages), (
        f"the agent skill is being packaged: {packages}"
    )
    assert "palette-skill" not in console_scripts(), (
        "a console script named for the skill implies the skill is installed software; "
        "it is a folder that gets copied"
    )
