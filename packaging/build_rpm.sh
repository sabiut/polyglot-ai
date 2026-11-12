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
python3 -m pip install build uv
python3 -m build --wheel

# Set up rpmbuild structure
RPMBUILD="$SCRIPT_DIR/rpmbuild"
rm -rf "$RPMBUILD"
mkdir -p "$RPMBUILD"/{SOURCES,SPECS,BUILD,RPMS,SRPMS}

# Copy sources
cp "$PROJECT_DIR/dist/"*.whl "$RPMBUILD/SOURCES/"
cp "$SCRIPT_DIR/debian/polyglot-ai.desktop" "$RPMBUILD/SOURCES/"
# Ship every available hicolor size — anything missing here is
# downscaled by the launcher and looks blurry on HiDPI menus.
# The default 256 keeps the historic ``polyglot-ai.png`` name for
# back-compat with anything that read it directly.
cp "$SCRIPT_DIR/assets/polyglot-ai-256.png" "$RPMBUILD/SOURCES/polyglot-ai.png"
for sz in 16 32 48 128 256 512; do
    if [ -f "$SCRIPT_DIR/assets/polyglot-ai-${sz}.png" ]; then
        cp "$SCRIPT_DIR/assets/polyglot-ai-${sz}.png" \
           "$RPMBUILD/SOURCES/polyglot-ai-${sz}.png"
    else
        echo "warn: missing assets/polyglot-ai-${sz}.png — run packaging/generate_icons.sh first"
    fi
done
if [ -f "$SCRIPT_DIR/assets/polyglot-ai.svg" ]; then
    cp "$SCRIPT_DIR/assets/polyglot-ai.svg" "$RPMBUILD/SOURCES/polyglot-ai.svg"
fi
cp "$SCRIPT_DIR/rpm/polyglot-ai.spec" "$RPMBUILD/SPECS/"

# Pre-download dependency wheels so %posttrans can install offline (no
# PyPI round-trip needed at install time). See build_deb.sh for the
# rationale; same trade-off applies here.
#
# 3.11–3.14 are required (RHEL 10 ships 3.12, openSUSE 3.13, Fedora
# 43/44 3.14). The build container's own Python comes after ``--try``:
# the newest Fedora's Python is usually ahead of the wheels on PyPI, so
# bundle_wheels.sh builds what is missing right here and only warns if
# it can't. That keeps the newest Fedora covered without failing the
# release when a new Python lands in ``fedora:latest``.
echo "Pre-downloading dependency wheels for offline install..."
HOST_PY=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
"$SCRIPT_DIR/bundle_wheels.sh" "$RPMBUILD/SOURCES/wheels" 3.11 3.12 3.13 3.14 --try "$HOST_PY"
# The tested versions, for %posttrans: it installs with these pins even
# when it has to reach PyPI.
cp "$PROJECT_DIR/constraints.txt" "$RPMBUILD/SOURCES/constraints.txt"

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
