"""Install-flow contract tests.

The packaging story has three install paths (deb / rpm / AppImage)
that each carry their own dependency declarations. These tests pin
the contract so a stray commit can't silently delete a Recommends
line or drop a Python dep that the app needs to come up cleanly on
a fresh system.

The bar is "the user installs the .deb and every feature works
without the first-launch dependency dialog popping up" — anything
that breaks that promise should fail one of these tests.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

# The repo root has a stable layout — walk up from this file rather
# than hard-coding a path. Keeps tests runnable from any cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_pyproject() -> dict:
    with (_REPO_ROOT / "pyproject.toml").open("rb") as f:
        return tomllib.load(f)


def _load_debian_control() -> str:
    return (_REPO_ROOT / "packaging" / "debian" / "control").read_text(encoding="utf-8")


def _load_rpm_spec() -> str:
    return (_REPO_ROOT / "packaging" / "rpm" / "polyglot-ai.spec").read_text(encoding="utf-8")


# ── Python dependency contract ─────────────────────────────────────


def test_pyproject_lists_qt_runtime_deps():
    """Every Qt feature the app uses must be in ``dependencies`` so
    the postinst bundle includes it. The deb/rpm install isolated
    venvs that can't reach system-Python Qt modules, so anything
    declared at the OS level (``python3-pyqt6.qtmultimedia`` etc.)
    won't help — the dep MUST be in pyproject.
    """
    deps = _load_pyproject()["project"]["dependencies"]
    dep_names = [d.split(">=")[0].split("==")[0].split(">")[0].strip().lower() for d in deps]
    # Hard Qt deps — without these the app doesn't start at all.
    assert "pyqt6" in dep_names, "PyQt6 missing from pyproject deps"
    # Optional Qt extras that ship features users expect by default.
    # PyQt6-WebEngine: Claude (subscription) embedded view.
    assert "pyqt6-webengine" in dep_names, (
        "PyQt6-WebEngine missing from pyproject deps — the Claude "
        "subscription panel will silently fall back to 'open in "
        "browser' mode for every installed user. Either restore "
        "the dep or document the regression in the panel's "
        "docstring."
    )


def test_pyproject_lists_provider_sdk_deps():
    """The four primary AI providers must be present so first-run
    works without a separate ``pip install`` step."""
    deps = _load_pyproject()["project"]["dependencies"]
    dep_names = [d.split(">=")[0].split("==")[0].split(">")[0].strip().lower() for d in deps]
    for required in ("openai", "anthropic", "google-genai"):
        assert required in dep_names, f"{required!r} provider SDK missing from pyproject deps"


def test_pyproject_lists_arduino_deps():
    """pyserial + mpremote come bundled — the Arduino panel's
    board detection depends on them and the first-run dialog had
    a regression where it kept nagging because pyserial wasn't
    a hard dep."""
    deps = _load_pyproject()["project"]["dependencies"]
    dep_names = [d.split(">=")[0].split("==")[0].split(">")[0].strip().lower() for d in deps]
    assert "pyserial" in dep_names
    assert "mpremote" in dep_names


# ── Debian Recommends contract ─────────────────────────────────────


def test_debian_recommends_includes_core_runtime_tools():
    """A fresh ``apt install ./polyglot-ai_*.deb`` should pre-pull
    every tool the app ships features for, so the post-install
    dependency dialog is rarely-needed.

    Each entry below corresponds to a panel/feature; removing one
    here means that feature stays broken until the user separately
    installs the dep, which we've decided not to ship that way.
    """
    control = _load_debian_control()
    # Extract the Recommends line (multi-line, comma-separated)
    # by finding the field and reading until the next header.
    rec_block = _extract_field(control, "Recommends")
    rec_tokens = {t.strip() for t in rec_block.replace("\n", ",").split(",") if t.strip()}

    expected = {
        # Already in place before this commit — keep them locked in.
        "git",
        "arduino-cli",
        "ffmpeg",
        "gstreamer1.0-plugins-good",
        "gstreamer1.0-plugins-bad",
        "gstreamer1.0-libav",
        # Added so the first-launch dependency dialog stops nagging
        # about Node (every MCP server needs it) and gh (CI/CD panel).
        "nodejs",
        "npm",
        "gh",
        # System Qt WebEngine — supplements the bundled PyQt6-WebEngine
        # wheel for users who run from system Python.
        "python3-pyqt6.qtwebengine",
    }
    missing = expected - rec_tokens
    assert not missing, (
        f"debian/control Recommends is missing expected entries: {missing}. "
        f"These were added to remove first-launch nags — restoring them "
        f"means every freshly-installed user sees the dependency dialog "
        f"complain again. If the dep was intentionally removed, also "
        f"update this test and the user-facing docs in packaging/INSTALL.md."
    )


# ── RPM Recommends contract ────────────────────────────────────────


def test_debian_depends_on_glib():
    """PyQt6's bundled Qt links libglib-2.0.so.0, and nothing else in
    Depends pulls it in: on a minimal install the app died at import
    with "libglib-2.0.so.0: cannot open shared object file". Debian 13
    and Ubuntu 24.04 renamed the package (64-bit time_t), so both names
    are needed.
    """
    depends = [d.strip() for d in _extract_field(_load_debian_control(), "Depends").split(",")]
    assert "libglib2.0-0t64 | libglib2.0-0" in depends


def test_debian_depends_on_what_qt_display_plugins_link():
    """``ldd`` on the bundled Qt in a bare Debian 12/13 and Ubuntu
    24.04/26.04 install showed these missing: three for the X11 plugin
    (without them: "Could not load the Qt platform plugin xcb") and one
    for the Wayland plugin. A desktop usually has them; nothing in
    Depends guaranteed it.
    """
    depends = {d.strip() for d in _extract_field(_load_debian_control(), "Depends").split(",")}
    assert {"libxcb-icccm4", "libxcb-keysyms1", "libxcb-shape0", "libwayland-cursor0"} <= depends


def test_readme_installs_packages_with_the_package_manager():
    """``rpm -i`` is refused by Fedora 45 for an unsigned download, and
    neither it nor ``dpkg -i`` installs the system libraries."""
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text()
    assert "sudo dnf install ./polyglot-ai-*.rpm" in readme
    assert "sudo apt install ./polyglot-ai_*.deb" in readme
    assert "sudo rpm -i" not in readme and "sudo dpkg -i" not in readme


def test_rpm_recommends_includes_core_runtime_tools():
    """Same contract as Debian, in the rpm spec syntax. The rpm
    spec uses one ``Recommends:`` line per entry — we just grep
    for each expected name."""
    spec = _load_rpm_spec()

    expected = (
        "git",
        "arduino-cli",
        "ffmpeg",
        "gstreamer1-plugins-good",
        "nodejs",
        "npm",
        # Fedora renamed gh→gh-cli mid-version; spec lists both. Test
        # only needs one of them to be present (whichever resolves).
        ("gh-cli", "gh"),
        # Same for WebEngine — the package was renamed across distros.
        ("python3-pyqt6-webengine", "python3-qt6-webengine"),
    )

    for entry in expected:
        if isinstance(entry, tuple):
            # Any of the alternatives is fine.
            present = any(f"Recommends:     {e}" in spec for e in entry)
            assert present, (
                f"rpm spec is missing Recommends for any of {entry}. "
                f"If the package was renamed again, update this list."
            )
        else:
            assert f"Recommends:     {entry}" in spec, (
                f"rpm spec is missing Recommends: {entry!r}. See "
                f"test_debian_recommends_includes_core_runtime_tools for "
                f"context on why these matter."
            )


# ── Helpers ────────────────────────────────────────────────────────


def _extract_field(control_text: str, field_name: str) -> str:
    """Pull a Debian control file field's value (handles continuation
    lines that start with a space).

    Why not use ``apt-pkg`` / ``python-debian``: those are heavyweight
    deps for a single grep-with-folding. Inline parsing matches the
    field grammar exactly and stays under ten lines.
    """
    lines = control_text.splitlines()
    out: list[str] = []
    capturing = False
    prefix = f"{field_name}:"
    for line in lines:
        if line.startswith(prefix):
            out.append(line[len(prefix) :].strip())
            capturing = True
            continue
        if capturing:
            if line.startswith(" ") or line.startswith("\t"):
                out.append(line.strip())
            else:
                break
    return "\n".join(out)


def _rpm_scriptlet(name: str, spec_text: str | None = None) -> str:
    """Body of one %scriptlet section in the spec ('' if absent)."""
    import re

    spec = spec_text or (_REPO_ROOT / "packaging" / "rpm" / "polyglot-ai.spec").read_text()
    body: list[str] = []
    inside = False
    for line in spec.splitlines():
        if re.fullmatch(r"%[a-z]+", line.strip()):
            if inside:
                break
            inside = line.strip() == name
            continue
        if inside:
            body.append(line)
    return "\n".join(body)


def test_rpm_upgrade_does_not_delete_the_new_venv():
    """rpm -U runs the new %post, then the OLD %preun. A %preun that deletes
    the venv unconditionally wiped the app on every dnf upgrade."""
    preun = _rpm_scriptlet("%preun")
    assert 'if [ "$1" -eq 0 ]' in preun, "%preun must only clean up on a real uninstall"
    assert "rm -rf /opt/polyglot-ai/venv" in preun


def test_rpm_builds_the_venv_after_old_scriptlets_run():
    """Releases <= 0.18.15 carry the unguarded %preun; only %posttrans runs
    after it, so that's where the venv must be built."""
    assert "python3 -m venv" in _rpm_scriptlet("%posttrans")
    assert "python3 -m venv" not in _rpm_scriptlet("%post")


