"""Unit tests for hook event modes: PreToolUse vs PermissionRequest.

The hook auto-detects the event from hook_event_name in stdin JSON.
Core logic is identical — only the output format differs:
  - PreToolUse: hookSpecificOutput.permissionDecision = "allow"
  - PermissionRequest: hookSpecificOutput.decision.behavior = "allow"
  - Fall-through: empty stdout, exit 0 (both modes)
"""

from __future__ import annotations

import json

import pytest

from readonly_bash_hook import (
    format_pretooluse_approval,
    format_permission_request_approval,
    detect_event_type,
    process_hook_input,
)


# ---------------------------------------------------------------------------
# PreToolUse output format
# ---------------------------------------------------------------------------

class TestPreToolUseFormat:
    def test_approval_format(self):
        output = format_pretooluse_approval("ls -la")
        data = json.loads(output)
        assert data["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert data["hookSpecificOutput"]["permissionDecision"] == "allow"

    def test_approval_includes_reason(self):
        output = format_pretooluse_approval("ls -la")
        data = json.loads(output)
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "ls -la" in reason or "ls" in reason

    def test_output_is_valid_json(self):
        output = format_pretooluse_approval("cat file.txt")
        json.loads(output)  # Should not raise


# ---------------------------------------------------------------------------
# PermissionRequest output format
# ---------------------------------------------------------------------------

class TestPermissionRequestFormat:
    def test_approval_format(self):
        output = format_permission_request_approval("ls -la")
        data = json.loads(output)
        assert data["hookSpecificOutput"]["hookEventName"] == "PermissionRequest"
        assert data["hookSpecificOutput"]["decision"]["behavior"] == "allow"

    def test_output_is_valid_json(self):
        output = format_permission_request_approval("grep foo bar")
        json.loads(output)  # Should not raise


# ---------------------------------------------------------------------------
# Event auto-detection
# ---------------------------------------------------------------------------

class TestEventDetection:
    def test_detects_pretooluse(self):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
        event = detect_event_type(json.dumps(payload))
        assert event == "PreToolUse"

    def test_detects_permission_request(self):
        payload = {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
        }
        event = detect_event_type(json.dumps(payload))
        assert event == "PermissionRequest"


# ---------------------------------------------------------------------------
# Non-Bash tool bail-out
# ---------------------------------------------------------------------------

class TestNonBashBailout:
    def test_non_bash_tool_falls_through(self):
        """tool_name != 'Bash' → fall through (empty output)."""
        payload = {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Read",
            "tool_input": {"file_path": "/tmp/x"},
        }
        result = process_hook_input(json.dumps(payload))
        assert result is None or result == ""

    def test_write_tool_falls_through(self):
        payload = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/x", "content": "hello"},
        }
        result = process_hook_input(json.dumps(payload))
        assert result is None or result == ""


# ---------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------

class TestMalformedInput:
    def test_empty_stdin(self):
        result = process_hook_input("")
        assert result is None or result == ""

    def test_invalid_json(self):
        result = process_hook_input("not json at all")
        assert result is None or result == ""

    def test_missing_tool_name(self):
        payload = {
            "hook_event_name": "PermissionRequest",
            "tool_input": {"command": "ls"},
        }
        result = process_hook_input(json.dumps(payload))
        assert result is None or result == ""

    def test_missing_command(self):
        payload = {
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {},
        }
        result = process_hook_input(json.dumps(payload))
        assert result is None or result == ""
