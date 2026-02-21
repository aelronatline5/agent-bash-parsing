"""Shared fixtures and helpers for readonly_bash_hook tests.

These fixtures define the testing contract against the hook's public API.
The implementation module (readonly_bash_hook) must expose:
  - CommandFragment dataclass
  - parse_command(cmd_string) -> list[CommandFragment]
  - evaluate_fragments(fragments, config) -> APPROVE | FALLTHROUGH
  - evaluate_command(cmd_string, config) -> APPROVE | FALLTHROUGH
  - Individual step functions: step1_redirections, step2_normalize, etc.
  - Handler functions: handle_sed, handle_find, handle_xargs, handle_awk
  - Constants: DEFAULT_COMMANDS, NEVER_APPROVE, GIT_READONLY, GIT_LOCAL_WRITES_CMDS
  - Config helpers: build_config, get_effective_whitelist
  - Output formatters: format_pretooluse_approval, format_permission_request_approval
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from readonly_bash_hook import (
    APPROVE,
    FALLTHROUGH,
    CommandFragment,
    evaluate_command,
    parse_command,
    build_config,
)


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def default_config():
    """Config with all defaults: no extras, no removals, no feature flags."""
    return build_config(
        extra_commands=[],
        remove_commands=[],
        git_local_writes=False,
        awk_safe_mode=False,
    )


@pytest.fixture
def config_with_extras():
    """Config with EXTRA_COMMANDS and one REMOVE_COMMANDS entry."""
    return build_config(
        extra_commands=["kubectl", "helm"],
        remove_commands=["less"],
        git_local_writes=False,
        awk_safe_mode=False,
    )


@pytest.fixture
def config_git_local_writes():
    """Config with GIT_LOCAL_WRITES=True."""
    return build_config(
        extra_commands=[],
        remove_commands=[],
        git_local_writes=True,
        awk_safe_mode=False,
    )


@pytest.fixture
def config_awk_safe_mode():
    """Config with AWK_SAFE_MODE=True."""
    return build_config(
        extra_commands=[],
        remove_commands=[],
        git_local_writes=False,
        awk_safe_mode=True,
    )


@pytest.fixture
def config_both_flags():
    """Config with GIT_LOCAL_WRITES=True and AWK_SAFE_MODE=True."""
    return build_config(
        extra_commands=[],
        remove_commands=[],
        git_local_writes=True,
        awk_safe_mode=True,
    )


# ---------------------------------------------------------------------------
# Command evaluation fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def eval_cmd(default_config):
    """Callable: evaluate_command(bash_string) -> APPROVE | FALLTHROUGH.

    Uses default config.
    """
    def _eval(cmd: str):
        return evaluate_command(cmd, default_config)
    return _eval


@pytest.fixture
def eval_cmd_git_writes(config_git_local_writes):
    """evaluate_command with GIT_LOCAL_WRITES=True."""
    def _eval(cmd: str):
        return evaluate_command(cmd, config_git_local_writes)
    return _eval


@pytest.fixture
def eval_cmd_awk_safe(config_awk_safe_mode):
    """evaluate_command with AWK_SAFE_MODE=True."""
    def _eval(cmd: str):
        return evaluate_command(cmd, config_awk_safe_mode)
    return _eval


@pytest.fixture
def eval_cmd_both(config_both_flags):
    """evaluate_command with both feature flags enabled."""
    def _eval(cmd: str):
        return evaluate_command(cmd, config_both_flags)
    return _eval


# ---------------------------------------------------------------------------
# Fragment factory
# ---------------------------------------------------------------------------

@pytest.fixture
def make_fragment():
    """Factory: make_fragment("ls", ["-la"], has_output_redirect=False)."""
    def _make(executable: str, args: list[str] | None = None,
              has_output_redirect: bool = False) -> CommandFragment:
        return CommandFragment(
            executable=executable,
            args=args or [],
            has_output_redirect=has_output_redirect,
        )
    return _make


# ---------------------------------------------------------------------------
# JSON stdin builders for E2E tests
# ---------------------------------------------------------------------------

def make_pretooluse_input(command: str) -> str:
    """Build JSON stdin payload for a PreToolUse hook event."""
    return json.dumps({
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    })


def make_permission_request_input(command: str) -> str:
    """Build JSON stdin payload for a PermissionRequest hook event."""
    return json.dumps({
        "hook_event_name": "PermissionRequest",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    })
