"""Dangerous-mode handlers for step 4 of the evaluation pipeline.

Each handler receives (args, config) and returns REJECT or PASS.
"""

from __future__ import annotations

from . import PASS, REJECT, CommandFragment, _Sentinel, _debug

# ---------------------------------------------------------------------------
# handle_sed
# ---------------------------------------------------------------------------


def handle_sed(args: list[str], config: object = None) -> _Sentinel:
    """Reject sed if -i or --in-place is detected."""
    for arg in args:
        if arg == "-i" or arg == "--in-place":
            _debug(1, "REJECT: sed in-place flag: %s", arg)
            return REJECT
        if arg.startswith("-i") or arg.startswith("--in-place="):
            _debug(1, "REJECT: sed in-place flag: %s", arg)
            return REJECT
        # Combined short flags containing 'i', e.g. -Ei, -ni
        if (
            arg.startswith("-")
            and not arg.startswith("--")
            and len(arg) > 1
            and "i" in arg[1:]
        ):
            _debug(1, "REJECT: sed combined flag with i: %s", arg)
            return REJECT
    return PASS


# ---------------------------------------------------------------------------
# handle_find
# ---------------------------------------------------------------------------

_FIND_DESTRUCTIVE = {"-delete", "-fprint", "-fprint0", "-fprintf"}
_FIND_EXEC_ACTIONS = {"-exec", "-execdir", "-ok", "-okdir"}


def handle_find(args: list[str], config: object = None) -> _Sentinel:
    """Reject find if destructive actions or unsafe -exec inner commands."""
    i = 0
    while i < len(args):
        arg = args[i]

        if arg in _FIND_DESTRUCTIVE:
            _debug(1, "REJECT: find destructive flag: %s", arg)
            return REJECT

        if arg in _FIND_EXEC_ACTIONS:
            # Extract inner command between action flag and terminator (; or +)
            inner_start = i + 1
            terminator_idx = None
            for j in range(inner_start, len(args)):
                if args[j] in (";", "+"):
                    terminator_idx = j
                    break

            if terminator_idx is None:  # pragma: no cover
                _debug(1, "REJECT: find %s with no terminator", arg)
                return REJECT

            # Strip {} placeholders
            inner_args = [a for a in args[inner_start:terminator_idx] if a != "{}"]

            if not inner_args:
                _debug(1, "REJECT: find %s with no command after stripping {}", arg)
                return REJECT

            # Recursively evaluate inner command through the pipeline
            from .pipeline import _evaluate_single_fragment
            if config is None:
                from .config import build_config
                config = build_config()

            inner_fragment = CommandFragment(
                executable=inner_args[0],
                args=inner_args[1:],
                has_output_redirect=False,
            )
            result = _evaluate_single_fragment(inner_fragment, config)
            if result is REJECT:
                _debug(1, "REJECT: find %s inner command rejected: %s", arg, inner_args[0])
                return REJECT

            i = terminator_idx + 1
            continue

        i += 1

    return PASS


# ---------------------------------------------------------------------------
# handle_xargs
# ---------------------------------------------------------------------------

_XARGS_FLAGS_WITH_ARGS = {
    "-d", "-a", "-I", "-L", "-n", "-P", "-s", "-E",
    "--max-args", "--max-procs", "--max-chars", "--delimiter",
    "--arg-file", "--replace", "--max-lines", "--eof",
}

_XARGS_FLAGS_NO_ARGS = {
    "-0", "-r", "-t", "-p", "-x",
    "--null", "--no-run-if-empty", "--verbose", "--interactive",
    "--exit", "--open-tty",
}


def handle_xargs(args: list[str], config: object = None) -> _Sentinel:
    """Strip xargs flags and recursively evaluate the inner command."""
    i = 0
    while i < len(args):
        arg = args[i]

        if arg in _XARGS_FLAGS_WITH_ARGS:
            i += 2  # skip flag and its argument
            continue

        if arg in _XARGS_FLAGS_NO_ARGS:
            i += 1
            continue

        # Check --flag=value syntax
        if "=" in arg:
            prefix = arg.split("=", 1)[0]
            if prefix in _XARGS_FLAGS_WITH_ARGS:
                i += 1
                continue

        # Check combined short flags (e.g. -0r, -0P)
        if arg.startswith("-") and not arg.startswith("--") and len(arg) > 2:
            # Might be combined flags — skip conservatively
            i += 1
            continue

        # Not a flag — this and everything after is the inner command
        remaining = args[i:]
        break
    else:
        remaining = []

    if not remaining:
        # No inner command → defaults to echo → PASS
        _debug(2, "xargs: no inner command, defaults to echo")
        return PASS

    # Recursively evaluate inner command
    from .pipeline import _evaluate_single_fragment
    if config is None:
        from .config import build_config
        config = build_config()

    inner_fragment = CommandFragment(
        executable=remaining[0],
        args=remaining[1:],
        has_output_redirect=False,
    )
    result = _evaluate_single_fragment(inner_fragment, config)
    if result is REJECT:
        _debug(1, "REJECT: xargs inner command rejected: %s", remaining[0])
        return REJECT

    return PASS


# ---------------------------------------------------------------------------
# handle_awk
# ---------------------------------------------------------------------------


def handle_awk(args: list[str], config: object = None) -> _Sentinel:
    """Reject awk if dangerous patterns found in program text.

    Only active when AWK_SAFE_MODE is enabled.
    """
    i = 0
    program = None

    while i < len(args):
        arg = args[i]

        if arg == "-f":
            _debug(1, "REJECT: awk -f (reads program from file)")
            return REJECT

        # Flags that consume the next argument
        if arg in ("-F", "-v"):
            i += 2
            continue

        # Other flags (e.g. -W, --posix)
        if arg.startswith("-") and arg != "-":
            i += 1
            continue

        # First non-flag arg is the program
        program = arg
        break

    if program is None:  # pragma: no cover
        return PASS

    # Scan program text for dangerous patterns
    if "system(" in program:
        _debug(1, "REJECT: awk program contains system()")
        return REJECT
    if "|" in program:
        _debug(1, "REJECT: awk program contains pipe")
        return REJECT
    if ">" in program or ">>" in program:
        _debug(1, "REJECT: awk program contains output redirect")
        return REJECT

    return PASS
