"""Unit tests for Step 6 — APPROVE (general): whitelist check.

Step 6 approves any command whose basename is in the effective whitelist.
Effective whitelist = DEFAULT_COMMANDS + EXTRA_COMMANDS - REMOVE_COMMANDS.

IMPORTANT: `git` must NOT be on this whitelist — git is handled by step 5.
"""

from __future__ import annotations

import pytest

from readonly_bash_hook import (
    APPROVE,
    NEXT,
    CommandFragment,
    step6_whitelist,
    DEFAULT_COMMANDS,
)


# ---------------------------------------------------------------------------
# Whitelisted commands (representative from each category)
# ---------------------------------------------------------------------------

# Filesystem listing
@pytest.mark.parametrize("cmd", ["ls", "tree", "stat", "file", "du", "df"])
def test_filesystem_listing(cmd):
    frag = CommandFragment(cmd, [], False)
    assert step6_whitelist(frag, DEFAULT_COMMANDS) == APPROVE


# File reading
@pytest.mark.parametrize("cmd", ["cat", "head", "tail", "less", "more", "tac"])
def test_file_reading(cmd):
    frag = CommandFragment(cmd, [], False)
    assert step6_whitelist(frag, DEFAULT_COMMANDS) == APPROVE


# Search
@pytest.mark.parametrize("cmd", ["grep", "rg", "fd", "find", "locate", "strings", "ag"])
def test_search(cmd):
    frag = CommandFragment(cmd, [], False)
    assert step6_whitelist(frag, DEFAULT_COMMANDS) == APPROVE


# Text processing
@pytest.mark.parametrize("cmd", [
    "sed", "cut", "paste", "tr", "sort", "uniq", "comm", "join",
    "fmt", "column", "nl", "rev", "fold", "expand", "unexpand",
    "wc", "xargs",
])
def test_text_processing(cmd):
    frag = CommandFragment(cmd, [], False)
    assert step6_whitelist(frag, DEFAULT_COMMANDS) == APPROVE


# JSON/structured data
@pytest.mark.parametrize("cmd", ["jq", "yq"])
def test_structured_data(cmd):
    frag = CommandFragment(cmd, [], False)
    assert step6_whitelist(frag, DEFAULT_COMMANDS) == APPROVE


# Diffing
@pytest.mark.parametrize("cmd", ["diff", "cmp"])
def test_diffing(cmd):
    frag = CommandFragment(cmd, [], False)
    assert step6_whitelist(frag, DEFAULT_COMMANDS) == APPROVE


# Path utilities
@pytest.mark.parametrize("cmd", ["readlink", "realpath", "basename", "dirname"])
def test_path_utilities(cmd):
    frag = CommandFragment(cmd, [], False)
    assert step6_whitelist(frag, DEFAULT_COMMANDS) == APPROVE


# Command lookup
@pytest.mark.parametrize("cmd", ["which", "type", "whereis"])
def test_command_lookup(cmd):
    frag = CommandFragment(cmd, [], False)
    assert step6_whitelist(frag, DEFAULT_COMMANDS) == APPROVE


# User/system info
@pytest.mark.parametrize("cmd", [
    "id", "whoami", "groups", "uname", "hostname", "uptime", "printenv",
])
def test_system_info(cmd):
    frag = CommandFragment(cmd, [], False)
    assert step6_whitelist(frag, DEFAULT_COMMANDS) == APPROVE


# Checksums
@pytest.mark.parametrize("cmd", ["sha256sum", "sha1sum", "md5sum", "cksum", "b2sum"])
def test_checksums(cmd):
    frag = CommandFragment(cmd, [], False)
    assert step6_whitelist(frag, DEFAULT_COMMANDS) == APPROVE


# Binary viewers
@pytest.mark.parametrize("cmd", ["xxd", "hexdump", "od"])
def test_binary_viewers(cmd):
    frag = CommandFragment(cmd, [], False)
    assert step6_whitelist(frag, DEFAULT_COMMANDS) == APPROVE


# Builtins
@pytest.mark.parametrize("cmd", ["echo", "printf", "true", "false", "test", "[", "read"])
def test_builtins(cmd):
    frag = CommandFragment(cmd, [], False)
    assert step6_whitelist(frag, DEFAULT_COMMANDS) == APPROVE


# Process info
@pytest.mark.parametrize("cmd", ["ps", "top", "htop", "lsof", "pgrep"])
def test_process_info(cmd):
    frag = CommandFragment(cmd, [], False)
    assert step6_whitelist(frag, DEFAULT_COMMANDS) == APPROVE


# ---------------------------------------------------------------------------
# git must NOT be on the whitelist
# ---------------------------------------------------------------------------

def test_git_not_in_whitelist():
    """git is handled by step 5, not the general whitelist."""
    assert "git" not in DEFAULT_COMMANDS
    frag = CommandFragment("git", ["log"], False)
    assert step6_whitelist(frag, DEFAULT_COMMANDS) == NEXT


# ---------------------------------------------------------------------------
# Commands NOT on the whitelist
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "rm", "cp", "mv", "mkdir", "touch", "chmod", "chown",
    "curl", "wget", "tee", "make", "pip", "npm", "docker",
    "dd", "ln", "tar", "date", "install", "patch",
    "truncate", "shred", "xdg-open", "open",
])
def test_not_whitelisted(cmd):
    frag = CommandFragment(cmd, [], False)
    assert step6_whitelist(frag, DEFAULT_COMMANDS) == NEXT


# ---------------------------------------------------------------------------
# EXTRA_COMMANDS and REMOVE_COMMANDS
# ---------------------------------------------------------------------------

def test_extra_commands_added():
    effective = DEFAULT_COMMANDS | {"kubectl", "helm"}
    frag = CommandFragment("kubectl", [], False)
    assert step6_whitelist(frag, effective) == APPROVE


def test_remove_commands_removed():
    effective = DEFAULT_COMMANDS - {"less"}
    frag = CommandFragment("less", [], False)
    assert step6_whitelist(frag, effective) == NEXT


def test_extra_and_remove_overlap():
    """If a command is in both EXTRA and REMOVE, REMOVE wins."""
    effective = (DEFAULT_COMMANDS | {"x"}) - {"x"}
    frag = CommandFragment("x", [], False)
    assert step6_whitelist(frag, effective) == NEXT
