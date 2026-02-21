"""Unit tests for Step 2 — NORMALIZE: resolve basename and unwrap wrappers.

Step 2 transforms a CommandFragment by:
  1. Resolving the basename: /usr/bin/ls → ls
  2. Iteratively unwrapping wrapper commands: env, nice, time, command, nohup
  3. Returning the normalized fragment (or an immediate APPROVE for `command -v/-V`)
"""

from __future__ import annotations

import pytest

from readonly_bash_hook import (
    APPROVE,
    CommandFragment,
    step2_normalize,
)


class TestBasenameResolution:
    def test_absolute_path(self):
        frag = CommandFragment("/usr/bin/ls", ["-la"], False)
        result = step2_normalize(frag)
        assert result.executable == "ls"
        assert result.args == ["-la"]

    def test_relative_path(self):
        frag = CommandFragment("./script.sh", [], False)
        result = step2_normalize(frag)
        assert result.executable == "script.sh"

    def test_deep_path(self):
        frag = CommandFragment("/usr/local/bin/rg", ["foo"], False)
        result = step2_normalize(frag)
        assert result.executable == "rg"

    def test_no_path(self):
        frag = CommandFragment("ls", ["-la"], False)
        result = step2_normalize(frag)
        assert result.executable == "ls"

    def test_multiple_slashes(self):
        frag = CommandFragment("///usr///bin///ls", [], False)
        result = step2_normalize(frag)
        assert result.executable == "ls"


class TestEnvUnwrapping:
    def test_env_simple(self):
        frag = CommandFragment("env", ["ls"], False)
        result = step2_normalize(frag)
        assert result.executable == "ls"
        assert result.args == []

    def test_env_with_var_assignment(self):
        frag = CommandFragment("env", ["FOO=bar", "ls", "-la"], False)
        result = step2_normalize(frag)
        assert result.executable == "ls"
        assert result.args == ["-la"]

    def test_env_with_i_flag(self):
        frag = CommandFragment("env", ["-i", "ls"], False)
        result = step2_normalize(frag)
        assert result.executable == "ls"

    def test_env_with_u_flag(self):
        frag = CommandFragment("env", ["-u", "HOME", "ls"], False)
        result = step2_normalize(frag)
        assert result.executable == "ls"

    def test_env_with_double_dash(self):
        frag = CommandFragment("env", ["--", "rm", "-rf"], False)
        result = step2_normalize(frag)
        assert result.executable == "rm"
        assert result.args == ["-rf"]

    def test_env_only_var_assignment(self):
        """env with only VAR=val and no command → should be treated as safe."""
        frag = CommandFragment("env", ["FOO=bar"], False)
        result = step2_normalize(frag)
        # No real command after stripping → either empty executable or immediate approve
        assert result == APPROVE or result.executable == ""

    def test_env_with_s_flag(self):
        frag = CommandFragment("env", ["-S", "ls -la"], False)
        result = step2_normalize(frag)
        # -S takes rest as single arg to split; implementation-dependent


class TestNiceUnwrapping:
    def test_nice_simple(self):
        frag = CommandFragment("nice", ["cat", "file"], False)
        result = step2_normalize(frag)
        assert result.executable == "cat"

    def test_nice_with_n_flag(self):
        frag = CommandFragment("nice", ["-n", "10", "cat", "file"], False)
        result = step2_normalize(frag)
        assert result.executable == "cat"
        assert "file" in result.args

    def test_nice_with_double_dash(self):
        frag = CommandFragment("nice", ["--", "cat", "file"], False)
        result = step2_normalize(frag)
        assert result.executable == "cat"


class TestNohupUnwrapping:
    def test_nohup_simple(self):
        frag = CommandFragment("nohup", ["ls"], False)
        result = step2_normalize(frag)
        assert result.executable == "ls"


class TestCommandUnwrapping:
    def test_command_v_immediate_approve(self):
        """command -v is a lookup, not execution → immediate APPROVE."""
        frag = CommandFragment("command", ["-v", "git"], False)
        result = step2_normalize(frag)
        assert result == APPROVE

    def test_command_V_immediate_approve(self):
        frag = CommandFragment("command", ["-V", "git"], False)
        result = step2_normalize(frag)
        assert result == APPROVE

    def test_command_p_stripped(self):
        frag = CommandFragment("command", ["-p", "ls"], False)
        result = step2_normalize(frag)
        assert result.executable == "ls"

    def test_command_double_dash(self):
        frag = CommandFragment("command", ["--", "ls"], False)
        result = step2_normalize(frag)
        assert result.executable == "ls"


class TestTimeUnwrapping:
    def test_time_simple(self):
        frag = CommandFragment("time", ["ls"], False)
        result = step2_normalize(frag)
        assert result.executable == "ls"

    def test_time_with_p_flag(self):
        frag = CommandFragment("time", ["-p", "ls"], False)
        result = step2_normalize(frag)
        assert result.executable == "ls"


class TestNestedUnwrapping:
    def test_env_nice_cat(self):
        frag = CommandFragment("env", ["nice", "-n", "5", "cat"], False)
        result = step2_normalize(frag)
        assert result.executable == "cat"

    def test_env_nice_bash(self):
        """Nested wrappers unwrap to reveal bash underneath."""
        frag = CommandFragment("env", ["nice", "bash", "-c", "anything"], False)
        result = step2_normalize(frag)
        assert result.executable == "bash"

    def test_triple_nested(self):
        frag = CommandFragment("env", ["nice", "nohup", "ls"], False)
        result = step2_normalize(frag)
        assert result.executable == "ls"

    def test_env_with_path_resolution(self):
        """env /usr/bin/ls → resolves path after unwrapping."""
        frag = CommandFragment("env", ["/usr/bin/ls"], False)
        result = step2_normalize(frag)
        assert result.executable == "ls"
