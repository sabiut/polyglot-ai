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