# ── Wheel bundle contract ──────────────────────────────────────────
#
# 0.18.15–0.18.19 shipped packages whose bundle held wheels for the build
# machine's Python only: the pinned download failed for every version
# (the PyQt6 wheels need manylinux_2_34, the platform list stopped at
# 2_28) and a fallback hid it. Every other distro installed from PyPI,
# unpinned. These pin the pieces that prevent a quiet repeat.


def _packaging(name: str) -> str:
    return (_REPO_ROOT / "packaging" / name).read_text(encoding="utf-8")


def _code(script: str) -> str:
    """A shell script without its comment lines."""
    return "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))


def test_package_builds_bundle_through_the_checked_script():
    for script in ("build_deb.sh", "build_rpm.sh"):
        text = _packaging(script)
        assert "bundle_wheels.sh" in text, script
        # No second, unchecked route into the bundle.
        assert "pip download" not in text, f"{script} must not download wheels itself"
        assert "WHEELS_OK" not in text, f"{script} still has the silent fallback"


def test_bundle_covers_the_pythons_the_target_distros_ship():
    import re

    # Debian 12 → 3.11, Ubuntu 24.04 / RHEL 10 → 3.12, Debian 13 → 3.13,
    # Ubuntu 26.04 / Fedora 43–44 → 3.14.
    for script in ("build_deb.sh", "build_rpm.sh"):
        call = re.search(r'bundle_wheels\.sh"? [^\n]*', _code(_packaging(script))).group(0)
        required = call.split("--try")[0]
        for version in ("3.11", "3.12", "3.13", "3.14"):
            assert f" {version}" in required, f"{script} must require Python {version}"
    # The newest Fedora's Python is usually ahead of PyPI's wheels: the
    # rpm covers it best effort, built in the release's own container.
    assert '--try "$HOST_PY"' in _packaging("build_rpm.sh")


