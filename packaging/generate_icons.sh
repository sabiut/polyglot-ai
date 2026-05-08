#!/bin/bash
# Generate PNG icons from SVG at standard sizes, plus mirror the
# generated set into ``src/polyglot_ai/resources/icons/`` so the
# running app can install them at first launch.
# Requires: librsvg2-bin (apt install librsvg2-bin)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
SVG="$SCRIPT_DIR/assets/polyglot-ai.svg"
OUT_DIR="$SCRIPT_DIR/assets"
RESOURCES_DIR="$PROJECT_DIR/src/polyglot_ai/resources/icons"

if ! command -v rsvg-convert &>/dev/null; then
    echo "Error: rsvg-convert not found. Install with: sudo apt install librsvg2-bin"
    exit 1
fi

mkdir -p "$RESOURCES_DIR"

for size in 16 32 48 128 256 512; do
    rsvg-convert -w "$size" -h "$size" "$SVG" -o "$OUT_DIR/polyglot-ai-${size}.png"
    # Mirror into the runtime resources dir so ``setup_platform``
    # can install all sizes when the app launches from a wheel /
    # AppImage / source checkout. Without this the runtime side
    # has only the original 256.
    cp "$OUT_DIR/polyglot-ai-${size}.png" "$RESOURCES_DIR/polyglot-ai-${size}.png"
    echo "Generated: polyglot-ai-${size}.png"
done

# Create a copy of 256px as the default icon (used by parts of
# the codebase that reference the unversioned filename).
cp "$OUT_DIR/polyglot-ai-256.png" "$OUT_DIR/polyglot-ai.png"
cp "$OUT_DIR/polyglot-ai-256.png" "$RESOURCES_DIR/polyglot-ai.png"
# Mirror the SVG too so vector-aware themes get a sharp render.
cp "$SVG" "$RESOURCES_DIR/polyglot-ai.svg"
echo "Done! Icons generated in $OUT_DIR and mirrored to $RESOURCES_DIR"
