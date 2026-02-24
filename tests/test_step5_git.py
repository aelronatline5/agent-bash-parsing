"""Unit tests for Step 5 — APPROVE (domain): git subcommand evaluation.

When the command is `git`, step 5 extracts the subcommand by skipping
global flags and classifies it:
  - GIT_READONLY subcommands → APPROVE (always)
  - GIT_LOCAL_WRITES subcommands → APPROVE only if GIT_LOCAL_WRITES flag is on
    - git config has extra guards: reject --global and --system
  - No subcommand or unknown subcommand → REJECT (fall through)
  - Non-git commands → NEXT (pass to next step)
"""

from __future__ import annotations

import pytest

from readonly_bash_hook import (
    APPROVE,
    REJECT,
    NEXT,
    CommandFragment,
    step5_subcommands,
)


# ---------------------------------------------------------------------------
# Read-only subcommands (always approved)
# ---------------------------------------------------------------------------

GIT_READONLY_SUBCMDS = [
    "blame", "diff", "log", "ls-files", "ls-tree",
    "rev-parse", "show", "show-ref", "status",
]


@pytest.mark.parametrize("subcmd", GIT_READONLY_SUBCMDS)
def test_git_readonly_approved(subcmd):
    frag = CommandFragment("git", [subcmd], False)
    assert step5_subcommands(frag, git_local_writes=False) == APPROVE


@pytest.mark.parametrize("subcmd", GIT_READONLY_SUBCMDS)
def test_git_readonly_approved_with_args(subcmd):
    frag = CommandFragment("git", [subcmd, "--oneline", "HEAD~3"], False)
    assert step5_subcommands(frag, git_local_writes=False) == APPROVE


# ---------------------------------------------------------------------------
# Global flag skipping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("args, expected_result", [
    # -C takes an argument
    (["-C", "/tmp/repo", "log"], APPROVE),
    # --no-pager is a standalone flag
    (["--no-pager", "diff"], APPROVE),
    # -c takes an argument (key=value)
    (["-c", "core.pager=less", "log"], APPROVE),
    # --git-dir takes an argument
    (["--git-dir", "/tmp/.git", "status"], APPROVE),
    # --work-tree takes an argument
    (["--work-tree", "/tmp", "diff"], APPROVE),
    # --bare is standalone
    (["--bare", "log"], APPROVE),
    # --no-replace-objects is standalone
    (["--no-replace-objects", "show"], APPROVE),
    # --namespace takes an argument
    (["--namespace", "foo", "log"], APPROVE),
    # Multiple global flags
    (["-C", "/tmp", "--no-pager", "-c", "x=y", "log", "--oneline"], APPROVE),
])
def test_git_global_flags(args, expected_result):
    frag = CommandFragment("git", args, False)
    assert step5_subcommands(frag, git_local_writes=False) == expected_result


# ---------------------------------------------------------------------------
# No subcommand → fall through
# ---------------------------------------------------------------------------

def test_git_no_subcommand():
    frag = CommandFragment("git", [], False)
    assert step5_subcommands(frag, git_local_writes=False) == REJECT


def test_git_only_global_flags():
    """All args consumed by global flags, no subcommand left."""
    frag = CommandFragment("git", ["--no-pager"], False)
    # --no-pager is a flag, no subcommand follows
    assert step5_subcommands(frag, git_local_writes=False) == REJECT


# ---------------------------------------------------------------------------
# Unknown subcommand → fall through
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("subcmd", ["unknown", "foo-bar", "anything", "cherry"])
def test_git_unknown_subcommand(subcmd):
    frag = CommandFragment("git", [subcmd], False)
    assert step5_subcommands(frag, git_local_writes=False) == REJECT


# ---------------------------------------------------------------------------
# Local-writes subcommands: feature flag dependent
# ---------------------------------------------------------------------------

GIT_LOCAL_WRITES_SUBCMDS = ["branch", "tag", "remote", "stash", "add"]


@pytest.mark.parametrize("subcmd", GIT_LOCAL_WRITES_SUBCMDS)
def test_git_local_writes_disabled(subcmd):
    """Falls through when GIT_LOCAL_WRITES=False."""
    frag = CommandFragment("git", [subcmd, "arg"], False)
    assert step5_subcommands(frag, git_local_writes=False) == REJECT


@pytest.mark.feature_git_local_writes
@pytest.mark.parametrize("subcmd", GIT_LOCAL_WRITES_SUBCMDS)
def test_git_local_writes_enabled(subcmd):
    """Approved when GIT_LOCAL_WRITES=True."""
    frag = CommandFragment("git", [subcmd, "arg"], False)
    assert step5_subcommands(frag, git_local_writes=True) == APPROVE


