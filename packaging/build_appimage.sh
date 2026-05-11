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
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/lib"

# Create a venv inside AppDir using the system Python
# NOTE: --clear wipes $APPDIR/usr, so mkdir for share/ must come AFTER this
python3 -m venv "$APPDIR/usr" --copies --clear

# Create directories that venv --clear would have removed.
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
# the only step that needs network. The previous setup ran a bare
# ``pip install`` against PyPI at build time; if the CI runner
# lost network mid-build, the AppImage would silently embed a
# partially-installed venv and ship to users. Mirrors the
# offline-bundle pattern already used by .deb / .rpm.
mkdir -p "$SCRIPT_DIR/appimage/wheels"
echo "Pre-downloading wheels for offline AppImage install..."
"$APPDIR/usr/bin/pip" install --upgrade pip
"$APPDIR/usr/bin/pip" download \
    --dest "$SCRIPT_DIR/appimage/wheels" \
    --only-binary=:all: \
    --platform manylinux2014_x86_64 \
    --platform manylinux_2_17_x86_64 \
    --platform manylinux_2_28_x86_64 \
    --platform any \
    "$PROJECT_DIR/dist/"*.whl \
    || {
        echo "Strict platform download failed; retrying with host resolver…"
        "$APPDIR/usr/bin/pip" download \
            --dest "$SCRIPT_DIR/appimage/wheels" \
            "$PROJECT_DIR/dist/"*.whl
    }

# Install entirely from the bundled wheels — no live PyPI access.
# ``--no-index`` forbids contacting PyPI and ``--find-links`` makes
# the resolver use the bundled wheels exclusively.
"$APPDIR/usr/bin/pip" install --no-index \
    --find-links "$SCRIPT_DIR/appimage/wheels" \
    "$PROJECT_DIR/dist/"*.whl

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
