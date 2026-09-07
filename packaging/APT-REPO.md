# Hosted APT/RPM repository (Cloudsmith) — maintainer setup

The release workflow's `publish-repos` job pushes every release's
`.deb` and `.rpm` into a hosted Cloudsmith repository, so installed
users get new versions through plain `apt update && apt upgrade`
(or `dnf upgrade`) instead of downloading from the releases page.

The job is **soft-gated**: until the steps below are done it logs a
notice and exits green. Nothing breaks by leaving this unconfigured.

## One-time setup (maintainer)

1. **Create a Cloudsmith account** at <https://cloudsmith.com> and
   apply for the free **open-source plan** (Polyglot AI qualifies:
   public repository, LGPL-3.0-or-later license). The OSS plan
   requires the repository to be public and to link back to
   Cloudsmith — see their current terms.

2. **Create a repository** named `polyglot-ai` under your account
   (public). One Cloudsmith repo serves both Debian and RPM
   packages. The workflow's default slug is `opensource-r6tx/polyglot`;
   if yours differs, set a GitHub Actions **repository variable**
   `CLOUDSMITH_REPO` (Settings → Secrets and variables → Actions →
   Variables) to `<account>/<repo>`.

3. **Create an API key** in Cloudsmith (Account → API Settings) and
   add it as a GitHub Actions **repository secret** named
   `CLOUDSMITH_API_KEY` (Settings → Secrets and variables → Actions
   → Secrets).

4. **Cut the next release.** The `publish-repos` job will push the
   `.deb` to `debian/trixie`, `debian/bookworm`, `ubuntu/noble`, and
   `ubuntu/jammy`, and the `.rpm` to `fedora/44` and `fedora/43`.
   Adjust the target lists in `.github/workflows/release.yml` as
   distro releases move on.

## What users do (once the repo is live)

Cloudsmith generates a per-repo setup script that registers the
sources entry and the repo's signing key:

```bash
curl -1sLf 'https://dl.cloudsmith.io/public/opensource-r6tx/polyglot/setup.deb.sh' | sudo -E bash
sudo apt install polyglot-ai
```

RPM systems:

```bash
curl -1sLf 'https://dl.cloudsmith.io/public/opensource-r6tx/polyglot/setup.rpm.sh' | sudo -E bash
sudo dnf install polyglot-ai
```

From then on, `apt upgrade` / `dnf upgrade` picks up every new
release automatically. The in-app update notifier keeps working
regardless of install method.

## Why Cloudsmith and not GitHub Pages?

A static repo on GitHub Pages is the usual zero-cost approach, but
Pages enforces a 100 MB per-file limit and our `.deb` is ~225 MB
(bundled offline wheels for three Python versions). Cloudsmith's
OSS tier hosts and GPG-signs the repo for free and keeps the signing
key out of our CI entirely.
