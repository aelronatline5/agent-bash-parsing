"""Unit tests for Step 3 — REJECT (unconditional): never-approve gate.

Step 3 rejects any command on the NEVER_APPROVE list. These are interpreters
and escape hatches that can bypass the safety model entirely.

When AWK_SAFE_MODE is disabled (default), awk/gawk/mawk/nawk are also on
this list. When AWK_SAFE_MODE is enabled, they are removed from NEVER_APPROVE
and handled by step 4 instead.
"""

from __future__ import annotations

import pytest

from readonly_bash_hook import (
    REJECT,
    NEXT,
    CommandFragment,
    step3_never_approve,
)


# All members of the NEVER_APPROVE set
NEVER_APPROVE_COMMANDS = [
    # Shell escape hatches
    "eval", "exec", "source", ".",
    # Privilege escalation
    "sudo", "su",
    # Shell interpreters
    "bash", "sh", "zsh", "fish", "dash", "csh", "ksh",
    # Language interpreters
    "python", "python3", "perl", "ruby", "node", "deno", "bun",
    # Too flexible to parse
    "parallel",
]


@pytest.mark.parametrize("cmd", NEVER_APPROVE_COMMANDS)
def test_never_approve_rejects(cmd):
    """Every command on the never-approve list must be rejected."""
    frag = CommandFragment(cmd, [], False)
    assert step3_never_approve(frag, awk_safe_mode=False) == REJECT


@pytest.mark.parametrize("cmd", NEVER_APPROVE_COMMANDS)
def test_never_approve_rejects_with_args(cmd):
    """Never-approve commands are rejected regardless of their arguments."""
    frag = CommandFragment(cmd, ["-c", "anything", "--flag"], False)
    assert step3_never_approve(frag, awk_safe_mode=False) == REJECT


# awk variants: rejected by default, allowed through when AWK_SAFE_MODE is on
AWK_VARIANTS = ["awk", "gawk", "mawk", "nawk"]


@pytest.mark.parametrize("cmd", AWK_VARIANTS)
def test_awk_never_approve_by_default(cmd):
    """awk variants are on never-approve when AWK_SAFE_MODE is disabled."""
    frag = CommandFragment(cmd, ["{print $1}", "file"], False)
    assert step3_never_approve(frag, awk_safe_mode=False) == REJECT


@pytest.mark.parametrize("cmd", AWK_VARIANTS)
def test_awk_passes_when_safe_mode(cmd):
    """awk variants pass through step 3 when AWK_SAFE_MODE is enabled."""
    frag = CommandFragment(cmd, ["{print $1}", "file"], False)
    assert step3_never_approve(frag, awk_safe_mode=True) == NEXT


# Commands NOT on the never-approve list should pass through
SAFE_COMMANDS = ["ls", "cat", "grep", "rm", "cp", "mv", "curl", "wget"]


@pytest.mark.parametrize("cmd", SAFE_COMMANDS)
def test_non_never_approve_passes(cmd):
    """Commands not on the never-approve list should return NEXT."""
    frag = CommandFragment(cmd, [], False)
    assert step3_never_approve(frag, awk_safe_mode=False) == NEXT
