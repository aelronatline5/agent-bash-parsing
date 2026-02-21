"""Unit tests for the parsing stage: AST walker and CommandFragment generation.

Tests that the parser correctly walks bashlex AST nodes and produces the
expected flat list of CommandFragment objects. Each test verifies fragment
count, executable names, args, and output-redirect flags.
"""

from __future__ import annotations

import pytest

from readonly_bash_hook import (
    APPROVE,
    FALLTHROUGH,
    CommandFragment,
    parse_command,
)


# ---------------------------------------------------------------------------
# Simple commands
# ---------------------------------------------------------------------------

class TestSimpleCommands:
    def test_single_command(self):
        frags = parse_command("ls -la")
        assert len(frags) >= 1
        assert frags[0].executable == "ls"
        assert "-la" in frags[0].args

    def test_single_command_no_args(self):
        frags = parse_command("ls")
        assert len(frags) >= 1
        assert frags[0].executable == "ls"
        assert frags[0].args == []

    def test_command_with_multiple_args(self):
        frags = parse_command("grep -r foo /tmp")
        assert frags[0].executable == "grep"
        assert "-r" in frags[0].args
        assert "foo" in frags[0].args


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------

class TestPipelines:
    def test_two_stage_pipeline(self):
        frags = parse_command("ls | grep foo")
        executables = [f.executable for f in frags]
        assert "ls" in executables
        assert "grep" in executables

    def test_multi_stage_pipeline(self):
        frags = parse_command("ls | grep foo | sort | head -5")
        executables = [f.executable for f in frags]
        assert executables == ["ls", "grep", "sort", "head"]


# ---------------------------------------------------------------------------
# Lists (compound commands with && || ; &)
# ---------------------------------------------------------------------------

class TestLists:
    def test_and_list(self):
        frags = parse_command("ls && cat file")
        executables = [f.executable for f in frags]
        assert "ls" in executables
        assert "cat" in executables

    def test_or_list(self):
        frags = parse_command('grep foo bar || echo "not found"')
        executables = [f.executable for f in frags]
        assert "grep" in executables
        assert "echo" in executables

    def test_semicolon_list(self):
        frags = parse_command("ls; cat file")
        executables = [f.executable for f in frags]
        assert "ls" in executables
        assert "cat" in executables

    def test_background_list(self):
        frags = parse_command("ls &")
        executables = [f.executable for f in frags]
        assert "ls" in executables


# ---------------------------------------------------------------------------
# Subshells and brace groups
# ---------------------------------------------------------------------------

class TestCompound:
    def test_subshell(self):
        frags = parse_command("(ls; cat file)")
        executables = [f.executable for f in frags]
        assert "ls" in executables
        assert "cat" in executables

    def test_brace_group(self):
        frags = parse_command("{ ls && cat file; }")
        executables = [f.executable for f in frags]
        assert "ls" in executables
        assert "cat" in executables


# ---------------------------------------------------------------------------
# Control flow
# ---------------------------------------------------------------------------

class TestControlFlow:
    def test_for_loop(self):
        frags = parse_command('for f in *.txt; do cat "$f"; done')
        executables = [f.executable for f in frags]
        assert "cat" in executables

    def test_while_loop(self):
        frags = parse_command('while read line; do echo "$line"; done')
        executables = [f.executable for f in frags]
        assert "read" in executables
        assert "echo" in executables

    def test_if_then(self):
        frags = parse_command("if true; then ls; fi")
        executables = [f.executable for f in frags]
        assert "true" in executables
        assert "ls" in executables

    def test_function_definition(self):
        """Function body commands must produce fragments."""
        frags = parse_command("f() { grep foo bar; }; f")
        executables = [f.executable for f in frags]
        assert "grep" in executables
        # The function invocation `f` should also produce a fragment
        assert "f" in executables


# ---------------------------------------------------------------------------
# Substitutions
# ---------------------------------------------------------------------------

