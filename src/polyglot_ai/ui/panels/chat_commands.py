"""Slash-command handler for the chat panel.

Extracted from ``chat_panel.py``. Every ``/cmd`` the user can type in
the chat input is dispatched through :func:`handle_slash_command`.

The handler takes the panel as its first argument so it can reach
whatever state the command needs (model combo, conversation, MCP
client, etc.) without hard-wiring any particular couplings into a
subclass hierarchy. Adding a new slash command is a matter of adding
a branch below — no panel-side boilerplate required.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from polyglot_ai.ui.panels.chat_panel import ChatPanel

logger = logging.getLogger(__name__)


#: Text shown by ``/help``. Kept here (not inlined) so the catalogue
#: stays next to the dispatch that uses it.
HELP_TEXT = (
    "**Available commands:**\n"
    "• `/clear` — Clear conversation\n"
    "• `/new` — Start new conversation\n"
    "• `/model [name]` — Show or switch model\n"
    "• `/review [branch]` — Review code changes\n"
    "• `/fix [issue]` — Fix an error or failing test\n"
    "• `/test [command]` — Run tests and fix failures\n"
    "• `/explain [target]` — Explain code or project\n"
    "• `/commit [message]` — Stage and commit changes\n"
    "• `/git [command]` — Run a git command\n"
    "• `/workflow [name] [--key value]` — Run a multi-step workflow\n"
    "• `/status` — Show session info\n"
    "• `/help` — Show this help"
)


def handle_slash_command(panel: "ChatPanel", text: str) -> bool:
    """Dispatch a ``/command`` typed in the chat input.

    Returns ``True`` if the command was handled (and the input should
    be cleared) or ``False`` to let the original text fall through as
    a regular chat message (e.g. typing ``/wat`` that isn't a real
    command should still be sendable).
    """
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/clear":
        panel._clear_messages()
        panel._current_conversation = None
        panel._add_system_message("Conversation cleared.")
        return True

    if cmd == "/new":
        panel._new_conversation()
        return True

    if cmd == "/model":
        if arg:
            for i in range(panel._model_combo.count()):
                if arg.lower() in panel._model_combo.itemText(i).lower():
                    panel._model_combo.setCurrentIndex(i)
                    panel._add_system_message(
                        f"Switched to model: {panel._model_combo.itemText(i).strip()}"
                    )
                    return True
            panel._add_system_message(
                f"Model '{arg}' not found. Available models are in the dropdown."
            )
        else:
            _, display = panel._get_selected_model()
            panel._add_system_message(f"Current model: {display}")
        return True

    if cmd == "/review":
        panel._run_code_review(arg)
        return True

    if cmd == "/status":
        project_root = panel._get_project_root()
        provider_count = (
            len(panel._provider_manager.get_all_providers()) if panel._provider_manager else 0
        )
        msg_count = len(panel._current_conversation.messages) if panel._current_conversation else 0
        mcp_servers = panel._mcp_client.connected_servers if panel._mcp_client else []
        mcp_tools = len(panel._mcp_client.available_tools) if panel._mcp_client else 0
        panel._add_system_message(
            f"**Project:** {project_root or 'None'}\n"
            f"**Providers:** {provider_count} active\n"
            f"**Messages:** {msg_count} in current conversation\n"
            f"**MCP Servers:** {len(mcp_servers)} connected ({mcp_tools} tools)"
        )
        return True

    if cmd == "/fix":
        issue = arg or "the last error or failing test"
        panel._inject_ai_prompt(
            f"Please analyze and fix: {issue}. Read the relevant files, "
            "identify the problem, and propose a fix."
        )
        return True

    if cmd == "/test":
        test_cmd = arg or ""
        if test_cmd:
            panel._inject_ai_prompt(
                f"Run this test command: `{test_cmd}`. If it fails, analyze "
                "the output and fix the issues."
            )
        else:
            panel._inject_ai_prompt(
                "Detect the test framework for this project (pytest, jest, go test, etc.), "
                "run the tests, and if any fail, analyze the output and propose fixes."
            )
        return True

    if cmd == "/explain":
        target = arg or "the current project"
        panel._inject_ai_prompt(
            f"Explain {target} clearly and concisely. Include purpose, "
            "key components, and how they fit together."
        )
        return True

    if cmd == "/commit":
        msg = arg or ""
        if msg:
            panel._inject_ai_prompt(f'Stage all changes and commit with message: "{msg}"')
        else:
            panel._inject_ai_prompt(
                "Look at the current git diff, generate a clear conventional "
                "commit message, then stage and commit the changes. Show me "
                "the message before committing."
            )
        return True

    if cmd == "/git":
        if arg:
            panel._inject_ai_prompt(f"Run `git {arg}` and show me the output.")
        else:
            panel._inject_ai_prompt(
                "Show me the current git status including branch, staged/unstaged "
                "changes, and recent commits."
            )
        return True

    if cmd == "/workflow":
        from polyglot_ai.ui.panels.chat_workflows import handle_workflow_command

        handle_workflow_command(panel, arg)
        return True

    if cmd == "/help":
        panel._add_system_message(HELP_TEXT)
        return True

    # Unknown /slash — let the caller send it as a regular message.
    return False
