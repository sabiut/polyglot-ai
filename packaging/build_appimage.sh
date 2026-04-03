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
mkdir -p "$APPDIR/usr/share/applications"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# Create a venv inside AppDir using the system Python
python3 -m venv "$APPDIR/usr" --copies --clear

# Install the wheel
"$APPDIR/usr/bin/pip" install --upgrade pip
"$APPDIR/usr/bin/pip" install "$PROJECT_DIR/dist/"*.whl

# Copy desktop file and icon
cp "$SCRIPT_DIR/appimage/polyglot-ai.desktop" "$APPDIR/"
cp "$SCRIPT_DIR/appimage/polyglot-ai.desktop" "$APPDIR/usr/share/applications/"
cp "$SCRIPT_DIR/assets/polyglot-ai-256.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/polyglot-ai.png"
cp "$SCRIPT_DIR/assets/polyglot-ai-256.png" "$APPDIR/polyglot-ai.png"

# Copy AppRun
cp "$SCRIPT_DIR/appimage/AppRun" "$APPDIR/"
chmod +x "$APPDIR/AppRun"

# Download appimagetool if not present — pinned version with integrity check
APPIMAGETOOL="$SCRIPT_DIR/appimagetool"
APPIMAGETOOL_VERSION="continuous"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/${APPIMAGETOOL_VERSION}/appimagetool-x86_64.AppImage"
# SHA-256 of the pinned appimagetool binary — update when upgrading version
APPIMAGETOOL_SHA256="da76b0e446dd48ef48ef8bae7e0e0cfa32bcdb49414c2fe80a3bf48b1208fde2"

if [ ! -f "$APPIMAGETOOL" ]; then
    echo "Downloading appimagetool..."
    wget -q -O "$APPIMAGETOOL" "$APPIMAGETOOL_URL"

    echo "Verifying checksum..."
    ACTUAL_SHA256=$(sha256sum "$APPIMAGETOOL" | cut -d' ' -f1)
    if [ "$ACTUAL_SHA256" != "$APPIMAGETOOL_SHA256" ]; then
        echo "ERROR: appimagetool checksum mismatch!"
        echo "  Expected: $APPIMAGETOOL_SHA256"
        echo "  Got:      $ACTUAL_SHA256"
        echo "This may indicate a compromised download. Aborting."
        rm -f "$APPIMAGETOOL"
        exit 1
    fi
    echo "Checksum verified."

    chmod +x "$APPIMAGETOOL"
fi

# Build AppImage
ARCH=x86_64 "$APPIMAGETOOL" "$APPDIR" "$SCRIPT_DIR/Polyglot_AI-${VERSION}-x86_64.AppImage"

echo "Built: $SCRIPT_DIR/Polyglot_AI-${VERSION}-x86_64.AppImage"
