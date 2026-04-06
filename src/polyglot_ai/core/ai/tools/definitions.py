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
    # ── Docker tools ────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "docker_list_containers",
            "description": "List Docker containers on the local machine with their status, image, and ports. Use this to see what's running.",
            "parameters": {
                "type": "object",
                "properties": {
                    "all": {
                        "type": "boolean",
                        "description": "Include stopped containers (default: true)",
                    },
                    "status": {
                        "type": "string",
                        "description": "Filter by status: running, exited, paused, etc.",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_list_images",
            "description": "List Docker images on the local machine with repository, tag, and size.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_container_logs",
            "description": "Get recent logs from a Docker container. Use this to troubleshoot running or crashed containers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {
                        "type": "string",
                        "description": "Container name or ID",
                    },
                    "tail": {
                        "type": "integer",
                        "description": "Number of lines to retrieve (default: 200)",
                    },
                },
                "required": ["container"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_inspect",
            "description": "Get detailed information about a Docker container or image (state, network, mounts, environment).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Container or image name/ID",
                    },
                },
                "required": ["name"],
            },
        },
    },
    # ── Kubernetes tools ────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "k8s_current_context",
            "description": "Get the active Kubernetes cluster context name.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "k8s_list_pods",
            "description": "List Kubernetes pods with their status and restart counts. Defaults to all namespaces.",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {
                        "type": "string",
                        "description": "Filter to a specific namespace (optional)",
                    },
                    "context": {
                        "type": "string",
                        "description": "kubectl context to use (optional)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "k8s_list_deployments",
            "description": "List Kubernetes deployments with ready/desired replica counts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "Specific namespace (optional)"},
                    "context": {"type": "string", "description": "kubectl context (optional)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "k8s_list_services",
            "description": "List Kubernetes services with type, cluster IP, and ports.",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "Specific namespace (optional)"},
                    "context": {"type": "string", "description": "kubectl context (optional)"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "k8s_pod_logs",
            "description": "Get recent logs from a Kubernetes pod. Use this to troubleshoot pod issues.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pod": {"type": "string", "description": "Pod name"},
                    "namespace": {"type": "string", "description": "Namespace the pod is in"},
                    "tail": {"type": "integer", "description": "Lines to retrieve (default: 200)"},
                    "container": {
                        "type": "string",
                        "description": "Specific container in pod (optional)",
                    },
                    "context": {"type": "string", "description": "kubectl context (optional)"},
                },
                "required": ["pod", "namespace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "k8s_describe",
            "description": "Get detailed info for a Kubernetes resource (pod, deployment, service, etc.) including events.",
            "parameters": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "Resource type: pod, deployment, service, configmap, etc.",
                    },
                    "name": {"type": "string", "description": "Resource name"},
                    "namespace": {"type": "string", "description": "Namespace"},
                    "context": {"type": "string", "description": "kubectl context (optional)"},
                },
                "required": ["type", "name", "namespace"],
            },
        },
    },
    # ── Database tools ──────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "db_list_connections",
            "description": "List available database connections configured in the app.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "db_get_schema",
            "description": "Get the schema (tables, columns, types) of a database connection. Use this to understand what's in a database before querying.",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection": {
                        "type": "string",
                        "description": "Name of the database connection",
                    },
                },
                "required": ["connection"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "db_query",
            "description": "Execute a SQL query against a database connection. Only read-only queries (SELECT, SHOW, DESCRIBE, EXPLAIN) are allowed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection": {
                        "type": "string",
                        "description": "Name of the database connection",
                    },
                    "sql": {"type": "string", "description": "The SQL query to execute"},
                    "max_rows": {
                        "type": "integer",
                        "description": "Max rows to return (default: 100)",
                    },
                },
                "required": ["connection", "sql"],
            },
        },
    },
    # ── Mutating Docker tools (require approval) ────────────────────
    {
        "type": "function",
        "function": {
            "name": "docker_restart",
            "description": "Restart a Docker container. Use this to fix transient issues like memory leaks or stuck processes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {"type": "string", "description": "Container name or ID"},
                },
                "required": ["container"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_stop",
            "description": "Stop a running Docker container.",
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {"type": "string", "description": "Container name or ID"},
                },
                "required": ["container"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_start",
            "description": "Start a stopped Docker container.",
            "parameters": {
                "type": "object",
                "properties": {
                    "container": {"type": "string", "description": "Container name or ID"},
                },
                "required": ["container"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "docker_remove",
            "description": "Remove a Docker container or image. Container must be stopped unless force=true.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Container or image name/ID"},
                    "is_image": {
                        "type": "boolean",
                        "description": "Set true to remove an image instead of a container",
                    },
                    "force": {"type": "boolean", "description": "Force removal even if running"},
                },
                "required": ["name"],
            },
        },
    },
    # ── Mutating Kubernetes tools (require approval) ────────────────
    {
        "type": "function",
        "function": {
            "name": "k8s_delete_pod",
            "description": "Delete a pod. Kubernetes will recreate it if managed by a deployment. Use this to force a pod restart.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pod": {"type": "string", "description": "Pod name"},
                    "namespace": {"type": "string", "description": "Namespace"},
                    "context": {"type": "string", "description": "kubectl context (optional)"},
                },
                "required": ["pod", "namespace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "k8s_restart_deployment",
            "description": "Trigger a rolling restart of a deployment (graceful pod replacement).",
            "parameters": {
                "type": "object",
                "properties": {
                    "deployment": {"type": "string", "description": "Deployment name"},
                    "namespace": {"type": "string", "description": "Namespace"},
                    "context": {"type": "string", "description": "kubectl context (optional)"},
                },
                "required": ["deployment", "namespace"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "k8s_scale_deployment",
            "description": "Scale a deployment to a specific number of replicas.",
            "parameters": {
                "type": "object",
                "properties": {
                    "deployment": {"type": "string", "description": "Deployment name"},
                    "namespace": {"type": "string", "description": "Namespace"},
                    "replicas": {"type": "integer", "description": "Desired replica count"},
                    "context": {"type": "string", "description": "kubectl context (optional)"},
                },
                "required": ["deployment", "namespace", "replicas"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "k8s_apply",
            "description": "Apply a Kubernetes manifest from YAML content or a file path. Creates or updates resources.",
            "parameters": {
                "type": "object",
                "properties": {
                    "yaml": {"type": "string", "description": "YAML manifest content"},
                    "file": {"type": "string", "description": "Path to a manifest file"},
                    "context": {"type": "string", "description": "kubectl context (optional)"},
                },
            },
        },
    },
    # ── Mutating database tool (requires approval) ──────────────────
    {
        "type": "function",
        "function": {
            "name": "db_execute",
            "description": "Execute a write SQL statement (INSERT, UPDATE, DELETE, CREATE, ALTER, DROP). Destructive — requires user approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "connection": {"type": "string", "description": "Database connection name"},
                    "sql": {"type": "string", "description": "SQL statement to execute"},
                },
                "required": ["connection", "sql"],
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
    # Docker read-only tools
    "docker_list_containers",
    "docker_list_images",
    "docker_container_logs",
    "docker_inspect",
    # Kubernetes read-only tools
    "k8s_current_context",
    "k8s_list_pods",
    "k8s_list_deployments",
    "k8s_list_services",
    "k8s_pod_logs",
    "k8s_describe",
    # Database read-only tools
    "db_list_connections",
    "db_get_schema",
    "db_query",  # Auto-approved; write queries are rejected at execution time
}
REQUIRES_APPROVAL = {
    "file_write",
    "file_patch",
    "shell_exec",
    "git_commit",
    # Mutating Docker tools
    "docker_restart",
    "docker_stop",
    "docker_start",
    "docker_remove",
    # Mutating Kubernetes tools
    "k8s_delete_pod",
    "k8s_restart_deployment",
    "k8s_scale_deployment",
    "k8s_apply",
    # Mutating database tool
    "db_execute",
}
