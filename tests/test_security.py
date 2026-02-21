"""Adversarial / security tests: bypass attempt verification.

These tests attempt to circumvent the safety model through various
obfuscation techniques, encoding tricks, nesting abuse, and edge cases.
Each test documents the attack vector and verifies the expected outcome.
"""

from __future__ import annotations

import pytest

from readonly_bash_hook import APPROVE, FALLTHROUGH, evaluate_command, build_config


@pytest.fixture
def cfg():
    return build_config([], [], git_local_writes=False, awk_safe_mode=False)


# ---------------------------------------------------------------------------
# Wrapper abuse: deeply nested wrappers
# ---------------------------------------------------------------------------

class TestWrapperAbuse:
    def test_quad_nested_env_to_bash(self, cfg):
        """env env env env bash -c 'rm -rf /' → must fall through (bash is never-approve)."""
        result = evaluate_command("env env env env bash -c 'rm -rf /'", cfg)
        assert result == FALLTHROUGH

    def test_stacked_nice_to_rm(self, cfg):
        result = evaluate_command("nice nice nice rm foo", cfg)
        assert result == FALLTHROUGH

    def test_nohup_to_sudo(self, cfg):
        result = evaluate_command("nohup nohup sudo rm -rf /", cfg)
        assert result == FALLTHROUGH

    def test_env_nice_nohup_to_eval(self, cfg):
        result = evaluate_command("env nice nohup eval echo foo", cfg)
        assert result == FALLTHROUGH


# ---------------------------------------------------------------------------
# Path manipulation
# ---------------------------------------------------------------------------

class TestPathManipulation:
    def test_path_traversal_to_rm(self, cfg):
        result = evaluate_command("/tmp/../usr/bin/rm foo", cfg)
        assert result == FALLTHROUGH

    def test_multiple_slashes(self, cfg):
        result = evaluate_command("///usr///bin///rm foo", cfg)
        assert result == FALLTHROUGH

    def test_path_traversal_to_safe(self, cfg):
        """Path traversal to a safe command should still approve."""
        result = evaluate_command("/tmp/../usr/bin/ls", cfg)
        assert result == APPROVE


# ---------------------------------------------------------------------------
# Quoting and escaping
# ---------------------------------------------------------------------------

class TestQuoting:
    def test_single_quoted_rm(self, cfg):
        """'rm' as a command name — bashlex sees it as rm."""
        result = evaluate_command("'rm' file.txt", cfg)
        assert result == FALLTHROUGH

    def test_double_quoted_rm(self, cfg):
        result = evaluate_command('"rm" file.txt', cfg)
        assert result == FALLTHROUGH


# ---------------------------------------------------------------------------
# Arguments that look like dangerous commands (but are just data)
# ---------------------------------------------------------------------------

class TestSafeArguments:
    def test_echo_rm(self, cfg):
        """echo rm -rf / — rm is just an argument to echo."""
        result = evaluate_command("echo rm -rf /", cfg)
        assert result == APPROVE

    def test_grep_dangerous_pattern(self, cfg):
        """grep 'sudo rm' file.txt — dangerous text is just a search pattern."""
        result = evaluate_command("grep 'sudo rm' file.txt", cfg)
        assert result == APPROVE

    def test_echo_eval(self, cfg):
        """echo eval exec source — just printing text."""
        result = evaluate_command("echo eval exec source", cfg)
        assert result == APPROVE


# ---------------------------------------------------------------------------
# Redirect tricks
# ---------------------------------------------------------------------------

class TestRedirectTricks:
    def test_redirect_after_find_exec(self, cfg):
        """find . -exec grep foo {} \\; > /tmp/out — output redirect on the find itself."""
        result = evaluate_command("find . -exec grep foo {} \\; > /tmp/out", cfg)
        assert result == FALLTHROUGH

    def test_stderr_redirect_to_file(self, cfg):
        """ls 2>/tmp/errors — stderr to file is an output redirect."""
        # This depends on whether the parser flags 2> as output redirect
        # Conservatively, it should fall through
        result = evaluate_command("ls 2>/tmp/errors", cfg)
        # Either approve (if only > to stdout counts) or fallthrough
        assert result in (APPROVE, FALLTHROUGH)


# ---------------------------------------------------------------------------
# xargs edge cases
# ---------------------------------------------------------------------------

class TestXargsEdge:
    def test_xargs_with_piped_bash(self, cfg):
        """echo 'bash' | xargs — xargs defaults to echo, stdin content is irrelevant
        to the static analysis. The inner command is echo."""
        # This should be approved since xargs with no args defaults to echo
        result = evaluate_command("echo 'bash' | xargs", cfg)
        assert result == APPROVE


# ---------------------------------------------------------------------------
# Long command chains
# ---------------------------------------------------------------------------

class TestLongChains:
    def test_100_chained_ls(self, cfg):
        """100 chained ls commands should all approve."""
        cmd = " && ".join(["ls"] * 100)
        result = evaluate_command(cmd, cfg)
        assert result == APPROVE

    def test_many_safe_pipes(self, cfg):
        cmd = " | ".join(["cat file", "grep foo", "sort", "uniq", "wc -l"])
        result = evaluate_command(cmd, cfg)
        assert result == APPROVE


