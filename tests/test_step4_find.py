"""Unit tests for Step 4 handler: handle_find.

find is on the whitelist, but has destructive actions and exec modes:
  - Destructive: -delete, -fprint, -fprint0, -fprintf → REJECT
  - Exec: -exec, -execdir, -ok, -okdir → extract inner command,
    feed through 7-step pipeline recursively. ALL exec blocks must pass.
  - No dangerous flags → PASS
"""

from __future__ import annotations

import pytest

from readonly_bash_hook import PASS, REJECT, handle_find


# ---------------------------------------------------------------------------
# Destructive flags
# ---------------------------------------------------------------------------

class TestFindDestructive:
    @pytest.mark.parametrize("args, expected", [
        # Safe: no dangerous flags
        ([".", "-name", "*.py"], PASS),
        ([".", "-name", "*.py", "-type", "f"], PASS),
        (["/tmp", "-maxdepth", "2", "-name", "*.log"], PASS),

        # Reject: -delete
        ([".", "-name", "*.pyc", "-delete"], REJECT),

        # Reject: -fprint variants
        ([".", "-fprint", "/tmp/out.txt"], REJECT),
        ([".", "-fprint0", "/tmp/out.txt"], REJECT),
        ([".", "-fprintf", "/tmp/out.txt", "%p"], REJECT),
    ])
    def test_destructive_flags(self, args, expected):
        assert handle_find(args) == expected


# ---------------------------------------------------------------------------
# -exec with recursive evaluation
# ---------------------------------------------------------------------------

class TestFindExec:
    @pytest.mark.parametrize("args, expected", [
        # Safe inner command: grep
        ([".", "-exec", "grep", "foo", "{}", ";"], PASS),

        # Unsafe inner command: rm
        ([".", "-exec", "rm", "{}", ";"], REJECT),

        # Multiple exec blocks: both safe
        ([".", "-name", "*.py",
          "-exec", "grep", "foo", "{}", ";",
          "-exec", "wc", "-l", "{}", ";"], PASS),

        # Multiple exec blocks: second unsafe
        ([".", "-name", "*.py",
          "-exec", "grep", "foo", "{}", ";",
          "-exec", "rm", "{}", ";"], REJECT),

        # No command after stripping {} placeholders
        ([".", "-exec", "{}", ";"], REJECT),

        # sed -i inside find -exec → rejected by recursive evaluation
        ([".", "-exec", "sed", "-i", "s/x/y/", "{}", ";"], REJECT),

        # git read-only inside find -exec
        ([".", "-exec", "git", "log", "{}", ";"], PASS),

        # xargs inside find -exec (nested)
        ([".", "-exec", "xargs", "grep", "foo", "{}", ";"], PASS),
    ])
    def test_exec_semicolon(self, args, expected):
        assert handle_find(args) == expected, f"handle_find({args}) expected {expected}"

    @pytest.mark.parametrize("args, expected", [
        # -exec with + terminator
        ([".", "-exec", "grep", "foo", "{}", "+"], PASS),
        ([".", "-exec", "rm", "{}", "+"], REJECT),
    ])
    def test_exec_plus(self, args, expected):
        assert handle_find(args) == expected

    def test_execdir(self):
        assert handle_find([".", "-execdir", "grep", "foo", "{}", ";"]) == PASS
        assert handle_find([".", "-execdir", "rm", "{}", ";"]) == REJECT

    def test_ok(self):
        assert handle_find([".", "-ok", "grep", "foo", "{}", ";"]) == PASS
        assert handle_find([".", "-ok", "rm", "{}", ";"]) == REJECT

    def test_okdir(self):
        assert handle_find([".", "-okdir", "grep", "foo", "{}", ";"]) == PASS
        assert handle_find([".", "-okdir", "rm", "{}", ";"]) == REJECT


# ---------------------------------------------------------------------------
# Mixed: destructive + exec
# ---------------------------------------------------------------------------

class TestFindMixed:
    def test_delete_with_exec(self):
        """If both -delete and -exec are present, -delete causes REJECT."""
        args = [".", "-exec", "grep", "foo", "{}", ";", "-delete"]
        assert handle_find(args) == REJECT

    def test_exec_before_delete(self):
        args = [".", "-name", "*.tmp", "-exec", "cat", "{}", ";", "-delete"]
        assert handle_find(args) == REJECT
