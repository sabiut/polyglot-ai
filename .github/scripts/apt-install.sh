#!/usr/bin/env bash
# Install apt packages on a CI runner, using the network only if needed.
#
# The runner images already ship most of what the workflows ask for.
# Asking apt anyway made every job depend on the Ubuntu mirrors being
# reachable: on 2026-11-11 the hosted runners could not resolve them
# ("Temporary failure resolving 'azure.archive.ubuntu.com'"), and CI sat
# in this step for ten minutes and failed before running a single test.
#
# Usage: apt-install.sh <package>...
set -euo pipefail

missing=()
for pkg in "$@"; do
  if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
    continue
  fi
  missing+=("$pkg")
done

if [ "${#missing[@]}" -eq 0 ]; then
  echo "Already on the runner image, nothing to install: $*"
  exit 0
fi

echo "Installing: ${missing[*]}"
opts=(-o Acquire::Retries=3 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20)
sudo apt-get "${opts[@]}" update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get "${opts[@]}" install -y -qq "${missing[@]}"
