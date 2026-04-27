from pathlib import Path

from polyglot_ai import __version__

APP_NAME = "Polyglot AI"
APP_ID = "io.github.sabiut.polyglotai"
APP_VERSION = __version__

# Directories
DATA_DIR = Path.home() / ".local" / "share" / "polyglot-ai"
LOG_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "polyglot.db"

# Legacy directories (for migration from Codex Desktop)
LEGACY_DATA_DIR = Path.home() / ".local" / "share" / "codex-desktop"
LEGACY_CONFIG_DIR = Path.home() / ".config" / "codex-desktop"

# Limits
MAX_AGENT_ITERATIONS = 20
COMMAND_TIMEOUT = 30  # seconds

# Event names — AI
EVT_AI_SEND_MESSAGE = "ai:send_message"
EVT_AI_STREAM_CHUNK = "ai:stream_chunk"
EVT_AI_STREAM_DONE = "ai:stream_done"
EVT_AI_TOOL_CALL_REQUEST = "ai:tool_call_request"
EVT_AI_ERROR = "ai:error"

# Event names — Files
EVT_FILE_OPENED = "file:opened"
EVT_FILE_SAVED = "file:saved"
EVT_FILE_CHANGED = "file:changed"
EVT_FILE_CREATED = "file:created"
EVT_FILE_DELETED = "file:deleted"

# Event names — Project
EVT_PROJECT_OPENED = "project:opened"
EVT_PROJECT_CLOSED = "project:closed"

# Event names — Terminal
EVT_TERMINAL_OUTPUT = "terminal:output"
EVT_TERMINAL_EXITED = "terminal:exited"

# Event names — Approval
EVT_APPROVAL_REQUESTED = "approval:requested"
EVT_APPROVAL_RESPONSE = "approval:response"

# Event names — Git
EVT_GIT_REFRESH = "git:refresh"
EVT_GIT_COMMITTED = "git:committed"

# Event names — Conversation
EVT_CONVERSATION_FORKED = "conversation:forked"

# Event names — Indexing
EVT_INDEX_READY = "index:ready"
EVT_INDEX_UPDATED = "index:updated"

# Model cost estimates (per 1K tokens). Kept conservative — actual
# pricing is set per-provider on their dashboard and may change; the
# values here drive the in-app usage estimate, not billing truth.
MODEL_COSTS = {
    "gpt-5.5": {"input": 0.012, "output": 0.04},
    "gpt-5.4": {"input": 0.01, "output": 0.03},
    "o4-mini": {"input": 0.001, "output": 0.004},
    "claude-opus-4-7": {"input": 0.018, "output": 0.09},
    "claude-opus-4-6": {"input": 0.015, "output": 0.075},
    "claude-sonnet-4-6": {"input": 0.003, "output": 0.015},
    "gemini-3.1-pro-preview": {"input": 0.00125, "output": 0.005},
    "gemini-3-flash-preview": {"input": 0.0001, "output": 0.0004},
}

# Keyring
KEYRING_SERVICE = "polyglot-ai"
LEGACY_KEYRING_SERVICE = "codex-desktop"

# Sandbox — allowed shell commands (union of both tiers)
# Read-only commands can run without approval.
# Dangerous commands ALWAYS require explicit user approval.
SAFE_COMMANDS = frozenset(
    {
        "ls",
        "cat",
        "grep",
        "find",
        "head",
        "tail",
        "wc",
        "sort",
        "uniq",
        "diff",
        "tree",
        "echo",
        "printf",
        "git",  # read-only git subcommands; commits need approval via tool
    }
)

DANGEROUS_COMMANDS = frozenset(
    {
        "python",
        "python3",
        "pip",
        "pip3",
        "node",
        "npm",
        "npx",
        "cargo",
        "rustc",
        "make",
        "cmake",
        "mkdir",
        "touch",
        "cp",
        "mv",
        "rm",
        "sed",
        "awk",
        "tee",
    }
)

ALLOWED_COMMANDS = SAFE_COMMANDS | DANGEROUS_COMMANDS

# File extensions recognized as source code
CODE_EXTENSIONS = frozenset(
    {
        ".py",
        ".pyw",
        ".js",
        ".mjs",
        ".ts",
        ".tsx",
        ".jsx",
        ".java",
        ".go",
        ".rs",
        ".c",
        ".cpp",
        ".cxx",
        ".cc",
        ".h",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
        ".swift",
        ".kt",
        ".scala",
        ".dart",
        ".lua",
        ".r",
        ".m",
        ".sh",
        ".bash",
        ".zsh",
        ".sql",
        ".html",
        ".htm",
        ".css",
        ".scss",
        ".xml",
        ".svg",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".cfg",
        ".ini",
        ".md",
        ".markdown",
        ".txt",
        ".env.example",
        ".gitignore",
        ".dockerignore",
        "Dockerfile",
        "Makefile",
        "CMakeLists.txt",
        # DevOps / IaC
        ".tf",
        ".tfvars",
        ".hcl",
        ".j2",
        ".jinja2",
        "Chart.yaml",
        "values.yaml",
        "dbt_project.yml",
    }
)

# Directories to skip when walking project trees
SKIP_DIRS = frozenset(
    {
        ".git",
        ".svn",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".eggs",
        ".tox",
        ".nox",
        "target",
        "bin",
        "obj",
        ".next",
        ".nuxt",
        ".idea",
        ".vscode",
        ".polyglot-backups",  # code_applier backup files — may contain old secrets
    }
)

# Max file size for indexing / context inclusion (bytes)
MAX_FILE_SIZE = 50_000

BLOCKED_PATTERNS = frozenset(
    {
        "sudo",
        "rm -rf /",
        "rm -rf /*",
        "curl|bash",
        "curl|sh",
        "wget|bash",
        "wget|sh",
        "chmod 777",
        "dd if=",
        "mkfs",
        ":(){",  # fork bomb
        "shutdown",
        "reboot",
        "poweroff",
        "init 0",
        "init 6",
    }
)
