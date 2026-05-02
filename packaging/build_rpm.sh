#!/bin/bash
# Build an RPM package for Polyglot AI
# Requires: rpm-build, python3
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

VERSION=$(python3 -c "
import tomllib
with open('$PROJECT_DIR/pyproject.toml', 'rb') as f:
    print(tomllib.load(f)['project']['version'])
")

echo "Building RPM for polyglot-ai v${VERSION}..."

# Build wheel
cd "$PROJECT_DIR"
python3 -m pip install build
python3 -m build --wheel

# Set up rpmbuild structure
RPMBUILD="$SCRIPT_DIR/rpmbuild"
rm -rf "$RPMBUILD"
mkdir -p "$RPMBUILD"/{SOURCES,SPECS,BUILD,RPMS,SRPMS}

# Copy sources
cp "$PROJECT_DIR/dist/"*.whl "$RPMBUILD/SOURCES/"
cp "$SCRIPT_DIR/debian/polyglot-ai.desktop" "$RPMBUILD/SOURCES/"
cp "$SCRIPT_DIR/assets/polyglot-ai-256.png" "$RPMBUILD/SOURCES/polyglot-ai.png"
cp "$SCRIPT_DIR/rpm/polyglot-ai.spec" "$RPMBUILD/SPECS/"

# Pre-download dependency wheels so %post can install offline (no
# PyPI round-trip needed at install time). See build_deb.sh for the
# rationale; same trade-off applies here.
mkdir -p "$RPMBUILD/SOURCES/wheels"
echo "Pre-downloading dependency wheels for offline install..."
python3 -m pip download \
    --dest "$RPMBUILD/SOURCES/wheels" \
    --only-binary=:all: \
    --python-version 3.11 \
    --platform manylinux2014_x86_64 \
    --platform manylinux_2_17_x86_64 \
    --platform manylinux_2_28_x86_64 \
    --platform any \
    "$PROJECT_DIR/dist/"*.whl \
    || python3 -m pip download \
        --dest "$RPMBUILD/SOURCES/wheels" \
        "$PROJECT_DIR/dist/"*.whl

# Build RPM
rpmbuild --define "_topdir $RPMBUILD" \
         --define "rpm_version $VERSION" \
         -bb "$RPMBUILD/SPECS/polyglot-ai.spec"

RPM_FILE=$(find "$RPMBUILD/RPMS" -name "*.rpm" | head -1)
if [ -n "$RPM_FILE" ]; then
    cp "$RPM_FILE" "$SCRIPT_DIR/"
    echo "Built: $SCRIPT_DIR/$(basename "$RPM_FILE")"
else
    echo "Error: RPM not found"
    exit 1
fi
