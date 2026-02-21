"""Unit tests for Step 4 handler: handle_xargs.

xargs is on the whitelist, but executes an inner command. handle_xargs:
  1. Strips known xargs flags (with and without arguments)
  2. Feeds the remaining tokens (inner command + args) through the 7-step pipeline
  3. If no inner command remains → defaults to echo → PASS
"""

from __future__ import annotations

import pytest

from readonly_bash_hook import PASS, REJECT, handle_xargs


@pytest.mark.parametrize("args, expected", [
    # Simple safe inner command
    (["grep", "foo"], PASS),
    (["wc", "-l"], PASS),
    (["cat"], PASS),

    # Unsafe inner command
    (["rm"], REJECT),
    (["rm", "-rf", "/"], REJECT),

    # Flags stripped, safe inner command remains
    (["-I{}", "grep", "foo", "{}"], PASS),
    (["-0", "-P4", "wc", "-l"], PASS),
    (["--max-args=10", "wc", "-l"], PASS),
    (["-d", "\\n", "wc", "-l"], PASS),
    (["-a", "files.txt", "grep", "foo"], PASS),

    # Flags with arg consumption
    (["-I", "{}", "grep", "foo", "{}"], PASS),
    (["-L", "1", "grep", "foo"], PASS),
    (["-n", "10", "wc", "-l"], PASS),
    (["-P", "4", "cat"], PASS),
    (["-s", "1024", "cat"], PASS),
    (["-E", "END", "cat"], PASS),

    # Long flags with = syntax
    (["--max-args=5", "wc", "-l"], PASS),
    (["--max-procs=4", "cat"], PASS),
    (["--delimiter=\\n", "wc", "-l"], PASS),
    (["--replace={}", "grep", "foo", "{}"], PASS),

    # Boolean flags (no arg)
    (["-0", "wc", "-l"], PASS),
    (["-r", "wc", "-l"], PASS),
    (["-t", "cat"], PASS),
    (["--null", "wc", "-l"], PASS),
    (["--no-run-if-empty", "cat"], PASS),
    (["--verbose", "grep", "foo"], PASS),

    # No inner command → defaults to echo → PASS
    ([], PASS),
    (["-0"], PASS),
    (["-0", "-r"], PASS),

    # Never-approve inner command
    (["-I{}", "sh", "-c", "echo {}"], REJECT),
    (["bash", "-c", "ls"], REJECT),
    (["python3", "script.py"], REJECT),

    # git subcommands
    (["git", "log"], PASS),
    (["git", "push"], REJECT),

    # find as inner command (whitelisted)
    (["find", ".", "-name", "*.py"], PASS),
])
def test_handle_xargs(args, expected):
    result = handle_xargs(args)
    assert result == expected, f"handle_xargs({args}) = {result}, expected {expected}"