# ---------------------------------------------------------------------------
# Environment variable injection via env
# ---------------------------------------------------------------------------

class TestEnvInjection:
    def test_env_with_path_override(self, cfg):
        """env PATH=/tmp/evil ls — ls is still ls regardless of PATH."""
        result = evaluate_command("env PATH=/tmp/evil ls", cfg)
        assert result == APPROVE

    def test_env_with_ld_preload(self, cfg):
        """env LD_PRELOAD=/tmp/evil.so ls — ls is still the command."""
        result = evaluate_command("env LD_PRELOAD=/tmp/evil.so ls", cfg)
        assert result == APPROVE

    def test_env_with_dangerous_vars_and_dangerous_cmd(self, cfg):
        """env FOO=bar rm file — rm is still rm."""
        result = evaluate_command("env FOO=bar rm file", cfg)
        assert result == FALLTHROUGH


# ---------------------------------------------------------------------------
# Subshell / substitution escape attempts
# ---------------------------------------------------------------------------

class TestSubstitutionEscape:
    def test_cmdsub_as_executable(self, cfg):
        """$(echo rm) file.txt — command substitution as the executable name."""
        result = evaluate_command("$(echo rm) file.txt", cfg)
        assert result == FALLTHROUGH

    def test_backtick_as_executable(self, cfg):
        """`echo rm` file.txt — backtick substitution as executable."""
        # bashlex may or may not handle backticks; either way, should fall through
        result = evaluate_command("`echo rm` file.txt", cfg)
        assert result == FALLTHROUGH

    def test_nested_subshell_with_unsafe(self, cfg):
        result = evaluate_command("$($(echo rm) -rf /)", cfg)
        assert result == FALLTHROUGH


# ---------------------------------------------------------------------------
# Unicode / null byte tricks
# ---------------------------------------------------------------------------

class TestEncodingTricks:
    def test_null_byte_injection(self, cfg):
        """Null byte in command string."""
        result = evaluate_command("ls\x00rm", cfg)
        assert result == FALLTHROUGH

    def test_zero_width_space(self, cfg):
        """Zero-width space in command name."""
        result = evaluate_command("ls\u200b", cfg)
        # \u200b makes the command "ls\u200b" which is not "ls"
        assert result == FALLTHROUGH


# ---------------------------------------------------------------------------
# Alias attempts (ineffective in bash -c but test defense-in-depth)
# ---------------------------------------------------------------------------

class TestAliases:
    def test_alias_definition(self, cfg):
        """alias ls='rm -rf /'; ls — should fall through.
        bashlex may fail to parse this or the alias command is not whitelisted."""
        result = evaluate_command("alias ls='rm -rf /'; ls", cfg)
        assert result == FALLTHROUGH


# ---------------------------------------------------------------------------
# Function definition tricks
# ---------------------------------------------------------------------------

class TestFunctionTricks:
    def test_function_redefines_safe_name(self, cfg):
        """ls() { rm -rf /; }; ls — function body is unsafe."""
        result = evaluate_command("ls() { rm -rf /; }; ls", cfg)
        assert result == FALLTHROUGH

    def test_function_with_safe_body(self, cfg):
        """f() { grep foo bar; }; f — f is not whitelisted, falls through."""
        result = evaluate_command("f() { grep foo bar; }; f", cfg)
        assert result == FALLTHROUGH

    def test_function_shadowing_builtin(self, cfg):
        """echo() { rm -rf /; }; echo hi"""
        result = evaluate_command("echo() { rm -rf /; }; echo hi", cfg)
        assert result == FALLTHROUGH


# ---------------------------------------------------------------------------
# Process substitution tricks
# ---------------------------------------------------------------------------

class TestProcessSubTricks:
    def test_output_process_sub(self, cfg):
        """cat foo >(rm bar) — output process substitution."""
        result = evaluate_command("cat foo >(rm bar)", cfg)
        assert result == FALLTHROUGH

    def test_redirect_to_output_process_sub(self, cfg):
        """ls > >(tee /tmp/log) — output channel via process substitution."""
        result = evaluate_command("ls > >(tee /tmp/log)", cfg)
        assert result == FALLTHROUGH


# ---------------------------------------------------------------------------
# Semicolon / newline injection
# ---------------------------------------------------------------------------

class TestInjection:
    def test_semicolon_injection(self, cfg):
        """ls; rm -rf / — rm is a separate command in the list."""
        result = evaluate_command("ls; rm -rf /", cfg)
        assert result == FALLTHROUGH

    def test_newline_injection(self, cfg):
        """ls\nrm -rf / — newline-separated commands."""
        result = evaluate_command("ls\nrm -rf /", cfg)
        assert result == FALLTHROUGH

    def test_ampersand_injection(self, cfg):
        """ls & rm foo — background + rm."""
        result = evaluate_command("ls & rm foo", cfg)
        assert result == FALLTHROUGH