@pytest.mark.parametrize("args", [
    ["branch", "feature-x"],
    ["tag", "v1.0"],
    ["remote", "add", "origin", "url"],
    ["stash"],
    ["stash", "pop"],
    ["add", "."],
])
def test_git_local_writes_specific_args(args):
    """Various local-write invocations approved when flag is on."""
    frag = CommandFragment("git", args, False)
    assert step5_subcommands(frag, git_local_writes=True) == APPROVE


# ---------------------------------------------------------------------------
# git config: special guards
# ---------------------------------------------------------------------------

@pytest.mark.feature_git_local_writes
def test_git_config_local_approved():
    """git config (local) approved when GIT_LOCAL_WRITES=True."""
    frag = CommandFragment("git", ["config", "user.name", "foo"], False)
    assert step5_subcommands(frag, git_local_writes=True) == APPROVE


@pytest.mark.feature_git_local_writes
def test_git_config_global_rejected():
    """git config --global always falls through, even with GIT_LOCAL_WRITES."""
    frag = CommandFragment("git", ["config", "--global", "user.name", "foo"], False)
    assert step5_subcommands(frag, git_local_writes=True) == REJECT


@pytest.mark.feature_git_local_writes
def test_git_config_system_rejected():
    """git config --system always falls through, even with GIT_LOCAL_WRITES."""
    frag = CommandFragment("git", ["config", "--system", "core.editor", "vim"], False)
    assert step5_subcommands(frag, git_local_writes=True) == REJECT


def test_git_config_disabled_flag():
    """git config falls through when GIT_LOCAL_WRITES=False."""
    frag = CommandFragment("git", ["config", "user.name", "foo"], False)
    assert step5_subcommands(frag, git_local_writes=False) == REJECT


# ---------------------------------------------------------------------------
# Always fall-through subcommands (regardless of flags)
# ---------------------------------------------------------------------------

ALWAYS_FALLTHROUGH = [
    "push", "pull", "fetch", "commit", "merge", "rebase", "reset",
    "checkout", "switch", "restore", "rm", "clean", "cherry-pick",
    "revert", "am", "apply",
]


@pytest.mark.parametrize("subcmd", ALWAYS_FALLTHROUGH)
def test_git_always_fallthrough_default(subcmd):
    frag = CommandFragment("git", [subcmd], False)
    assert step5_subcommands(frag, git_local_writes=False) == REJECT


@pytest.mark.parametrize("subcmd", ALWAYS_FALLTHROUGH)
def test_git_always_fallthrough_with_flag(subcmd):
    """Even with GIT_LOCAL_WRITES=True, these subcommands fall through."""
    frag = CommandFragment("git", [subcmd], False)
    assert step5_subcommands(frag, git_local_writes=True) == REJECT


# ---------------------------------------------------------------------------
# Non-git commands pass through to next step
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", ["ls", "cat", "grep", "find"])
def test_non_git_passes(cmd):
    frag = CommandFragment(cmd, [], False)
    assert step5_subcommands(frag, git_local_writes=False) == NEXT


# ---------------------------------------------------------------------------
# Non-git subcommand whitelisting (generic path)
# ---------------------------------------------------------------------------

from readonly_bash_hook import build_config


def _docker_config():
    return build_config(subcommand_whitelist={"docker": ["ps", "images", "inspect"]})


def test_generic_subcommand_approved():
    frag = CommandFragment("docker", ["ps"], False)
    assert step5_subcommands(frag, _docker_config()) == APPROVE


def test_generic_subcommand_with_flags():
    """Leading flags are skipped, subcommand found."""
    frag = CommandFragment("docker", ["--debug", "ps"], False)
    assert step5_subcommands(frag, _docker_config()) == APPROVE


def test_generic_subcommand_rejected():
    """Subcommand not in allowed set → REJECT."""
    frag = CommandFragment("docker", ["rm", "foo"], False)
    assert step5_subcommands(frag, _docker_config()) == REJECT


def test_generic_bare_command_rejected():
    """Bare command with no subcommand → REJECT."""
    frag = CommandFragment("docker", [], False)
    assert step5_subcommands(frag, _docker_config()) == REJECT


def test_generic_only_flags_rejected():
    """Only flags, no subcommand → REJECT."""
    frag = CommandFragment("docker", ["--debug", "-v"], False)
    assert step5_subcommands(frag, _docker_config()) == REJECT


def test_generic_not_configured_passes():
    """Executable not in subcommand whitelist → NEXT."""
    frag = CommandFragment("helm", ["install"], False)
    assert step5_subcommands(frag, _docker_config()) == NEXT
