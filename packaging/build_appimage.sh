#!/bin/bash
# Build an AppImage for Polyglot AI
# Requires: python3, wget (to download appimagetool)
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

VERSION=$(python3 -c "
import tomllib
with open('$PROJECT_DIR/pyproject.toml', 'rb') as f:
    print(tomllib.load(f)['project']['version'])
")

echo "Building AppImage for polyglot-ai v${VERSION}..."

# Build wheel
cd "$PROJECT_DIR"
python3 -m pip install build
python3 -m build --wheel

# Create AppDir structure
APPDIR="$SCRIPT_DIR/Polyglot_AI.AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr"

# Bundle a relocatable CPython (python-build-standalone), pinned and
# checksum-verified. The AppImage used to bundle `python3 -m venv
# --copies`, which copies only the interpreter binary, not the standard
# library, so the AppImage died at startup with "Failed to import
# encodings module" on every machine. A standalone build carries its
# own stdlib and works from wherever the AppImage is mounted.
PBS_RELEASE="20261026"
PBS_PYTHON="3.12.16"
PBS_SHA256="6a79e922e50006b601f29e688407659da1c2b86ebcd4e9c7254cf234be8b89de"
PBS_FILE="cpython-${PBS_PYTHON}+${PBS_RELEASE}-x86_64-unknown-linux-gnu-install_only.tar.gz"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${PBS_FILE/+/%2B}"
PBS_CACHE="$SCRIPT_DIR/appimage/$PBS_FILE"
if [ ! -f "$PBS_CACHE" ]; then
    echo "Downloading standalone Python ${PBS_PYTHON}..."
    wget -q -O "$PBS_CACHE.part" "$PBS_URL"
    mv "$PBS_CACHE.part" "$PBS_CACHE"
fi
echo "${PBS_SHA256}  ${PBS_CACHE}" | sha256sum -c - >/dev/null || {
    echo "Checksum mismatch for $PBS_FILE — refusing to bundle it." >&2
    rm -f "$PBS_CACHE"
    exit 1
}
# The tarball unpacks to python/{bin,lib,include,share}; put that at usr/.
tar -xzf "$PBS_CACHE" -C "$APPDIR/usr" --strip-components=1
PY="$APPDIR/usr/bin/python3"

# Full hicolor size set — appimaged / AppImageLauncher pick the
# closest size when integrating, and a 24 px tray would otherwise
# downscale 256 → blurry.
mkdir -p "$APPDIR/usr/share/applications"
for sz in 16 32 48 128 256 512; do
    mkdir -p "$APPDIR/usr/share/icons/hicolor/${sz}x${sz}/apps"
done
mkdir -p "$APPDIR/usr/share/icons/hicolor/scalable/apps"

# Pre-download every wheel into a sibling directory first, then
# install with ``--no-index --find-links`` so the build itself is
# the only step that needs network. Versions come from
# constraints.txt — the exact set CI tested — like the .deb / .rpm.
mkdir -p "$SCRIPT_DIR/appimage/wheels"
echo "Pre-downloading wheels for offline AppImage install..."
"$PY" -m pip download \
    --constraint "$PROJECT_DIR/constraints.txt" \
    --dest "$SCRIPT_DIR/appimage/wheels" \
    --only-binary=:all: \
    "$PROJECT_DIR/dist/"*.whl

# Install entirely from the bundled wheels — no live PyPI access.
# Into the bundled interpreter's own site-packages (no venv).
"$PY" -m pip install --no-index --no-warn-script-location \
    --constraint "$PROJECT_DIR/constraints.txt" \
    --find-links "$SCRIPT_DIR/appimage/wheels" \
    "$PROJECT_DIR/dist/"*.whl

# Smoke-test before packaging: start the real app through the same
# interpreter, flags and entry point AppRun uses, headless, in a clean
# environment and a throwaway home, and require it to finish starting
# up. A broken interpreter (the old venv layout) or a launcher that
# never reaches main() (the old ``-m polyglot_ai.app``) now fails the
# build instead of shipping.
SMOKE_HOME="$(mktemp -d)"
SMOKE_OUT="$SMOKE_HOME/startup.txt"
if ! env -i HOME="$SMOKE_HOME" PATH=/usr/bin:/bin QT_QPA_PLATFORM=offscreen \
        POLYGLOT_AI_DISABLE_UPDATE_CHECK=1 POLYGLOT_AI_STARTUP_TIMING=1 \
        POLYGLOT_AI_EXIT_AFTER_STARTUP=1 \
        timeout 180 "$PY" -s -E -m polyglot_ai > "$SMOKE_OUT" 2>&1 \
   || ! grep -q "total since app import" "$SMOKE_OUT"; then
    echo "AppImage smoke test failed — the bundled app did not start:" >&2
    tail -40 "$SMOKE_OUT" >&2
    rm -rf "$SMOKE_HOME"
    exit 1
fi
grep "total since" "$SMOKE_OUT"
rm -rf "$SMOKE_HOME"
echo "AppImage runtime OK"

# Copy desktop file and icons
cp "$SCRIPT_DIR/appimage/polyglot-ai.desktop" "$APPDIR/"
cp "$SCRIPT_DIR/appimage/polyglot-ai.desktop" "$APPDIR/usr/share/applications/"
for sz in 16 32 48 128 256 512; do
    if [ -f "$SCRIPT_DIR/assets/polyglot-ai-${sz}.png" ]; then
        cp "$SCRIPT_DIR/assets/polyglot-ai-${sz}.png" \
           "$APPDIR/usr/share/icons/hicolor/${sz}x${sz}/apps/polyglot-ai.png"
    fi
done
if [ -f "$SCRIPT_DIR/assets/polyglot-ai.svg" ]; then
    cp "$SCRIPT_DIR/assets/polyglot-ai.svg" \
       "$APPDIR/usr/share/icons/hicolor/scalable/apps/polyglot-ai.svg"
fi

# Top-level icon. The AppImage spec requires a PNG at the AppDir
# root and a ``.DirIcon`` file (or symlink) — appimagetool, file
# managers, and the appimaged daemon all look here for thumbnail
# extraction. Without ``.DirIcon`` the integration daemon can't
# bind the .desktop to an icon, so the AppImage shows a generic
# binary glyph in GNOME Files / KDE Dolphin.
cp "$SCRIPT_DIR/assets/polyglot-ai-256.png" "$APPDIR/polyglot-ai.png"
ln -sf polyglot-ai.png "$APPDIR/.DirIcon"

# Copy AppRun
cp "$SCRIPT_DIR/appimage/AppRun" "$APPDIR/"
chmod +x "$APPDIR/AppRun"

# Download appimagetool if not present
APPIMAGETOOL="$SCRIPT_DIR/appimagetool"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"

if [ ! -f "$APPIMAGETOOL" ]; then
    echo "Downloading appimagetool..."
    wget -q -O "$APPIMAGETOOL" "$APPIMAGETOOL_URL"
    chmod +x "$APPIMAGETOOL"
fi

# Build AppImage
ARCH=x86_64 "$APPIMAGETOOL" "$APPDIR" "$SCRIPT_DIR/Polyglot_AI-${VERSION}-x86_64.AppImage"

echo "Built: $SCRIPT_DIR/Polyglot_AI-${VERSION}-x86_64.AppImage"
