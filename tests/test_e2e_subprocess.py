"""End-to-end subprocess tests: run the hook as Claude Code would invoke it.

These tests spawn the hook script as a subprocess, feed JSON on stdin,
and verify stdout output, exit codes, and JSON format.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

# Path to the hook script (adjacent to the test directory)
HOOK_SCRIPT = str(Path(__file__).resolve().parent.parent / "readonly_bash_hook.py")


def run_hook(
    command: str,
    event: str = "PermissionRequest",
    tool_name: str = "Bash",
    timeout: int = 10,
) -> subprocess.CompletedProcess:
    """Run the hook as a subprocess with JSON on stdin."""
    stdin_json = json.dumps({
        "hook_event_name": event,
        "tool_name": tool_name,
        "tool_input": {"command": command},
    })
    return subprocess.run(
        ["python3", HOOK_SCRIPT],
        input=stdin_json,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_hook_raw(stdin: str, timeout: int = 10) -> subprocess.CompletedProcess:
    """Run the hook with arbitrary stdin."""
    return subprocess.run(
        ["python3", HOOK_SCRIPT],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# PermissionRequest mode
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestPermissionRequest:
    def test_approve_ls(self):
        result = run_hook("ls -la")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["hookSpecificOutput"]["decision"]["behavior"] == "allow"

    def test_approve_cat(self):
        result = run_hook("cat file.txt")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["hookSpecificOutput"]["decision"]["behavior"] == "allow"

    def test_approve_pipeline(self):
        result = run_hook("ls | grep foo | sort")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["hookSpecificOutput"]["decision"]["behavior"] == "allow"

    def test_approve_git_log(self):
        result = run_hook("git log --oneline")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["hookSpecificOutput"]["decision"]["behavior"] == "allow"

    def test_fallthrough_rm(self):
        result = run_hook("rm foo")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_fallthrough_python3(self):
        result = run_hook("python3 script.py")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_fallthrough_redirect(self):
        result = run_hook("ls > file.txt")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_fallthrough_sed_i(self):
        result = run_hook("sed -i 's/foo/bar/' file.txt")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_fallthrough_git_push(self):
        result = run_hook("git push origin main")
        assert result.returncode == 0
        assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# PreToolUse mode
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestPreToolUse:
    def test_approve_ls(self):
        result = run_hook("ls -la", event="PreToolUse")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["hookSpecificOutput"]["permissionDecision"] == "allow"
        assert "hookEventName" in data["hookSpecificOutput"]

    def test_approve_includes_reason(self):
        result = run_hook("ls -la", event="PreToolUse")
        data = json.loads(result.stdout)
        assert "permissionDecisionReason" in data["hookSpecificOutput"]

    def test_fallthrough_rm(self):
        result = run_hook("rm foo", event="PreToolUse")
        assert result.returncode == 0
        assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Non-Bash tool
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestNonBashTool:
    def test_read_tool_falls_through(self):
        stdin_json = json.dumps({
            "hook_event_name": "PermissionRequest",
            "tool_name": "Read",
            "tool_input": {"file_path": "/tmp/x"},
        })
        result = run_hook_raw(stdin_json)
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_write_tool_falls_through(self):
        stdin_json = json.dumps({
            "hook_event_name": "PermissionRequest",
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/x", "content": "hi"},
        })
        result = run_hook_raw(stdin_json)
        assert result.returncode == 0
        assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Malformed stdin
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestMalformedInput:
    def test_invalid_json(self):
        result = run_hook_raw("not json at all")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_empty_stdin(self):
        result = run_hook_raw("")
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_partial_json(self):
        result = run_hook_raw('{"hook_event_name": "PermissionRequest"')
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_missing_tool_input(self):
        stdin_json = json.dumps({
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
        })
        result = run_hook_raw(stdin_json)
        assert result.returncode == 0
        assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestExitCodes:
    def test_approve_exit_0(self):
        result = run_hook("ls")
        assert result.returncode == 0

    def test_fallthrough_exit_0(self):
        result = run_hook("rm foo")
        assert result.returncode == 0

    def test_malformed_exit_0(self):
        result = run_hook_raw("garbage")
        assert result.returncode == 0
