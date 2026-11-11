#!/usr/bin/env bash
# Install apt packages on a CI runner, using the network only if needed.
#
# The runner images already ship most of what the workflows ask for.
# Asking apt anyway made every job depend on the Ubuntu mirrors being
# reachable: on 2026-11-11 the hosted runners could not resolve them
# ("Temporary failure resolving 'azure.archive.ubuntu.com'"), and CI sat
# in this step for ten minutes and failed before running a single test.
#
# So: install only what is missing, and if the runner can't resolve its
# configured mirror, say what it can resolve and go through a fallback
# mirror instead. Packages are verified against Ubuntu's archive key
# whichever mirror serves them, which is also why plain http is fine
# (the image's own primary mirror is http too) and avoids needing CA
# certificates to be in working order on a runner that is misbehaving.
#
# Usage: apt-install.sh <package>...
set -euo pipefail

FALLBACK_MIRROR="http://mirrors.edge.kernel.org/ubuntu/"

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

resolves() { timeout 8 getent hosts "$1" >/dev/null 2>&1; }

mirrors=/etc/apt/apt-mirrors.txt
primary=$(awk 'NF {print $1; exit}' "$mirrors" 2>/dev/null | sed -E 's#^[a-z]+://([^/]+)/.*#\1#')
primary=${primary:-archive.ubuntu.com}

if ! resolves "$primary"; then
  echo "::warning::This runner cannot resolve its apt mirror ($primary)."
  echo "Name resolution from this runner:"
  for host in "$primary" archive.ubuntu.com security.ubuntu.com \
              mirrors.edge.kernel.org pypi.org github.com; do
    if resolves "$host"; then echo "  ok      $host"; else echo "  FAILED  $host"; fi
  done
  echo "Resolver configuration:"
  grep -v '^#' /etc/resolv.conf | sed 's/^/  /' || true
  if [ -f "$mirrors" ] && resolves mirrors.edge.kernel.org; then
    echo "Using the fallback mirror $FALLBACK_MIRROR for this job."
    printf '%s\n' "$FALLBACK_MIRROR" | sudo tee "$mirrors" >/dev/null
  else
    echo "::error::No apt mirror is reachable from this runner; cannot install ${missing[*]}."
    exit 1
  fi
fi

opts=(-o Acquire::Retries=2 -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20)
sudo apt-get "${opts[@]}" update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get "${opts[@]}" install -y -qq "${missing[@]}"