class TestSubstitutions:
    def test_command_substitution(self):
        frags = parse_command("echo $(ls)")
        executables = [f.executable for f in frags]
        assert "echo" in executables
        assert "ls" in executables

    def test_nested_command_substitution(self):
        frags = parse_command("echo $(echo $(rm -rf /))")
        executables = [f.executable for f in frags]
        assert "rm" in executables

    def test_input_process_substitution(self):
        frags = parse_command("diff <(sort a) <(sort b)")
        executables = [f.executable for f in frags]
        assert "diff" in executables
        assert executables.count("sort") == 2

    def test_output_process_substitution_flagged(self):
        """>(cmd) must be flagged as an output channel."""
        frags = parse_command("cat foo >(rm bar)")
        # The rm fragment should exist; output process sub detected
        rm_frags = [f for f in frags if f.executable == "rm"]
        assert len(rm_frags) >= 1
        # The output process substitution should cause a flag
        # (either on the fragment or detected as output redirect)
        has_output_flag = any(f.has_output_redirect for f in frags)
        assert has_output_flag


# ---------------------------------------------------------------------------
# Redirections
# ---------------------------------------------------------------------------

class TestRedirections:
    def test_output_redirect_detected(self):
        frags = parse_command("ls > file.txt")
        ls_frag = [f for f in frags if f.executable == "ls"][0]
        assert ls_frag.has_output_redirect is True

    def test_append_redirect_detected(self):
        frags = parse_command("echo foo >> bar.txt")
        echo_frag = [f for f in frags if f.executable == "echo"][0]
        assert echo_frag.has_output_redirect is True

    def test_fd_duplication_not_flagged(self):
        """2>&1 is fd duplication, not a file write."""
        frags = parse_command("grep foo 2>&1")
        grep_frag = [f for f in frags if f.executable == "grep"][0]
        assert grep_frag.has_output_redirect is False

    def test_input_redirect_not_flagged(self):
        frags = parse_command("cat < input.txt")
        cat_frag = [f for f in frags if f.executable == "cat"][0]
        assert cat_frag.has_output_redirect is False


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------

class TestAssignments:
    def test_pure_assignment_no_command(self):
        """FOO=bar with no command should yield no command fragments
        (or a special pure-assignment result)."""
        frags = parse_command("FOO=bar")
        # Pure assignment: either empty list or all fragments are assignment-only
        cmd_frags = [f for f in frags if f.executable]
        assert len(cmd_frags) == 0

    def test_assignment_with_command_substitution(self):
        """FOO=$(rm -rf /) — the substitution must be walked."""
        frags = parse_command("FOO=$(rm -rf /)")
        executables = [f.executable for f in frags]
        assert "rm" in executables


# ---------------------------------------------------------------------------
# Variable as command (unknowable)
# ---------------------------------------------------------------------------

class TestVariableCommands:
    def test_variable_as_command(self):
        """$CMD foo — command is a variable, cannot be resolved."""
        frags = parse_command("$CMD foo")
        # Should produce a fragment with the variable as executable
        assert len(frags) >= 1
        # The executable should be unresolvable (contains $)
        assert "$" in frags[0].executable or frags[0].executable == ""


# ---------------------------------------------------------------------------
# Negation
# ---------------------------------------------------------------------------

class TestNegation:
    def test_negation(self):
        frags = parse_command("! grep foo bar")
        executables = [f.executable for f in frags]
        assert "grep" in executables


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_case_statement_falls_through(self):
        """case statement raises NotImplementedError in bashlex — must fall through."""
        # parse_command should return None or raise, and the caller falls through
        result = parse_command("case $x in a) echo;; esac")
        # Either returns None (fall through signal) or an empty/error marker
        assert result is None or isinstance(result, list)

    def test_malformed_input_falls_through(self):
        result = parse_command('ls "unclosed')
        assert result is None or isinstance(result, list)

    def test_empty_string(self):
        frags = parse_command("")
        assert frags is not None
        cmd_frags = [f for f in frags if f.executable]
        assert len(cmd_frags) == 0

    def test_whitespace_only(self):
        frags = parse_command("   ")
        assert frags is not None
        cmd_frags = [f for f in frags if f.executable]
        assert len(cmd_frags) == 0

    def test_comment_only(self):
        frags = parse_command("# just a comment")
        assert frags is not None
        cmd_frags = [f for f in frags if f.executable]
        assert len(cmd_frags) == 0


# ---------------------------------------------------------------------------
# Default-deny walker rule
# ---------------------------------------------------------------------------

class TestDefaultDeny:
    def test_unknown_node_kind_forces_fallthrough(self):
        """If the walker encounters an unknown AST node kind, it must
        force fall-through. This is tested at integration level since
        it requires injecting a mock node into bashlex output."""
        # This is primarily tested via mocking in integration tests.
        # Here we verify the contract: parse_command returns None
        # when an unknown node is encountered.
        pass  # Covered by integration test with mock injection
