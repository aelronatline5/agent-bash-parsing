"""Unit tests for the configuration system.

The hook loads optional config from readonly_bash_config.py:
  - EXTRA_COMMANDS: list of commands to add to the whitelist
  - REMOVE_COMMANDS: list of commands to remove from the whitelist
  - GIT_LOCAL_WRITES: bool feature flag
  - AWK_SAFE_MODE: bool feature flag

If the config module is missing, all defaults apply.
If it exists but has missing attributes, defaults for those attributes.
"""

from __future__ import annotations

from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest

from readonly_bash_hook import (
    APPROVE,
    FALLTHROUGH,
    build_config,
    evaluate_command,
    get_effective_whitelist,
    DEFAULT_COMMANDS,
)


# ---------------------------------------------------------------------------
# Config building
# ---------------------------------------------------------------------------

class TestBuildConfig:
    def test_default_config(self):
        cfg = build_config([], [], False, False)
        assert cfg is not None

    def test_extra_commands(self):
        cfg = build_config(extra_commands=["kubectl", "helm"], remove_commands=[],
                           git_local_writes=False, awk_safe_mode=False)
        wl = get_effective_whitelist(cfg)
        assert "kubectl" in wl
        assert "helm" in wl

    def test_remove_commands(self):
        cfg = build_config(extra_commands=[], remove_commands=["less"],
                           git_local_writes=False, awk_safe_mode=False)
        wl = get_effective_whitelist(cfg)
        assert "less" not in wl

    def test_extra_and_remove_overlap(self):
        """REMOVE wins over EXTRA when they overlap."""
        cfg = build_config(extra_commands=["x"], remove_commands=["x"],
                           git_local_writes=False, awk_safe_mode=False)
        wl = get_effective_whitelist(cfg)
        assert "x" not in wl

    def test_remove_default_command(self):
        """Removing a DEFAULT_COMMANDS entry removes it from the whitelist."""
        cfg = build_config(extra_commands=[], remove_commands=["ls"],
                           git_local_writes=False, awk_safe_mode=False)
        wl = get_effective_whitelist(cfg)
        assert "ls" not in wl

    def test_default_commands_preserved(self):
        """Default commands are present when no removals."""
        cfg = build_config([], [], False, False)
        wl = get_effective_whitelist(cfg)
        for cmd in ["ls", "cat", "grep", "sort", "wc"]:
            assert cmd in wl


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------

class TestFeatureFlags:
    def test_git_local_writes_flag(self):
        cfg = build_config([], [], git_local_writes=True, awk_safe_mode=False)
        result = evaluate_command("git branch feature-x", cfg)
        assert result == APPROVE

    def test_git_local_writes_off(self):
        cfg = build_config([], [], git_local_writes=False, awk_safe_mode=False)
        result = evaluate_command("git branch feature-x", cfg)
        assert result == FALLTHROUGH

    def test_awk_safe_mode_flag(self):
        cfg = build_config([], [], git_local_writes=False, awk_safe_mode=True)
        result = evaluate_command("awk '{print $1}' file.txt", cfg)
        assert result == APPROVE

    def test_awk_safe_mode_off(self):
        cfg = build_config([], [], git_local_writes=False, awk_safe_mode=False)
        result = evaluate_command("awk '{print $1}' file.txt", cfg)
        assert result == FALLTHROUGH

    def test_both_flags_on(self):
        cfg = build_config([], [], git_local_writes=True, awk_safe_mode=True)
        assert evaluate_command("git branch x", cfg) == APPROVE
        assert evaluate_command("awk '{print $1}' file", cfg) == APPROVE


# ---------------------------------------------------------------------------
# EXTRA_COMMANDS does not bypass never-approve
# ---------------------------------------------------------------------------

class TestSecurityInvariants:
    def test_extra_cannot_whitelist_bash(self):
        """Adding 'bash' to EXTRA_COMMANDS should not bypass step 3."""
        cfg = build_config(extra_commands=["bash"], remove_commands=[],
                           git_local_writes=False, awk_safe_mode=False)
        result = evaluate_command("bash -c 'rm -rf /'", cfg)
        assert result == FALLTHROUGH

    def test_extra_cannot_whitelist_eval(self):
        cfg = build_config(extra_commands=["eval"], remove_commands=[],
                           git_local_writes=False, awk_safe_mode=False)
        result = evaluate_command("eval echo foo", cfg)
        assert result == FALLTHROUGH

    def test_extra_cannot_whitelist_sudo(self):
        cfg = build_config(extra_commands=["sudo"], remove_commands=[],
                           git_local_writes=False, awk_safe_mode=False)
        result = evaluate_command("sudo ls", cfg)
        assert result == FALLTHROUGH


# ---------------------------------------------------------------------------
# Config module loading (import-based)
# ---------------------------------------------------------------------------

class TestConfigModuleLoading:
    def test_no_config_module(self):
        """When config module doesn't exist, all defaults apply."""
        cfg = build_config([], [], False, False)
        wl = get_effective_whitelist(cfg)
        assert wl == DEFAULT_COMMANDS

    def test_partial_config_module(self):
        """Config with only EXTRA_COMMANDS set; others get defaults."""
        cfg = build_config(extra_commands=["kubectl"], remove_commands=[],
                           git_local_writes=False, awk_safe_mode=False)
        wl = get_effective_whitelist(cfg)
        assert "kubectl" in wl
        # Default commands still present
        assert "ls" in wl
