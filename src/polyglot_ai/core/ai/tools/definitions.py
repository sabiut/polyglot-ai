"""Tool definitions and approval policies for AI function calling."""

# Tool definitions in OpenAI function calling format
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "file_read",
            "description": "Read the contents of a file at the given path relative to project root.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path from project root",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_write",
            "description": "Write content to a file. Creates the file if it doesn't exist, overwrites if it does.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path from project root",
                    },
                    "content": {
                        "type": "string",
                        "description": "The full content to write to the file",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_search",
            "description": "Search for a text pattern in files under the project root (plain text, not regex).",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Plain text pattern to search for",
                    },
                    "path": {
                        "type": "string",
                        "description": "Subdirectory to search in (optional, defaults to project root)",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "shell_exec",
            "description": "Execute a single command in the project directory. Only allowlisted commands are permitted (e.g. git, python, npm, grep, find). Shell operators (&&, |, >, <) are not allowed — use separate calls instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "Working directory relative to project root (optional)",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List the contents of a directory as a tree structure.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative directory path (defaults to project root)",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Maximum depth of the tree (default 3)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for information. Use when you need current docs, error solutions, or API references.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_patch",
            "description": "Apply a targeted search-and-replace edit to a file. Use this for small edits instead of rewriting the entire file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative file path from project root",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "The exact text to find (must be unique in the file)",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "The replacement text",
                    },
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Run git status --porcelain to show working tree status: staged, unstaged, and untracked files.",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show git diff of changes. Use mode to choose what to diff.",
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["working", "staged", "branch"],
                        "description": "working=unstaged, staged=cached, branch=diff against main/master",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "Show recent git commit history (last 20 commits, one line each).",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of commits to show (default 20, max 50)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Stage all changes and commit with the given message. Runs git add -A followed by git commit -m 'message' as two separate commands.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "The commit message",
                    },
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_show_file",
            "description": "Show the contents of a file at a specific git ref (commit, branch, tag). Defaults to HEAD.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to repo root",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Git ref to show the file at (default HEAD)",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_plan",
            "description": (
                "Create a structured implementation plan. Call this tool in plan mode "
                "to propose a step-by-step plan before writing any code. Each step should "
                "describe one logical unit of work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Short title for the plan",
                    },
                    "summary": {
                        "type": "string",
                        "description": "Brief summary of the overall approach",
                    },
                    "steps": {
                        "type": "array",
                        "description": "Ordered list of implementation steps",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {
                                    "type": "string",
                                    "description": "Short title for this step",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "What this step does and how",
                                },
                                "files_affected": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "File paths this step will create or modify",
                                },
                            },
                            "required": ["title", "description"],
                        },
                    },
                },
                "required": ["title", "summary", "steps"],
            },
        },
    },
]

# Tool approval policy — explicit categorization.
#
# AUTO_APPROVE: read-only, local-only tools that cannot modify state or
#               send data externally. Safe to run without user confirmation.
#
# REQUIRES_APPROVAL: tools that write files, execute commands, make network
#                    requests, or modify git state. Always require user
#                    confirmation before execution.
#
# Any tool not listed in either set defaults to REQUIRES_APPROVAL (fail-safe).

AUTO_APPROVE = {
    "file_read",
    "file_search",
    "list_directory",
    "git_status",
    "git_diff",
    "git_log",
    "git_show_file",
    "create_plan",
    "web_search",  # read-only external fetch; auto-approved for UX
}
REQUIRES_APPROVAL = {
    "file_write",
    "file_patch",
    "shell_exec",
    "git_commit",
}
