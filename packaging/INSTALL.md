# Installing Polyglot AI

Pick the format that matches your system. Each one bundles all
Python dependencies (~150 MB of wheels for PyQt6, openai, anthropic,
google-genai, etc.) so a network connection is **not** required at
install time.

## Debian / Ubuntu / Mint / Pop!_OS / etc. (.deb)

```bash
sudo apt install ./polyglot-ai_<version>_amd64.deb
```

`apt` (or `dpkg -i` followed by `apt-get install -f`) pulls in the
required Qt and X11 libraries automatically. No PyPI access needed.

After install:

```bash
polyglot-ai            # from a terminal, or click the launcher
```

## Fedora / RHEL / Rocky / CentOS / openSUSE (.rpm)

```bash
sudo dnf install ./polyglot-ai-<version>-1.fc44.x86_64.rpm
# or
sudo zypper install ./polyglot-ai-<version>-1.fc44.x86_64.rpm
```

## Any Linux (.AppImage)

```bash
chmod +x Polyglot_AI-<version>-x86_64.AppImage
./Polyglot_AI-<version>-x86_64.AppImage
```

### AppImage troubleshooting

If a double-click does nothing or you see
`dlopen libfuse.so.2: cannot open shared object file`, your distro
needs the legacy FUSE library. Ubuntu 22.04+ no longer ships it
by default:

```bash
# Debian/Ubuntu
sudo apt install libfuse2

# Fedora 36+
sudo dnf install fuse-libs

# Arch
sudo pacman -S fuse2
```

Alternatively, run without FUSE:

```bash
./Polyglot_AI-<version>-x86_64.AppImage --appimage-extract-and-run
```

## Python wheel (advanced)

For containers or systems where you want to manage the venv
yourself:

```bash
python3 -m venv ~/.venvs/polyglot-ai
~/.venvs/polyglot-ai/bin/pip install ./polyglot_ai-<version>-py3-none-any.whl
~/.venvs/polyglot-ai/bin/polyglot-ai
```

You'll still need the system Qt libraries listed below.

---

## After installing — first-run setup

Polyglot AI runs out of the box, but a couple of optional steps
unlock the full experience.

### Add yourself to the `dialout` group (for Arduino uploads)

If you want to use the Arduino panel to flash boards, you need
serial-port permissions:

```bash
sudo usermod -aG dialout $USER
# then log out and back in
```

The .deb post-install hint catches this; on the .rpm and AppImage
paths you'll need to do it manually.

### Sign in to your AI provider

Open **Settings → AI Providers** and either paste an API key or use
the OAuth flow (OpenAI subscription, Claude subscription).

### Optional: install side tools

These unlock additional panels but the app works without them.
The first-run dialog flags any that are missing.

| Tool         | Unlocks                                  | Install                                                                |
|--------------|------------------------------------------|------------------------------------------------------------------------|
| `arduino-cli`| Arduino C++ panel build & upload         | `curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh \| sh` |
| `mpremote`   | MicroPython panel upload                 | `pip install --user mpremote`                                          |
| `gh`         | CI/CD pipeline inspector                 | `sudo apt install gh` / `sudo dnf install gh`                          |
| `docker`     | Docker panel                             | https://docs.docker.com/engine/install/                                |
| `kubectl`    | Kubernetes panel                         | https://kubernetes.io/docs/tasks/tools/                                |
| `npx` (Node) | MCP servers (sequential-thinking, etc.)  | `sudo apt install nodejs npm`                                          |
| `uvx` (uv)   | MCP servers (fetch, git)                 | `curl -LsSf https://astral.sh/uv/install.sh \| sh`                     |

---

## System libraries (already pulled in by .deb / .rpm)

If you're installing the wheel manually or running on a minimal
distro, these need to be present for Qt6 to load:

**Debian/Ubuntu**

```
libgl1 libegl1 libxcb-xinerama0 libxcb-cursor0 libxkbcommon0
libxkbcommon-x11-0 libfontconfig1 libdbus-1-3 libnss3 libxcomposite1
libxdamage1 libxrandr2 libxi6 libxtst6
```

**Fedora**

```
mesa-libGL mesa-libEGL libxkbcommon libxkbcommon-x11 xcb-util-cursor
fontconfig dbus-libs nss libXcomposite libXdamage libXrandr libXi libXtst
```

---

## Logs and config

| What        | Where                                              |
|-------------|----------------------------------------------------|
| Logs        | `~/.local/share/polyglot-ai/logs/polyglot-ai.log` |
| Config      | `~/.config/polyglot-ai/`                           |
| API keys    | OS keyring (Secret Service / KWallet / pass)       |

When reporting a bug, attach the log file — it's truncated to the
last 500 KB and contains no API keys.

---

## Uninstall

```bash
# .deb
sudo apt remove polyglot-ai

# .rpm
sudo dnf remove polyglot-ai

# AppImage
rm Polyglot_AI-*.AppImage   # there's nothing else installed

# Wheel
rm -rf ~/.venvs/polyglot-ai
```

User config in `~/.config/polyglot-ai/` is preserved across
uninstalls — delete it manually if you want a clean slate.