def test_bundle_script_fails_loudly_and_verifies_offline():
    text = _packaging("bundle_wheels.sh")
    assert "set -euo pipefail" in text
    # The pinned PyQt6 wheels are manylinux_2_34; a lower cap rejects them.
    import re

    assert int(re.search(r"^GLIBC_MINOR=(\d+)", text, re.M).group(1)) >= 34
    # Each version's bundle is resolved again from the directory alone.
    assert "--no-index --find-links" in text
    assert "exit 1" in text and "::error::" in text


def test_installers_use_the_tested_pins_offline_first():
    postinst = _code(_packaging("debian/postinst"))
    posttrans = _code(_rpm_scriptlet("%posttrans"))
    for name, script in (("postinst", postinst), ("%posttrans", posttrans)):
        offline = script.index("--no-index")
        assert '--constraint "$PINS"' in script[offline : offline + 200], name
        # Online only after the offline attempt, and pinned before unpinned.
        assert offline < script.index("--only-binary :all:"), name
    assert "constraints.txt" in _packaging("build_deb.sh")
    assert "/opt/polyglot-ai/constraints.txt" in _load_rpm_spec().split("%files")[1]


def test_constraints_give_one_version_per_package_on_every_python():
    """A marker split (the 3.15 pins) must never leave two pins active."""
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    lines = [
        line.split("#")[0].strip()
        for line in (_REPO_ROOT / "constraints.txt").read_text().splitlines()
        if line.split("#")[0].strip()
    ]
    for version in ("3.11", "3.12", "3.13", "3.14", "3.15"):
        env = {"python_version": version, "python_full_version": f"{version}.0"}
        seen: dict[str, str] = {}
        for line in lines:
            req = Requirement(line)
            if req.marker is not None and not req.marker.evaluate(env):
                continue
            name = canonicalize_name(req.name)
            assert name not in seen, f"{name} pinned twice for Python {version}"
            seen[name] = str(req.specifier)
            assert str(req.specifier).startswith("=="), f"{name} is not an exact pin"
        # The split packages still resolve to something on each version.
        for name in ("asyncpg", "pydantic", "pydantic-core"):
            assert name in seen, f"{name} has no pin for Python {version}"


