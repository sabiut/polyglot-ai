#!/bin/bash
# Build a .deb package for Polyglot AI
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PACKAGE="polyglot-ai"
ARCH="amd64"

# Read version from pyproject.toml
VERSION=$(python3 -c "
import tomllib
with open('$PROJECT_DIR/pyproject.toml', 'rb') as f:
    print(tomllib.load(f)['project']['version'])
")

DEB_NAME="${PACKAGE}_${VERSION}_${ARCH}"

echo "Building $DEB_NAME..."

# Build the wheel
cd "$PROJECT_DIR"
python3 -m pip install build uv
python3 -m build --wheel

# Create staging directory
STAGING="$SCRIPT_DIR/staging/$DEB_NAME"
rm -rf "$STAGING"
mkdir -p "$STAGING/DEBIAN"
mkdir -p "$STAGING/opt/polyglot-ai"
mkdir -p "$STAGING/usr/share/applications"
# Full hicolor size set — anything missing here gets downscaled by
# the launcher at display time, which looks blurry on HiDPI menus.
for sz in 16 32 48 128 256 512; do
    mkdir -p "$STAGING/usr/share/icons/hicolor/${sz}x${sz}/apps"
done
mkdir -p "$STAGING/usr/share/icons/hicolor/scalable/apps"

# Copy debian control files
cp "$SCRIPT_DIR/debian/control" "$STAGING/DEBIAN/"
# Update version in control file
sed -i "s/^Version:.*/Version: $VERSION/" "$STAGING/DEBIAN/control"
cp "$SCRIPT_DIR/debian/postinst" "$STAGING/DEBIAN/"
cp "$SCRIPT_DIR/debian/prerm" "$STAGING/DEBIAN/"
cp "$SCRIPT_DIR/debian/copyright" "$STAGING/DEBIAN/"
chmod 755 "$STAGING/DEBIAN/postinst"
chmod 755 "$STAGING/DEBIAN/prerm"

# Copy wheel
cp "$PROJECT_DIR/dist/"*.whl "$STAGING/opt/polyglot-ai/"

# Bundle dependency wheels so postinst can install offline.
# Without this, ``apt install polyglot-ai_*.deb`` requires internet
# at install time to fetch PyQt6 + provider SDKs from PyPI — fails
# silently for users behind a corporate proxy, on a flight, or on
# a fresh distro before networking comes up.
echo "Pre-downloading dependency wheels for offline install..."
# One set per Python minor version the target distros ship: the
# postinst venv uses the SYSTEM python3 (Debian 12 → 3.11, Ubuntu
# 24.04 → 3.12, Debian 13 → 3.13, Ubuntu 26.04 → 3.14), and compiled
# cpXY wheels for any other version are useless to it. Pure-Python
# wheels are shared between versions; only compiled ones are stored
# per version. All four are required: bundle_wheels.sh fails the
# build if one is incomplete (see its header for why nothing falls
# back any more).
"$SCRIPT_DIR/bundle_wheels.sh" "$STAGING/opt/polyglot-ai/wheels" 3.11 3.12 3.13 3.14
# The tested versions, for the postinst: it installs with these pins
# even when it has to reach PyPI.
cp "$PROJECT_DIR/constraints.txt" "$STAGING/opt/polyglot-ai/constraints.txt"

# Copy desktop file
cp "$SCRIPT_DIR/debian/polyglot-ai.desktop" "$STAGING/usr/share/applications/"

# Copy icons — every available hicolor size, plus the SVG for
# vector-aware themes (KDE, modern GNOME).
for sz in 16 32 48 128 256 512; do
    if [ -f "$SCRIPT_DIR/assets/polyglot-ai-${sz}.png" ]; then
        cp "$SCRIPT_DIR/assets/polyglot-ai-${sz}.png" \
           "$STAGING/usr/share/icons/hicolor/${sz}x${sz}/apps/polyglot-ai.png"
    else
        echo "warn: missing assets/polyglot-ai-${sz}.png — run packaging/generate_icons.sh first"
    fi
done
if [ -f "$SCRIPT_DIR/assets/polyglot-ai.svg" ]; then
    cp "$SCRIPT_DIR/assets/polyglot-ai.svg" \
       "$STAGING/usr/share/icons/hicolor/scalable/apps/polyglot-ai.svg"
fi

# Build .deb
dpkg-deb --build "$STAGING" "$SCRIPT_DIR/$DEB_NAME.deb"

echo "Built: $SCRIPT_DIR/$DEB_NAME.deb"
echo "Install with: sudo dpkg -i $DEB_NAME.deb"
