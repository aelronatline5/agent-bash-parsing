"""7-step evaluation pipeline and orchestrator."""

from __future__ import annotations

import os

from . import (
    APPROVE,
    FALLTHROUGH,
    NEXT,
    REJECT,
    CommandFragment,
    GIT_LOCAL_WRITES_CMDS,
    GIT_READONLY,
    WRAPPER_COMMANDS,
    _Sentinel,
    _debug,
)
from .config import _Config, get_effective_whitelist
from .parser import parse_command

# ---------------------------------------------------------------------------
# Git global flags
# ---------------------------------------------------------------------------

_GIT_GLOBAL_FLAGS_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
_GIT_GLOBAL_FLAGS_NO_ARG = {"--no-pager", "--bare", "--no-replace-objects"}

# ---------------------------------------------------------------------------
# Step 1 — REJECT (structural): output redirections
# ---------------------------------------------------------------------------


def step1_redirections(fragment: CommandFragment, config: _Config | None = None) -> _Sentinel:
    if fragment.has_output_redirect:
        _debug(1, "REJECT: output redirect on %s", fragment.executable)
        return REJECT
    return NEXT


# ---------------------------------------------------------------------------
# Step 2 — NORMALIZE: resolve and unwrap
# ---------------------------------------------------------------------------


def _unwrap_env(args: list[str]) -> tuple[str, list[str]]:
    """Skip VAR=val tokens and env flags. Return (executable, remaining_args)."""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        if "=" in arg and not arg.startswith("-"):
            i += 1  # VAR=val, skip
            continue
        if arg in ("-i", "--ignore-environment"):
            i += 1
            continue
        if arg in ("-u", "--unset"):
            i += 2  # -u NAME
            continue
        if arg in ("-S", "--split-string"):
            i += 2  # -S string
            continue
        if arg.startswith("-"):  # pragma: no cover
            i += 1
            continue
        break
    if i >= len(args):
        return ("", [])
    return (args[i], args[i + 1 :])


def _unwrap_nice(args: list[str]) -> tuple[str, list[str]]:
    """Skip nice flags. Return (executable, remaining_args)."""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        if arg in ("-n", "--adjustment"):
            i += 2
            continue
        if arg.startswith("-n") and len(arg) > 2:  # pragma: no cover
            i += 1  # -n10
            continue
        if arg.startswith("--adjustment="):  # pragma: no cover
            i += 1
            continue
        if arg.startswith("-") and arg != "-":  # pragma: no cover
            i += 1
            continue
        break
    if i >= len(args):  # pragma: no cover
        return ("", [])
    return (args[i], args[i + 1 :])


def _unwrap_time(args: list[str]) -> tuple[str, list[str]]:
    """Skip time flags. Return (executable, remaining_args)."""
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":  # pragma: no cover
            i += 1
            break
        if arg == "-p":
            i += 1
            continue
        if arg.startswith("-"):  # pragma: no cover
            i += 1
            continue
        break
    if i >= len(args):  # pragma: no cover
        return ("", [])
    return (args[i], args[i + 1 :])


def _unwrap_command(args: list[str]) -> _Sentinel | tuple[str, list[str]]:
    """Handle ``command`` wrapper.

    Returns APPROVE for ``-v``/``-V`` (lookup, not execution),
    or (executable, remaining_args) otherwise.
    """
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("-v", "-V"):
            return APPROVE
        if arg == "-p":
            i += 1
            continue
        if arg == "--":
            i += 1
            break
        break
    if i >= len(args):  # pragma: no cover
        return ("", [])
    return (args[i], args[i + 1 :])


def step2_normalize(
    fragment: CommandFragment, config: _Config | None = None
) -> CommandFragment | _Sentinel:
    """Resolve basename and iteratively unwrap wrapper commands.

    Returns the mutated fragment on success, or APPROVE sentinel
    for special cases (e.g. ``command -v``).
    """
    # Resolve basename
    fragment.executable = os.path.basename(fragment.executable)

    # Iteratively unwrap wrappers
    while fragment.executable in WRAPPER_COMMANDS:
        wrapper = fragment.executable
        args = fragment.args

        if wrapper == "env":
            result = _unwrap_env(args)
        elif wrapper == "nice":
            result = _unwrap_nice(args)
        elif wrapper == "time":
            result = _unwrap_time(args)
        elif wrapper == "command":
            result = _unwrap_command(args)
            if result is APPROVE:
                _debug(1, "APPROVE: command -v/-V lookup")
                return APPROVE
        elif wrapper == "nohup":
            if args:
                result = (args[0], args[1:])
            else:
                return fragment  # bare nohup  # pragma: no cover
        else:
            break  # pragma: no cover

        fragment.executable, fragment.args = result

        if not fragment.executable:
            # No inner command after unwrapping (e.g. env FOO=bar)
            _debug(1, "APPROVE: bare wrapper with no inner command")
            return APPROVE

        # Resolve basename again
        fragment.executable = os.path.basename(fragment.executable)

    return fragment


# ---------------------------------------------------------------------------
# Step 3 — REJECT (unconditional): never-approve gate
# ---------------------------------------------------------------------------


def step3_never_approve(
    fragment: CommandFragment, config: _Config | None = None, **kwargs
) -> _Sentinel:
    if config is None:
        from .config import build_config
        config = build_config(**kwargs)
    if fragment.executable in config.effective_never_approve:
        _debug(1, "REJECT: never-approve: %s", fragment.executable)
        return REJECT
    return NEXT


# ---------------------------------------------------------------------------
# Step 5 — APPROVE (domain): subcommand evaluation
# ---------------------------------------------------------------------------