def test_rpm_launcher_points_at_the_final_venv(tmp_path):
    """%posttrans builds the venv as ``venv.new`` and renames it. pip had
    written the temporary path into every script's ``#!`` line, so on
    0.18.16–0.18.19 ``polyglot-ai`` died with "No such file or directory"."""
    import shutil
    import subprocess
    import sys

    import pytest

    if sys.platform != "linux" or not shutil.which("bash"):
        pytest.skip("needs GNU sed and bash")
    posttrans = _rpm_scriptlet("%posttrans")
    rename = posttrans.index('mv "$NEW_VENV" "$VENV_DIR"')
    snippet = posttrans[rename : posttrans.index("ln -sf", rename)]

    new, final = tmp_path / "venv.new", tmp_path / "venv"
    (new / "bin").mkdir(parents=True)
    (new / "bin" / "polyglot-ai").write_text(f"#!{new}/bin/python3\nimport polyglot_ai\n")
    (new / "pyvenv.cfg").write_text(f"command = /usr/bin/python3 -m venv {new}\n")
    binary = b"\x7fELF\x00\x01\x02" + str(new).encode() + b"\x00\xff"
    (new / "bin" / "ruff").write_bytes(binary)

    subprocess.run(["bash", "-ec", f'NEW_VENV="{new}"; VENV_DIR="{final}"\n{snippet}'], check=True)
    launcher = (final / "bin" / "polyglot-ai").read_text()
    assert launcher.startswith(f"#!{final}/bin/python3\n")
    assert "venv.new" not in launcher
    assert "venv.new" not in (final / "pyvenv.cfg").read_text()
    assert (final / "bin" / "ruff").read_bytes() == binary  # real binaries untouched


def test_packaging_scripts_parse():
    import shutil
    import subprocess

    import pytest

    if not shutil.which("bash"):
        pytest.skip("needs bash")
    for name in ("bundle_wheels.sh", "build_deb.sh", "build_rpm.sh", "debian/postinst"):
        result = subprocess.run(
            ["bash", "-n", str(_REPO_ROOT / "packaging" / name)], capture_output=True, text=True
        )
        assert result.returncode == 0, f"{name}: {result.stderr}"
