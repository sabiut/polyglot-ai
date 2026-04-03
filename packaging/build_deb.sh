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
python3 -m pip install build
python3 -m build --wheel

# Create staging directory
STAGING="$SCRIPT_DIR/staging/$DEB_NAME"
rm -rf "$STAGING"
mkdir -p "$STAGING/DEBIAN"
mkdir -p "$STAGING/opt/polyglot-ai"
mkdir -p "$STAGING/usr/share/applications"
mkdir -p "$STAGING/usr/share/icons/hicolor/256x256/apps"
mkdir -p "$STAGING/usr/share/icons/hicolor/128x128/apps"
mkdir -p "$STAGING/usr/share/icons/hicolor/48x48/apps"

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

# Copy desktop file
cp "$SCRIPT_DIR/debian/polyglot-ai.desktop" "$STAGING/usr/share/applications/"

# Copy icons
if [ -f "$SCRIPT_DIR/assets/polyglot-ai-256.png" ]; then
    cp "$SCRIPT_DIR/assets/polyglot-ai-256.png" \
       "$STAGING/usr/share/icons/hicolor/256x256/apps/polyglot-ai.png"
fi
if [ -f "$SCRIPT_DIR/assets/polyglot-ai-128.png" ]; then
    cp "$SCRIPT_DIR/assets/polyglot-ai-128.png" \
       "$STAGING/usr/share/icons/hicolor/128x128/apps/polyglot-ai.png"
fi
if [ -f "$SCRIPT_DIR/assets/polyglot-ai-48.png" ]; then
    cp "$SCRIPT_DIR/assets/polyglot-ai-48.png" \
       "$STAGING/usr/share/icons/hicolor/48x48/apps/polyglot-ai.png"
fi

# Build .deb
dpkg-deb --build "$STAGING" "$SCRIPT_DIR/$DEB_NAME.deb"

echo "Built: $SCRIPT_DIR/$DEB_NAME.deb"
echo "Install with: sudo dpkg -i $DEB_NAME.deb"
