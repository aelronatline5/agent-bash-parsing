"""Pre-parse workarounds, bashlex AST walker, and parse_command."""

from __future__ import annotations

import re

import bashlex

from . import CommandFragment, _debug

# ---------------------------------------------------------------------------
# Known AST node kinds (default-deny: anything not here forces fall-through)
# ---------------------------------------------------------------------------

_KNOWN_NODE_KINDS = {
    # Command-bearing (walker recurses into these)
    "command", "pipeline", "list", "compound",
    "for", "while", "until", "if", "function",
    "commandsubstitution", "processsubstitution",
    # Leaf / structural (walker skips these)
    "word", "assignment", "redirect", "reservedword",
    "operator", "pipe", "parameter", "tilde", "heredoc",
}

# Sentinel fragment returned on parse failure — executable will never match
# any whitelist, never-approve, or git check, so it naturally falls through.
_PARSE_FAILURE = [CommandFragment(executable="\x00__PARSE_FAILURE__")]


# ---------------------------------------------------------------------------
# Pre-parse functions
# ---------------------------------------------------------------------------

def preparse_strip_time(cmd: str) -> str:
    """Strip the ``time`` keyword and its flags from the front of *cmd*."""
    stripped = cmd.lstrip()
    if not stripped.startswith("time"):
        return cmd

    # Make sure it's the keyword, not e.g. "timeout"
    rest = stripped[4:]
    if rest and rest[0] not in (" ", "\t", "\n", ";", "|", "&"):  # pragma: no cover
        return cmd

    rest = rest.lstrip()

    # Consume flags: -p, --
    while rest:
        if rest.startswith("-p") and (len(rest) == 2 or rest[2] in " \t\n"):
            rest = rest[2:].lstrip()
        elif rest.startswith("--") and (len(rest) == 2 or rest[2] in " \t\n"):  # pragma: no cover
            rest = rest[2:].lstrip()
            break
        else:
            break

    return rest


def preparse_command(cmd: str) -> str:
    """Apply all pre-parse workarounds before calling bashlex."""
    result = preparse_strip_time(cmd)
    # Replace arithmetic expansion $((...)) with literal 0
    result = re.sub(r"\$\(\(.*?\)\)", "0", result)
    # Replace [[ ... ]] with true
    result = re.sub(r"\[\[.*?\]\]", "true", result)
    return result


# ---------------------------------------------------------------------------
# AST walker
# ---------------------------------------------------------------------------

def _is_output_redirect(node: object) -> bool:
    """Return True if *node* (a RedirectNode) represents an output file write."""
    rtype = getattr(node, "type", "")
    if rtype in (">", ">>"):
        return True
    if rtype == ">&":
        # fd duplication: output is an int → allowed  (e.g. 2>&1)
        # file redirect: output is a word/str → rejected (e.g. >&file)
        output = getattr(node, "output", None)
        if isinstance(output, int):
            return False
        return True
    return False


