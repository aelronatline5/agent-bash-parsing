"""Unit tests for pre-parse workarounds applied before bashlex.parse().

The hook applies transformations to the raw command string to work around
bashlex limitations:
  1. Strip `time` keyword (and its flags like -p) from the front
  2. Replace arithmetic expansion $((...)) with a placeholder literal
  3. Replace extended test [[ ... ]] with `true`
"""

from __future__ import annotations

import pytest

from readonly_bash_hook import preparse_strip_time, preparse_command


# ---------------------------------------------------------------------------
# time keyword stripping
# ---------------------------------------------------------------------------

class TestStripTime:
    def test_time_simple(self):
        assert preparse_strip_time("time ls -la") == "ls -la"

    def test_time_with_p_flag(self):
        assert preparse_strip_time("time -p find . -name '*.py'") == "find . -name '*.py'"

    def test_time_only(self):
        """Bare `time` with no command after it."""
        result = preparse_strip_time("time")
        assert result.strip() == ""

    def test_no_time(self):
        assert preparse_strip_time("ls -la") == "ls -la"

    def test_time_not_at_start(self):
        """time in the middle of a command should NOT be stripped here
        (pre-parse only strips from the leading position)."""
        original = "ls && time cat foo"
        result = preparse_strip_time(original)
        # The leading command is ls, not time — no stripping
        assert result == original

    def test_time_with_multiple_flags(self):
        assert preparse_strip_time("time -p ls") == "ls"

    def test_time_preserves_rest(self):
        assert preparse_strip_time("time grep foo bar | wc -l") == "grep foo bar | wc -l"


# ---------------------------------------------------------------------------
# Full pre-parse pipeline
# ---------------------------------------------------------------------------

class TestPreparseCommand:
    def test_arithmetic_expansion_replaced(self):
        """$((...)) should be replaced with a safe placeholder literal."""
        result = preparse_command("echo $((1+2))")
        assert "$((" not in result

    def test_nested_arithmetic(self):
        result = preparse_command("echo $(( $(wc -l < f) + 1 ))")
        assert "$((" not in result

    def test_extended_test_replaced(self):
        """[[ ... ]] should be replaced with `true`."""
        result = preparse_command("[[ -f foo ]] && cat foo")
        assert "[[" not in result
        assert "cat foo" in result

    def test_extended_test_in_if(self):
        result = preparse_command("if [[ $x == y ]]; then ls; fi")
        assert "[[" not in result
        assert "ls" in result

    def test_passthrough_normal_command(self):
        """Commands without special constructs pass through unchanged."""
        cmd = "ls -la | grep foo"
        assert preparse_command(cmd) == cmd

    def test_combined_time_and_arithmetic(self):
        result = preparse_command("time echo $((1+2))")
        assert "time" not in result.split()[0] if result.strip() else True
        assert "$((" not in result

    def test_empty_string(self):
        assert preparse_command("") == ""

    def test_whitespace_only(self):
        assert preparse_command("   ").strip() == ""
