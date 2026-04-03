#!/bin/bash
# Generate PNG icons from SVG at standard sizes.
# Requires: librsvg2-bin (apt install librsvg2-bin)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SVG="$SCRIPT_DIR/assets/polyglot-ai.svg"
OUT_DIR="$SCRIPT_DIR/assets"

if ! command -v rsvg-convert &>/dev/null; then
    echo "Error: rsvg-convert not found. Install with: sudo apt install librsvg2-bin"
    exit 1
fi

for size in 16 32 48 128 256 512; do
    rsvg-convert -w "$size" -h "$size" "$SVG" -o "$OUT_DIR/polyglot-ai-${size}.png"
    echo "Generated: polyglot-ai-${size}.png"
done

# Create a copy of 256px as the default icon
cp "$OUT_DIR/polyglot-ai-256.png" "$OUT_DIR/polyglot-ai.png"
echo "Done! Icons generated in $OUT_DIR"
