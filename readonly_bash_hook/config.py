"""Configuration system for the read-only Bash hook."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from . import (
    DEFAULT_COMMANDS,
    NEVER_APPROVE,
    PASS,
    REJECT,
    _AWK_VARIANTS,
    _Sentinel,
    _debug,
)


@dataclass
class _Config:
    """Internal configuration object."""

    extra_commands: list[str] = field(default_factory=list)
    remove_commands: list[str] = field(default_factory=list)
    git_local_writes: bool = False
    awk_safe_mode: bool = False
    effective_never_approve: frozenset[str] = field(default_factory=frozenset)
    handlers: dict[str, Callable[..., _Sentinel]] = field(default_factory=dict)


def build_config(
    extra_commands: list[str] | None = None,
    remove_commands: list[str] | None = None,
    git_local_writes: bool = False,
    awk_safe_mode: bool = False,
) -> _Config:
    """Build a config from explicit parameters.

    Used for programmatic/test use. JSON config (settings.json) is resolved
    to these same parameters by load_config_from_settings().
    """
    extra_commands = extra_commands or []
    remove_commands = remove_commands or []

    # Build effective never-approve set
    never = set(NEVER_APPROVE)
    if not awk_safe_mode:
        never |= _AWK_VARIANTS
    effective_never_approve = frozenset(never)

    # Build handler registry — lazy imports to avoid circular deps
    from .handlers import handle_find, handle_sed, handle_xargs

    handlers: dict[str, Callable[..., _Sentinel]] = {
        "sed": handle_sed,
        "find": handle_find,
        "xargs": handle_xargs,
    }

    if awk_safe_mode:
        from .handlers import handle_awk

        for name in _AWK_VARIANTS:
            handlers[name] = handle_awk

    return _Config(
        extra_commands=extra_commands,
        remove_commands=remove_commands,
        git_local_writes=git_local_writes,
        awk_safe_mode=awk_safe_mode,
        effective_never_approve=effective_never_approve,
        handlers=handlers,
    )


def get_effective_whitelist(config: _Config) -> set[str]:
    """Compute the effective whitelist: DEFAULT_COMMANDS + extras - removals.

    When awk_safe_mode is enabled, awk variants are added to the whitelist
    so that the handler's PASS flows through to step 6 approval.
    """
    wl = (DEFAULT_COMMANDS | set(config.extra_commands)) - set(config.remove_commands)
    if config.awk_safe_mode:
        wl |= _AWK_VARIANTS
    return wl


def _find_and_read_settings_json() -> dict[str, Any]:  # pragma: no cover
    """Locate and read settings.json, returning its contents as a dict."""
    candidates = [
        os.path.join(".claude", "settings.json"),
        os.path.expanduser(os.path.join("~", ".claude", "settings.json")),
    ]
    for path in candidates:
        try:
            with open(path) as f:
                data = json.load(f)
                _debug(3, "Loaded settings from %s", path)
                return data
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
    _debug(3, "No settings.json found, using defaults")
    return {}


def load_config_from_settings() -> _Config:  # pragma: no cover
    """Load config from settings.json, falling back to defaults."""
    settings = _find_and_read_settings_json()
    hook_cfg = settings.get("readonlyBashHook", {})
    return build_config(
        extra_commands=hook_cfg.get("extraCommands", []),
        remove_commands=hook_cfg.get("removeCommands", []),
        git_local_writes=hook_cfg.get("features", {}).get("gitLocalWrites", False),
        awk_safe_mode=hook_cfg.get("features", {}).get("awkSafeMode", False),
    )
