"""Shippable artifacts.

A release has to be usable by someone who has never cloned Palette. Two shapes:

  * a **tarball** per host — untar into a skills root and you are done, offline,
    wheel included. This is how skills.sh installs land, and it is what a
    consumer actually wants.
  * a **wheel** — ``pip install palette-skill`` then
    ``python -m palette_skill.install``. No vendored wheel in that mode, so the
    skill has to tell the agent to pull the client from an index instead.

The failure this guards against is subtle: a release that quietly needs the
checkout it was built from. Nothing about it looks wrong until someone else
tries to use it.
"""

from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from palette_skill import __version__, build_skill, hosts, install, release  # noqa: E402


class TestReleaseShape:
    """Assembly logic, without paying for a wheel build."""

    def test_every_host_gets_an_artifact_name(self) -> None:
        for key in hosts.HOSTS:
            assert f"-{key}.tar.gz" in f"palette-skill-{__version__}-{key}.tar.gz"

    def test_server_regions_are_frozen_outside_a_checkout(self) -> None:
        """A consumer has no config.py, so those regions must not be re-rendered."""
        assert "models" in build_skill.SERVER_REGIONS
        assert "examples" in build_skill.SERVER_REGIONS

    def test_this_repo_is_a_checkout(self) -> None:
        assert build_skill.in_checkout(), "releases can only be built from a checkout"

    def test_release_refuses_outside_a_checkout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(build_skill, "in_checkout", lambda: False)
        with pytest.raises(SystemExit, match="checkout"):
            release.build()


class TestNonVendoredRendering:
    """Installed from a wheel, there is nothing to vendor."""

    def _render(self, *, vendored: bool) -> str:
        build_skill._HOST = hosts.get("cuga")
        build_skill._VENDORED = vendored
        try:
            return build_skill.render_execution()
        finally:
            build_skill._VENDORED = True

    def test_vendored_build_points_at_the_local_wheel(self) -> None:
        assert "vendor/palette_skill-*.whl" in self._render(vendored=True)

    def test_unvendored_build_points_at_the_index(self) -> None:
        rendered = self._render(vendored=False)
        assert "uv pip install palette-skill" in rendered
        assert "vendor/" not in rendered, (
            "a consumer installing from PyPI has no vendor directory — telling them "
            "to install from one sends the agent looking for a file that is not there"
        )


class TestPinnedDeployment:
    """A pinned-remote artifact must not carry local-only advice.

    `serve ensure` on a remote-pinned skill starts a *second*, local Palette
    with none of the user's data — worse than saying nothing.
    """

    def _render(self, url: str | None) -> str:
        build_skill._HOST = hosts.get("cuga")
        build_skill._BASE_URL = url
        try:
            return build_skill.render_unreachable()
        finally:
            build_skill._BASE_URL = None

    def test_unpinned_covers_both_paths(self) -> None:
        rendered = self._render(None)
        assert "A remote deployment" in rendered and "A local service" in rendered
        assert "serve ensure" in rendered

    def test_remote_pinned_drops_local_commands(self) -> None:
        rendered = self._render("https://palette.example.cloud")
        assert "palette.example.cloud" in rendered
        assert "serve ensure" not in rendered, "local start-up advice is wrong for a remote pin"
        assert "nothing to start locally" in rendered

    def test_loopback_pin_keeps_local_commands(self) -> None:
        """Pinning localhost is still a local service — the advice applies."""
        assert "serve ensure" in self._render("http://127.0.0.1:18814")

    @pytest.mark.parametrize(
        ("url", "remote"),
        [(None, False), ("http://127.0.0.1:18814", False), ("http://localhost:9", False),
         ("https://palette.example.cloud", True), ("http://10.0.0.4:18814", True)],
    )
    def test_remote_detection(self, url, remote) -> None:
        assert build_skill._is_remote(url) is remote


@pytest.mark.slow
class TestBuiltArtifacts:
    """Actually build a release and open it. Slow — it compiles a wheel."""

    @pytest.fixture(scope="class")
    def artifacts(self, tmp_path_factory) -> list[Path]:
        built = release.build(host_keys=["cuga", "claude-code"])
        yield built
        # Leave the payload as the default host, exactly as release.build does.
        build_skill.run(check=False, host=hosts.DEFAULT_HOST)

    def test_produces_a_wheel_and_a_tarball_per_host(self, artifacts) -> None:
        assert sum(1 for a in artifacts if a.suffix == ".whl") == 1
        tarballs = [a for a in artifacts if a.name.endswith(".tar.gz")]
        prefix = f"palette-skill-{__version__}-"
        built = {a.name.removeprefix(prefix).removesuffix(".tar.gz") for a in tarballs}
        assert built == {"cuga", "claude-code"}

    def test_tarball_is_a_complete_skill_folder(self, artifacts) -> None:
        tarball = next(a for a in artifacts if a.name.endswith("-cuga.tar.gz"))
        with tarfile.open(tarball) as archive:
            names = archive.getnames()
        for expected in (
            "palette/SKILL.md",
            "palette/reference.md",
            f"palette/{install.MANIFEST_NAME}",
        ):
            assert expected in names, f"{expected} missing from the release tarball"
        assert any(n.startswith("palette/vendor/") and n.endswith(".whl") for n in names), (
            "the tarball must carry the client wheel, or the agent cannot install offline"
        )

    def test_tarball_carries_its_host_vocabulary(self, artifacts) -> None:
        for host_key, expected, forbidden in (
            ("cuga", "run_command", "`Bash`"),
            ("claude-code", "Bash", "run_command"),
        ):
            tarball = next(a for a in artifacts if a.name.endswith(f"-{host_key}.tar.gz"))
            with tarfile.open(tarball) as archive:
                skill = archive.extractfile("palette/SKILL.md").read().decode()
            assert expected in skill, f"{host_key} tarball is missing {expected!r}"
            assert forbidden not in skill, f"{host_key} tarball leaked {forbidden!r}"

    def test_manifest_marks_it_a_release(self, artifacts) -> None:
        tarball = next(a for a in artifacts if a.name.endswith("-cuga.tar.gz"))
        with tarfile.open(tarball) as archive:
            manifest = json.loads(
                archive.extractfile(f"palette/{install.MANIFEST_NAME}").read().decode()
            )
        assert manifest["release"] is True
        assert manifest["host"] == "cuga"
        assert manifest["vendored"] is True
        assert manifest["package_version"] == __version__
