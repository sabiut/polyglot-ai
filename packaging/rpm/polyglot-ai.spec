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
# Video editor: ffmpeg drives the actual edits, gstreamer plugins
# back the inline preview's QtMultimedia pipeline. ``Recommends``
# rather than ``Requires`` so users who never touch the video
# panel don't drag in the codecs.
Recommends:     ffmpeg
Recommends:     gstreamer1-plugins-good
Recommends:     gstreamer1-plugins-bad-free
# libav plugins live in RPM Fusion (not the default Fedora repos)
# and ship some patent-encumbered codecs. Marking as Recommends
# means dnf will pull them in if RPM Fusion is enabled and ignore
# them otherwise — safe default behaviour.
Recommends:     gstreamer1-libav
BuildArch:      x86_64

%description
Polyglot AI is a desktop coding assistant for Linux that supports
multiple AI providers including OpenAI, Anthropic Claude, Google Gemini,
and xAI Grok, with a full IDE-like interface.

%install
mkdir -p %{buildroot}/opt/polyglot-ai
mkdir -p %{buildroot}/opt/polyglot-ai/wheels
mkdir -p %{buildroot}/usr/share/applications
# Full hicolor size set so menus / pickers / tray pick a sharp
# render at any size instead of downscaling 256 → 24 (blurry).
for sz in 16 32 48 128 256 512; do
    mkdir -p %{buildroot}/usr/share/icons/hicolor/${sz}x${sz}/apps
done
mkdir -p %{buildroot}/usr/share/icons/hicolor/scalable/apps
cp %{_sourcedir}/*.whl %{buildroot}/opt/polyglot-ai/
# Bundled dependency wheels — see build_rpm.sh.
if [ -d %{_sourcedir}/wheels ] && [ -n "$(ls -A %{_sourcedir}/wheels 2>/dev/null)" ]; then
    cp %{_sourcedir}/wheels/* %{buildroot}/opt/polyglot-ai/wheels/
fi
cp %{_sourcedir}/polyglot-ai.desktop %{buildroot}/usr/share/applications/
# Every PNG size build_rpm.sh staged.
for sz in 16 32 48 128 256 512; do
    if [ -f %{_sourcedir}/polyglot-ai-${sz}.png ]; then
        cp %{_sourcedir}/polyglot-ai-${sz}.png \
           %{buildroot}/usr/share/icons/hicolor/${sz}x${sz}/apps/polyglot-ai.png
    fi
done
if [ -f %{_sourcedir}/polyglot-ai.svg ]; then
    cp %{_sourcedir}/polyglot-ai.svg \
       %{buildroot}/usr/share/icons/hicolor/scalable/apps/polyglot-ai.svg
fi

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
# Refresh desktop / icon caches so the launcher entry shows up
# in the user's menu *now*, not after the next reboot. Both tools
# are commonly present on Fedora/RHEL but not guaranteed in
# minimal images, so we tolerate either being absent.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || :
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor || :
fi
echo "Polyglot AI installed successfully."
echo "Run 'polyglot-ai' to start, or find it in your application menu."

%preun
rm -f /usr/local/bin/polyglot-ai
rm -rf /opt/polyglot-ai/venv

%postun
# After uninstall, refresh caches so the leftover icon doesn't
# linger in the user's launcher. ``: `` (the rpm idiom for "this
# is fine") because rpm scriptlet failures abort %postun.
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || :
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor || :
fi

%files
/opt/polyglot-ai/*.whl
/opt/polyglot-ai/wheels
/usr/share/applications/polyglot-ai.desktop
/usr/share/icons/hicolor/16x16/apps/polyglot-ai.png
/usr/share/icons/hicolor/32x32/apps/polyglot-ai.png
/usr/share/icons/hicolor/48x48/apps/polyglot-ai.png
/usr/share/icons/hicolor/128x128/apps/polyglot-ai.png
/usr/share/icons/hicolor/256x256/apps/polyglot-ai.png
/usr/share/icons/hicolor/512x512/apps/polyglot-ai.png
%if 0
# SVG only listed when present — wrapped under %if 0 in case some
# CI doesn't generate it. (Listed manually below to be safe.)
%endif
/usr/share/icons/hicolor/scalable/apps/polyglot-ai.svg
