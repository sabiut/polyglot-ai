Name:           polyglot-ai
Version:        %{rpm_version}
Release:        1%{?dist}
Summary:        AI-powered multi-provider coding assistant
License:        LGPL-3.0-or-later
URL:            https://github.com/sabiut/polyglot-ai

Requires:       python3 >= 3.11
Requires:       python3-pip
BuildArch:      x86_64

%description
Polyglot AI is a desktop coding assistant for Linux that supports
multiple AI providers including OpenAI, Anthropic Claude, Google Gemini,
and xAI Grok, with a full IDE-like interface.

%install
mkdir -p %{buildroot}/opt/polyglot-ai
mkdir -p %{buildroot}/usr/share/applications
mkdir -p %{buildroot}/usr/share/icons/hicolor/256x256/apps
cp %{_sourcedir}/*.whl %{buildroot}/opt/polyglot-ai/
cp %{_sourcedir}/polyglot-ai.desktop %{buildroot}/usr/share/applications/
cp %{_sourcedir}/polyglot-ai.png %{buildroot}/usr/share/icons/hicolor/256x256/apps/

%post
INSTALL_DIR="/opt/polyglot-ai"
VENV_DIR="$INSTALL_DIR/venv"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install "$INSTALL_DIR"/*.whl
ln -sf "$VENV_DIR/bin/polyglot-ai" /usr/local/bin/polyglot-ai
echo "Polyglot AI installed successfully."

%preun
rm -f /usr/local/bin/polyglot-ai
rm -rf /opt/polyglot-ai/venv

%files
/opt/polyglot-ai/*.whl
/usr/share/applications/polyglot-ai.desktop
/usr/share/icons/hicolor/256x256/apps/polyglot-ai.png