def _extract_git_subcommand(args: list[str]) -> tuple[str | None, list[str]]:
    """Git-specific global flag parsing.

    Returns (subcommand, remaining_args) where remaining_args are the args
    after the subcommand (used for the --global/--system guard).
    """
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in _GIT_GLOBAL_FLAGS_WITH_ARG:
            i += 2
            continue
        if arg in _GIT_GLOBAL_FLAGS_NO_ARG:
            i += 1
            continue
        if arg.startswith("-"):  # pragma: no cover
            i += 1
            continue
        return (arg, args[i + 1 :])
    return (None, [])


def _extract_subcommand_generic(args: list[str]) -> str | None:
    """Simple heuristic: skip leading ``-``-prefixed args, return first non-flag arg."""
    for arg in args:
        if not arg.startswith("-"):
            return arg
    return None


def step5_subcommands(
    fragment: CommandFragment, config: _Config | None = None, **kwargs
) -> _Sentinel:
    """Generic subcommand whitelist check.

    Handles git with specialized flag parsing and all other configured
    executables with a simple flag-skipping heuristic.
    """
    if config is None:
        from .config import build_config
        config = build_config(**kwargs)

    if fragment.executable not in config.subcommand_whitelist:
        return NEXT

    allowed = config.subcommand_whitelist[fragment.executable]

    if fragment.executable == "git":
        subcommand, remaining_args = _extract_git_subcommand(fragment.args)

        if subcommand is None:
            _debug(1, "REJECT: bare git (no subcommand)")
            return REJECT

        if subcommand in allowed:
            # Guard: reject git config --global/--system even with local writes
            if config.git_local_writes and subcommand == "config":
                for a in remaining_args:
                    if a in ("--global", "--system"):
                        _debug(1, "REJECT: git config %s", a)
                        return REJECT
            _debug(1, "APPROVE: git subcommand: %s", subcommand)
            return APPROVE

        _debug(1, "REJECT: git non-readonly: %s", subcommand)
        return REJECT

    # Non-git executable
    subcommand = _extract_subcommand_generic(fragment.args)

    if subcommand is None:
        _debug(1, "REJECT: bare %s (no subcommand)", fragment.executable)
        return REJECT

    if subcommand in allowed:
        _debug(1, "APPROVE: %s subcommand: %s", fragment.executable, subcommand)
        return APPROVE

    _debug(1, "REJECT: %s non-whitelisted subcommand: %s", fragment.executable, subcommand)
    return REJECT


# ---------------------------------------------------------------------------
# Step 6 — APPROVE (general): whitelist check
# ---------------------------------------------------------------------------


def step6_whitelist(
    fragment: CommandFragment, config: _Config | set | None = None, **kwargs
) -> _Sentinel:
    # Accept a set directly as the whitelist (convenience for tests)
    if isinstance(config, (set, frozenset)):
        whitelist = config
    else:
        if config is None:  # pragma: no cover
            from .config import build_config
            config = build_config(**kwargs)
        whitelist = get_effective_whitelist(config)  # pragma: no cover
    if fragment.executable in whitelist:
        _debug(1, "APPROVE: whitelisted: %s", fragment.executable)
        return APPROVE
    return NEXT


# ---------------------------------------------------------------------------
# Step 7 — REJECT (default): fall through
# ---------------------------------------------------------------------------


def step7_default(fragment: CommandFragment, config: _Config | None = None) -> _Sentinel:
    _debug(1, "REJECT: default (not whitelisted): %s", fragment.executable)
    return REJECT


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def _evaluate_single_fragment(fragment: CommandFragment, config: _Config) -> _Sentinel:
    """Run a single fragment through the full 7-step pipeline."""
    # Step 1: redirections
    result = step1_redirections(fragment, config)
    if result is not NEXT:
        return result

    # Step 2: normalize (returns mutated fragment or APPROVE sentinel)
    result = step2_normalize(fragment, config)
    if result is APPROVE:
        return APPROVE
    # step2 returns the mutated fragment on normal path

    # Step 3: never-approve
    result = step3_never_approve(fragment, config)
    if result is not NEXT:
        return result

    # Step 4: dangerous-mode handlers (inline)
    handler = config.handlers.get(fragment.executable)
    if handler:
        from . import PASS as _PASS

        handler_result = handler(fragment.args, config)
        if handler_result is REJECT:
            return REJECT
        # PASS → continue to step 5

    # Step 5: subcommand whitelist (git + user-configured commands)
    result = step5_subcommands(fragment, config)
    if result is not NEXT:
        return result

    # Step 6: whitelist
    result = step6_whitelist(fragment, config)
    if result is not NEXT:
        return result

    # Step 7: default reject
    return step7_default(fragment, config)


def evaluate_fragments(
    fragments: list[CommandFragment], config: _Config
) -> _Sentinel:
    """Evaluate all fragments. ALL must pass → APPROVE, ANY reject → FALLTHROUGH."""
    if not fragments:
        _debug(1, "APPROVE: empty fragments")
        return APPROVE

    for fragment in fragments:
        result = _evaluate_single_fragment(fragment, config)
        if result is REJECT:
            _debug(1, "FALLTHROUGH: fragment rejected: %s", fragment.executable)
            return FALLTHROUGH

    _debug(1, "APPROVE: all fragments passed")
    return APPROVE


def evaluate_command(cmd: str, config: _Config) -> _Sentinel:
    """Convenience: parse_command + evaluate_fragments."""
    fragments = parse_command(cmd)
    return evaluate_fragments(fragments, config)
