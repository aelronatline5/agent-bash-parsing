"""Unit tests for Step 7 — REJECT (default): fall through.

Step 7 is the final step. Any command that reaches here is rejected
(falls through to the user prompt). This is the default-deny rule.
"""

from __future__ import annotations

import pytest

from readonly_bash_hook import REJECT, CommandFragment, step7_default


def test_unknown_command():
    frag = CommandFragment("zzznotacommand", [], False)
    assert step7_default(frag) == REJECT


def test_write_command():
    frag = CommandFragment("rm", ["-rf", "/"], False)
    assert step7_default(frag) == REJECT


def test_network_command():
    frag = CommandFragment("curl", ["-s", "https://example.com"], False)
    assert step7_default(frag) == REJECT


@pytest.mark.parametrize("cmd", [
    "rm", "cp", "mv", "mkdir", "touch", "chmod", "chown",
    "tee", "curl", "wget", "make", "pip", "npm", "docker",
    "dd", "ln", "install", "patch", "truncate", "shred",
    "tar", "date", "cargo",
])
def test_excluded_commands_reach_step7(cmd):
    """Commands intentionally excluded from the whitelist reach step 7."""
    frag = CommandFragment(cmd, [], False)
    assert step7_default(frag) == REJECT
