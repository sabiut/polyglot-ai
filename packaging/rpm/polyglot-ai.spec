Name:           polyglot-ai
Version:        %{rpm_version}
Release:        1%{?dist}
Summary:        AI-powered multi-provider coding assistant
License:        LGPL-3.0-or-later
URL:            https://github.com/sabiut/polyglot-ai

Requires:       python3 >= 3.11
Requires:       python3-pip
# Qt6 / X11 runtime libraries — without these, PyQt6 fails to load
# its platform plugin and the app silently exits to a terminal
# error like ``Could not load the Qt platform plugin "xcb"``.
Requires:       mesa-libGL
Requires:       mesa-libEGL
Requires:       libxkbcommon
Requires:       libxkbcommon-x11
Requires:       xcb-util-cursor
Requires:       fontconfig
Requires:       dbus-libs
Requires:       nss
Requires:       libXcomposite
Requires:       libXdamage
Requires:       libXrandr
Requires:       libXi
Requires:       libXtst
# Optional but very nice to have. ``Recommends`` is honoured by
# dnf5+; older yum will silently ignore it.
Recommends:     gnome-keyring
Recommends:     git
Recommends:     arduino-cli
BuildArch:      x86_64

%description
Polyglot AI is a desktop coding assistant for Linux that supports
multiple AI providers including OpenAI, Anthropic Claude, Google Gemini,
and xAI Grok, with a full IDE-like interface.

%install
mkdir -p %{buildroot}/opt/polyglot-ai
mkdir -p %{buildroot}/opt/polyglot-ai/wheels
mkdir -p %{buildroot}/usr/share/applications
mkdir -p %{buildroot}/usr/share/icons/hicolor/256x256/apps
cp %{_sourcedir}/*.whl %{buildroot}/opt/polyglot-ai/
# Bundled dependency wheels — see build_rpm.sh.
if [ -d %{_sourcedir}/wheels ] && [ -n "$(ls -A %{_sourcedir}/wheels 2>/dev/null)" ]; then
    cp %{_sourcedir}/wheels/* %{buildroot}/opt/polyglot-ai/wheels/
fi
cp %{_sourcedir}/polyglot-ai.desktop %{buildroot}/usr/share/applications/
cp %{_sourcedir}/polyglot-ai.png %{buildroot}/usr/share/icons/hicolor/256x256/apps/

%post
INSTALL_DIR="/opt/polyglot-ai"
VENV_DIR="$INSTALL_DIR/venv"
WHEELS_DIR="$INSTALL_DIR/wheels"
python3 -m venv "$VENV_DIR"
# Install offline from bundled wheels first; fall back to PyPI if
# the bundle is empty or pip refuses (incompatible platform tags
# on a non-x86_64 box, etc.).
if [ -d "$WHEELS_DIR" ] && [ -n "$(ls -A "$WHEELS_DIR" 2>/dev/null)" ]; then
    "$VENV_DIR/bin/pip" install --no-index --find-links "$WHEELS_DIR" \
        "$INSTALL_DIR"/*.whl || {
        "$VENV_DIR/bin/pip" install --upgrade pip
        "$VENV_DIR/bin/pip" install "$INSTALL_DIR"/*.whl
    }
else
    "$VENV_DIR/bin/pip" install --upgrade pip
    "$VENV_DIR/bin/pip" install "$INSTALL_DIR"/*.whl
fi
ln -sf "$VENV_DIR/bin/polyglot-ai" /usr/local/bin/polyglot-ai
echo "Polyglot AI installed successfully."
echo "Run 'polyglot-ai' to start, or find it in your application menu."

%preun
rm -f /usr/local/bin/polyglot-ai
rm -rf /opt/polyglot-ai/venv

%files
/opt/polyglot-ai/*.whl
/opt/polyglot-ai/wheels
/usr/share/applications/polyglot-ai.desktop
/usr/share/icons/hicolor/256x256/apps/polyglot-ai.png
