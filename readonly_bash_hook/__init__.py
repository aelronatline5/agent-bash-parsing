"""Read-only Bash hook for Claude Code.

Auto-approves Bash commands that are strictly read-only.
Non-read-only commands fall through silently to the normal user prompt.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Sentinels
# ---------------------------------------------------------------------------

class _Sentinel:
    """Identity-compared sentinel value."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __repr__(self) -> str:  # pragma: no cover
        return self._name

    def __bool__(self) -> bool:  # pragma: no cover
        return True


APPROVE = _Sentinel("APPROVE")
FALLTHROUGH = _Sentinel("FALLTHROUGH")
REJECT = _Sentinel("REJECT")
NEXT = _Sentinel("NEXT")
PASS = _Sentinel("PASS")


# ---------------------------------------------------------------------------
# CommandFragment dataclass
# ---------------------------------------------------------------------------

@dataclass
class CommandFragment:
    """A single command extracted from a parsed shell AST."""

    executable: str
    args: list[str] = field(default_factory=list)
    has_output_redirect: bool = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_COMMANDS: set[str] = {
    # Filesystem listing
    "ls", "tree", "stat", "file", "du", "df",
    # File reading
    "cat", "head", "tail", "less", "more", "tac",
    # Search
    "grep", "rg", "fd", "find", "locate", "strings", "ag",
    # Text processing (read-only — sed -i handled by step 4)
    "sed", "cut", "paste", "tr", "sort", "uniq", "comm", "join",
    "fmt", "column", "nl", "rev", "fold", "expand", "unexpand",
    "wc", "xargs",
    # JSON/structured data
    "jq", "yq",
    # Diffing
    "diff", "cmp",
    # Path utilities
    "readlink", "realpath", "basename", "dirname",
    # Command lookup
    "which", "type", "whereis",
    # User/system info
    "id", "whoami", "groups", "uname", "hostname", "uptime", "printenv",
    # Checksums
    "sha256sum", "sha1sum", "md5sum", "cksum", "b2sum",
    # Binary viewers
    "xxd", "hexdump", "od",
    # Builtins
    "echo", "printf", "true", "false", "test", "[", "read",
    # Process info
    "ps", "top", "htop", "lsof", "pgrep",
}

NEVER_APPROVE: set[str] = {
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
}

_AWK_VARIANTS: set[str] = {"awk", "gawk", "mawk", "nawk"}

GIT_READONLY: set[str] = {
    "blame", "diff", "log", "ls-files", "ls-tree",
    "rev-parse", "show", "show-ref", "status",
}

GIT_LOCAL_WRITES_CMDS: set[str] = {
    "branch", "tag", "remote", "stash", "add", "config",
}

WRAPPER_COMMANDS: set[str] = {"env", "nice", "time", "command", "nohup"}


# ---------------------------------------------------------------------------
# Debug logging
# ---------------------------------------------------------------------------

_DEBUG_LEVEL = int(os.environ.get("READONLY_HOOK_DEBUG", "0"))


def _setup_logger() -> logging.Logger:  # pragma: no cover
    logger = logging.getLogger("readonly_bash_hook")
    if _DEBUG_LEVEL > 0:
        log_dir = os.path.expanduser("~/.claude/hooks")
        os.makedirs(log_dir, exist_ok=True)
        handler = logging.FileHandler(os.path.join(log_dir, "readonly_bash.log"))
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    else:
        logger.addHandler(logging.NullHandler())
    return logger


_log = _setup_logger()


def _debug(level: int, msg: str, *args: object) -> None:
    if _DEBUG_LEVEL >= level:
        _log.debug(msg, *args)  # pragma: no cover


# ---------------------------------------------------------------------------
# Re-exports from submodules
# ---------------------------------------------------------------------------

from .config import build_config, get_effective_whitelist, load_config_from_settings  # noqa: E402
from .parser import parse_command, preparse_strip_time, preparse_command  # noqa: E402
from .handlers import handle_sed, handle_find, handle_xargs, handle_awk  # noqa: E402
from .pipeline import (  # noqa: E402
    step1_redirections,
    step2_normalize,
    step3_never_approve,
    step5_git,
    step6_whitelist,
    step7_default,
    evaluate_fragments,
    evaluate_command,
)
from .output import (  # noqa: E402
    format_pretooluse_approval,
    format_permission_request_approval,
    detect_event_type,
    process_hook_input,
)

__all__ = [
    # Sentinels
    "APPROVE", "FALLTHROUGH", "REJECT", "NEXT", "PASS",
    # Dataclass
    "CommandFragment",
    # Constants
    "DEFAULT_COMMANDS", "NEVER_APPROVE", "GIT_READONLY", "GIT_LOCAL_WRITES_CMDS",
    "WRAPPER_COMMANDS",
    # Config
    "build_config", "get_effective_whitelist", "load_config_from_settings",
    # Parser
    "parse_command", "preparse_strip_time", "preparse_command",
    # Handlers
    "handle_sed", "handle_find", "handle_xargs", "handle_awk",
    # Pipeline
    "step1_redirections", "step2_normalize", "step3_never_approve",
    "step5_git", "step6_whitelist", "step7_default",
    "evaluate_fragments", "evaluate_command",
    # Output
    "format_pretooluse_approval", "format_permission_request_approval",
    "detect_event_type", "process_hook_input",
]
