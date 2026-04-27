{
  description = "Polyglot AI — multi-provider AI coding assistant for Linux (development shell)";

  # Pin nixpkgs via flake.lock — `nix flake update` to bump.
  # We use unstable so PyQt6 / openai / anthropic stay near current.
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };

        # The Python interpreter + heavy native packages we want
        # provided by Nix (rather than pip-built from source). Pure-
        # Python deps come from the project's own `pyproject.toml`
        # via `pip install -e ".[dev]"` inside the shell — that way a
        # new dep added to pyproject.toml works without touching the
        # flake.
        pythonEnv = pkgs.python311.withPackages (
          ps: with ps; [
            pip
            virtualenv
            # PyQt stack — these are awful to build from PyPI inside
            # Nix without proper Qt wiring, so let nixpkgs handle them
            # and we'll add the activation paths to PYTHONPATH below.
            pyqt6
            pyqt6-charts
            qscintilla-qt6
            # Async DB drivers — native compile, far easier from
            # nixpkgs than from pip.
            aiosqlite
            asyncpg
            aiomysql
            # Terminal emulator
            pyte
            # Other heavy deps that benefit from nixpkgs caching
            keyring
            pyyaml
            qasync
          ]
        );

        # Runtime libraries the Qt platform plugins look for at load
        # time. PyQt6 bundles its own Qt, but for the nixpkgs version
        # we need the system Qt platform-plugin path.
        qtPlatformDeps = with pkgs; [
          qt6.qtbase
          qt6.qtwayland
          qt6.qtsvg
          # X11 / Wayland glue libs the Qt platform plugin dlopens.
          libxkbcommon
          libGL
          fontconfig
          freetype
          dbus
          # xcb stack — needed even on Wayland for compatibility.
          xorg.libxcb
          xorg.xcbutil
          xorg.xcbutilimage
          xorg.xcbutilkeysyms
          xorg.xcbutilrenderutil
          xorg.xcbutilwm
          xorg.xcbutilcursor
        ];
      in
      {
        devShells.default = pkgs.mkShell {
          name = "polyglot-ai-dev";

          # Build-time / development tools available on PATH inside
          # the shell. Mirrors what CONTRIBUTING.md tells contributors
          # to install.
          packages =
            [
              pythonEnv
              pkgs.ruff
              pkgs.pre-commit
              # Runtime tools used by MCP servers and the workflow
              # commands. These are optional at runtime, but contributors
              # will hit half the codebase if they're missing.
              pkgs.nodejs_20 # provides `npx` for filesystem / memory / playwright MCP servers
              pkgs.uv # provides `uvx` for the fetch / git MCP servers
              pkgs.git
              pkgs.gh # used by the CI/CD panel
            ]
            ++ qtPlatformDeps;

          # Shell environment.
          shellHook = ''
            # Make Qt's platform plugins discoverable. Without this the
            # PyQt6 we expose can fail to load with a confusing
            # "could not find or load the Qt platform plugin xcb"
            # message — the binary is fine, but plugin lookup goes
            # through QT_PLUGIN_PATH.
            export QT_PLUGIN_PATH="${pkgs.qt6.qtbase}/lib/qt-6/plugins''${QT_PLUGIN_PATH:+:$QT_PLUGIN_PATH}"
            export QT_QPA_PLATFORM_PLUGIN_PATH="${pkgs.qt6.qtbase}/lib/qt-6/plugins/platforms"

            # Nix isolates libraries; PyQt's bundled wheels would
            # fail at runtime without a path to the system OpenGL,
            # xcb, fontconfig stack.
            export LD_LIBRARY_PATH="${pkgs.lib.makeLibraryPath qtPlatformDeps}''${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

            # If the project's own venv exists (created the very first
            # time the shell is entered), use it so `polyglot-ai` from
            # the editable install resolves on PATH. The Nix-provided
            # PyQt6 still works because we expose its site-packages
            # via PYTHONPATH below.
            if [ ! -d .venv ]; then
              echo "[polyglot-ai] First-time setup: creating .venv and installing project (editable)"
              ${pythonEnv}/bin/python -m venv --system-site-packages .venv
              # --system-site-packages makes the Nix-provided PyQt6,
              # asyncpg, etc. visible to pip, so it doesn't try to
              # rebuild them from source.
              .venv/bin/pip install --upgrade pip
              .venv/bin/pip install -e ".[dev]"
            fi
            # shellcheck disable=SC1091
            source .venv/bin/activate

            echo ""
            echo "  polyglot-ai dev shell ready."
            echo "  Run:  polyglot-ai          (launch the app)"
            echo "        pytest tests/         (run the test suite)"
            echo "        ruff check src/ tests/   (lint)"
            echo "        ruff format src/ tests/  (format)"
            echo ""
          '';
        };

        # `nix flake check` runs this — keeps the flake from rotting.
        checks.devShellBuilds = self.devShells.${system}.default;
      }
    );
}
