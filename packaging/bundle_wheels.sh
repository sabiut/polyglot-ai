#!/bin/bash
# Fill a directory with the dependency wheels the .deb / .rpm need to
# install with no network, for each Python version their targets ship.
#
#   bundle_wheels.sh DEST PYVER... [--try PYVER...]
#
# Versions before ``--try`` are required: the script fails if the
# bundle for one of them is incomplete. Versions after it are best
# effort: a gap is reported as a warning and the build goes on.
#
# Why this is not one ``pip download`` per version any more:
#
# * It failed for every version for weeks and nobody saw. The pinned
#   PyQt6 wheels are tagged manylinux_2_34 and the old platform list
#   stopped at 2_28, so each download ended in ResolutionImpossible and
#   a fallback quietly bundled wheels for the build machine's Python
#   only. Every other distro then installed from PyPI, unpinned. Nothing
#   here falls back: a required version that can't be bundled fails
#   the build, and each bundle is checked by resolving from it alone.
#
# * ``pip download --python-version X`` still reads dependency markers
#   with the Python it runs on. Python 3.11 needs three packages
#   (importlib-metadata, zipp, backports-tarfile) that newer versions
#   don't; built from a newer Python the 3.11 bundle would have lacked
#   them. ``uv pip compile`` resolves for the target version properly,
#   so it produces the exact list and pip only fetches it (--no-deps).
#
# A version with no wheel on PyPI for some pin (a brand-new Python) can
# still be bundled when it is the build machine's own Python: the
# missing wheels are built here from the pinned sdists. That is how the
# .rpm covers the Python of the Fedora release it is built on.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONSTRAINTS="$PROJECT_DIR/constraints.txt"
PY="${PYTHON:-python3}"

# Newest glibc a bundled wheel may require. 2.34 is what the pinned
# PyQt6 needs; every target is newer (Debian 12 has 2.36). Raising it
# drops support for distros older than that glibc.
GLIBC_MINOR=34

if [ "$#" -lt 2 ]; then
    echo "usage: $0 DEST PYVER... [--try PYVER...]" >&2
    exit 2
fi
DEST="$1"
shift
required=()
optional=()
target=required
for arg in "$@"; do
    if [ "$arg" = "--try" ]; then
        target=optional
    elif [ "$target" = required ]; then
        required+=("$arg")
    else
        optional+=("$arg")
    fi
done

mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
HOST_PY="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

platforms=()
for ((minor = GLIBC_MINOR; minor >= 5; minor--)); do
    platforms+=(--platform "manylinux_2_${minor}_x86_64")
done
platforms+=(--platform manylinux2014_x86_64 --platform manylinux2010_x86_64
    --platform manylinux1_x86_64)

# resolve VERSION OUTFILE [uv options] — the pinned set for VERSION,
# binary wheels only, as VERSION itself would resolve it.
resolve() {
    local version="$1" out="$2"
    shift 2
    "$PY" -m uv pip compile "$PROJECT_DIR/pyproject.toml" \
        --constraint "$CONSTRAINTS" \
        --python-version "$version" \
        --python-platform "x86_64-manylinux_2_${GLIBC_MINOR}" \
        --only-binary :all: \
        --no-header --no-annotate --quiet \
        --output-file "$out" "$@"
}

# Build wheels for the pins PyPI has no wheel for, with this machine's
# own Python. pip's resolution report says which ones are sdists.
build_missing() {
    local report="$WORK/native.json" spec built=0
    "$PY" -m pip install --quiet --dry-run --ignore-installed \
        --constraint "$CONSTRAINTS" --report "$report" "$PROJECT_DIR" || return 1
    while read -r spec; do
        echo "    building $spec from source (no wheel on PyPI for Python $HOST_PY)"
        "$PY" -m pip wheel --quiet --no-deps --wheel-dir "$DEST" "$spec" || return 1
        built=1
    done < <("$PY" - "$report" <<'PYEOF'
import json, sys

for item in json.load(open(sys.argv[1]))["install"]:
    meta, url = item["metadata"], item["download_info"]["url"]
    if not url.endswith(".whl") and meta["name"] != "polyglot-ai":
        print(f"{meta['name']}=={meta['version']}")
PYEOF
    )
    [ "$built" -eq 1 ]
}

bundle_one() {
    local version="$1" req="$WORK/req-$1.txt" log="$WORK/resolve-$1.log"
    local native=()
    if ! resolve "$version" "$req" --find-links "$DEST" 2>"$log"; then
        if [ "$version" != "$HOST_PY" ] || ! build_missing; then
            sed 's/^/    /' "$log" >&2
            return 1
        fi
        # Wheels built here are tagged plain linux_x86_64.
        native=(--platform linux_x86_64)
        if ! resolve "$version" "$req" --find-links "$DEST" 2>"$log"; then
            sed 's/^/    /' "$log" >&2
            return 1
        fi
    fi
    "$PY" -m pip download --quiet --no-deps --only-binary=:all: \
        --python-version "$version" "${platforms[@]}" ${native[@]+"${native[@]}"} \
        --find-links "$DEST" --dest "$DEST" --requirement "$req" || return 1
    # The check that was missing: can this version install from the
    # directory alone?
    if ! resolve "$version" "$WORK/check-$version.txt" --no-index --find-links "$DEST" 2>"$log"; then
        sed 's/^/    /' "$log" >&2
        return 1
    fi
    echo "    $(grep -c '==' "$req") packages, verified offline"
}

failed=()
for version in "${required[@]}"; do
    echo "Bundling wheels for Python $version"
    bundle_one "$version" || failed+=("$version")
done
for version in ${optional[@]+"${optional[@]}"}; do
    case " ${required[*]} " in *" $version "*) continue ;; esac
    echo "Bundling wheels for Python $version (best effort)"
    if ! bundle_one "$version"; then
        msg="No complete wheel bundle for Python $version: installs on it will download from PyPI."
        echo "::warning::$msg"
        echo "WARNING: $msg" >&2
    fi
done

if [ "${#failed[@]}" -gt 0 ]; then
    echo "::error::Wheel bundle incomplete for Python ${failed[*]} — see the resolver output above."
    echo "ERROR: wheel bundle incomplete for Python ${failed[*]}." >&2
    echo "A pin in constraints.txt has no binary wheel for that version (or needs a newer" >&2
    echo "glibc than 2.$GLIBC_MINOR). Bump the pin, or drop the version from the build script." >&2
    exit 1
fi

echo "Wheel bundle: $(find "$DEST" -name '*.whl' | wc -l) wheels, $(du -sh "$DEST" | cut -f1)"
