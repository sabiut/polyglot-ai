# PyPI publishing — maintainer setup

The release workflow's `publish-pypi` job uploads the wheel to PyPI
on every tagged release, so users can install with:

```bash
pipx install polyglot-ai
```

It uses **trusted publishing** (OIDC): PyPI trusts this GitHub
repository's release workflow directly — no API token, no secret.

The job is **hard-gated** on the `PYPI_PUBLISH_ENABLED` repository
variable and is skipped until you finish the setup below.

## One-time setup (maintainer)

1. **Create a PyPI account** at <https://pypi.org> (enable 2FA —
   required for new publishers).

2. **Register a pending trusted publisher** (this reserves the name
   and authorizes CI in one step): go to
   <https://pypi.org/manage/account/publishing/> → "Add a new
   pending publisher" and fill in:
   - PyPI project name: `polyglot-ai`
   - Owner: `sabiut`
   - Repository name: `polyglot-ai`
   - Workflow name: `release.yml`
   - Environment name: *(leave blank)*

3. **Enable the job**: on GitHub → Settings → Secrets and variables
   → Actions → **Variables** → New repository variable:
   name `PYPI_PUBLISH_ENABLED`, value `true`.

4. **Cut the next release.** The first successful publish converts
   the pending publisher into the real project; subsequent releases
   just upload the new version.

## Notes

- The wheel depends on PyQt6/QScintilla wheels from PyPI, so a
  `pipx install` needs network — unlike the .deb/.rpm/AppImage,
  nothing is bundled. The system Qt libraries listed in INSTALL.md
  must be present.
- Yanking a bad release: <https://pypi.org/manage/project/polyglot-ai/releases/>.
