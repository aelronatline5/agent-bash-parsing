"""Output formatting and event processing for the hook."""

from __future__ import annotations

import json

from . import APPROVE, _debug
from .config import load_config_from_settings
from .pipeline import evaluate_command


def format_pretooluse_approval(cmd: str) -> str:
    """Format PreToolUse approval JSON output."""
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": f"Read-only command: {cmd}",
        }
    })


def format_permission_request_approval(cmd: str) -> str:
    """Format PermissionRequest approval JSON output."""
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PermissionRequest",
            "decision": {
                "behavior": "allow",
            },
        }
    })


def detect_event_type(stdin_json: str) -> str:
    """Extract hook_event_name from stdin JSON."""
    try:
        data = json.loads(stdin_json)
        return data.get("hook_event_name", "")
    except (json.JSONDecodeError, TypeError, AttributeError):  # pragma: no cover
        return ""


def process_hook_input(stdin_json: str) -> str | None:
    """Full pipeline: parse stdin → evaluate → format output.

    Returns JSON string for approval, or None/\"\" for fall-through.
    """
    try:
        data = json.loads(stdin_json)
    except (json.JSONDecodeError, TypeError):
        _debug(1, "Failed to parse stdin JSON")
        return None

    event = data.get("hook_event_name", "")
    tool_name = data.get("tool_name", "")

    if tool_name != "Bash":
        _debug(2, "Not a Bash tool call, skipping")
        return None

    tool_input = data.get("tool_input", {})
    cmd = tool_input.get("command", "")

    if not cmd or not cmd.strip():
        _debug(2, "Empty command, skipping")
        return None

    config = load_config_from_settings()  # pragma: no cover
    result = evaluate_command(cmd, config)  # pragma: no cover

    if result is APPROVE:  # pragma: no cover
        _debug(1, "APPROVED: %s", cmd)
        if event == "PreToolUse":
            return format_pretooluse_approval(cmd)
        elif event == "PermissionRequest":
            return format_permission_request_approval(cmd)
        # Unknown event type but approved — fall through
        return None

    _debug(1, "FALLTHROUGH: %s", cmd)  # pragma: no cover
    return None  # pragma: no cover