def _walk_ast(nodes: list, source: str) -> list[CommandFragment]:
    """Walk bashlex AST *nodes* and return a flat list of CommandFragments."""
    fragments: list[CommandFragment] = []
    force_fallthrough = False

    def walk(node: object) -> None:
        nonlocal force_fallthrough

        kind = getattr(node, "kind", None)

        if kind not in _KNOWN_NODE_KINDS:  # pragma: no cover
            _debug(3, "Unknown node kind: %s — forcing fall-through", kind)
            force_fallthrough = True
            return

        # --- Command-bearing nodes ---
        if kind == "command":
            _handle_command_node(node, source, fragments, walk)

        elif kind == "pipeline":
            for part in node.parts:
                pk = getattr(part, "kind", None)
                if pk and pk != "pipe":
                    walk(part)

        elif kind == "list":
            for part in node.parts:
                pk = getattr(part, "kind", None)
                if pk and pk != "operator":
                    walk(part)

        elif kind == "compound":
            for item in node.list:
                walk(item)

        elif kind in ("for", "while", "until", "if"):
            for part in node.parts:
                pk = getattr(part, "kind", None)
                if pk and pk != "reservedword":
                    walk(part)

        elif kind == "function":
            walk(node.body)

        elif kind == "commandsubstitution":
            walk(node.command)

        elif kind == "processsubstitution":
            # Determine direction from source text
            pos = getattr(node, "pos", (0, 0))
            if pos[0] < len(source) and source[pos[0]] == ">":
                _debug(2, "Output process substitution detected — flagging output channel")
                # Add a marker fragment with has_output_redirect=True
                # so step 1 rejects it, while still walking inner command
                fragments.append(CommandFragment(
                    executable="__output_procsub__",
                    args=[],
                    has_output_redirect=True,
                ))
            walk(node.command)

        # Leaf / structural nodes: word, assignment, redirect, reservedword,
        # operator, pipe, parameter, tilde, heredoc — skip silently.

    for node in nodes:
        walk(node)

    if force_fallthrough:  # pragma: no cover
        return list(_PARSE_FAILURE)

    return fragments


def _handle_command_node(
    node: object,
    source: str,
    fragments: list[CommandFragment],
    walk_fn,
) -> None:
    """Process a CommandNode, appending a CommandFragment to *fragments*."""
    words: list[str] = []
    has_output_redirect = False
    has_command_word = False

    for part in node.parts:
        pk = getattr(part, "kind", None)

        if pk == "word":
            words.append(part.word)
            has_command_word = True
            # Check for nested substitutions inside the word
            if hasattr(part, "parts") and part.parts:
                for subpart in part.parts:
                    spk = getattr(subpart, "kind", None)
                    if spk in ("commandsubstitution", "processsubstitution"):
                        walk_fn(subpart)

        elif pk == "redirect":
            if _is_output_redirect(part):
                has_output_redirect = True
            # Check redirect target for nested substitutions
            output_node = getattr(part, "output", None)
            if hasattr(output_node, "parts") and output_node.parts:
                for subpart in output_node.parts:
                    spk = getattr(subpart, "kind", None)
                    if spk in ("commandsubstitution", "processsubstitution"):
                        walk_fn(subpart)

        elif pk == "assignment":
            # Check assignment value for nested substitutions
            if hasattr(part, "parts") and part.parts:
                for subpart in part.parts:
                    spk = getattr(subpart, "kind", None)
                    if spk in ("commandsubstitution", "processsubstitution"):
                        walk_fn(subpart)

        elif pk == "reservedword":
            pass  # e.g. "!" for negation  # pragma: no cover

        else:
            # Unknown part kind inside a command — recurse
            walk_fn(part)  # pragma: no cover

    # Build fragment
    if has_command_word and words:
        fragments.append(CommandFragment(
            executable=words[0],
            args=words[1:],
            has_output_redirect=has_output_redirect,
        ))
    # else: pure assignment (no command word) — no fragment, naturally approves


# ---------------------------------------------------------------------------
# parse_command
# ---------------------------------------------------------------------------

def parse_command(cmd: str) -> list[CommandFragment]:
    """Parse a shell command string into a flat list of CommandFragments.

    Returns ``[]`` on empty/comment-only input.
    Returns a sentinel list (forcing fall-through) on parse failure.
    """
    cleaned = preparse_command(cmd)

    if not cleaned or not cleaned.strip():
        return []

    # Comment-only lines: strip and check if what remains is empty or a comment
    stripped = cleaned.strip()
    if stripped.startswith("#"):
        return []

    try:
        parts = bashlex.parse(cleaned)
    except Exception:
        _debug(2, "bashlex parse failure for: %s", cmd)
        return list(_PARSE_FAILURE)

    _debug(3, "bashlex AST: %s", parts)
    return _walk_ast(parts, cleaned)
