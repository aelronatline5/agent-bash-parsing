# Read-Only Bash Hook for Claude Code

---

# Part 1 — User Guide

## What this is

A Claude Code hook (Python) that auto-approves Bash commands when they are strictly read-only. Non-read-only commands fall through silently to the normal user prompt — never hard-denied.

**Zero-config works.** Install the hook and it immediately approves common read-only commands (`ls`, `cat`, `grep`, `find`, `sort`, `wc`, `jq`, `rg`, `git log`, etc.) while leaving everything else to the interactive prompt.

## Install

```bash
pip install bashlex
cp readonly_bash_hook.py ~/.claude/hooks/
cp readonly_bash_config.py ~/.claude/hooks/   # optional — only if customizing
```

Wire in `.claude/settings.json` or `~/.claude/settings.json`:

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

Alternatively, wire to `PreToolUse` instead (see Part 2 for the difference).

## What gets auto-approved

- Simple read-only commands: `ls -la`, `cat file.txt`, `grep pattern file`
- Pipelines of read-only commands: `find . -name "*.py" | head -20 | sort`
- Compound commands: `ls && cat file`, `grep foo bar || echo "not found"`
- Control flow with safe bodies: `for f in *.txt; do cat "$f"; done`
- Command/process substitution with safe inner commands: `diff <(sort a) <(sort b)`
- `sed` without `-i`: `sed 's/foo/bar/' file` (read-only transform to stdout)
- `find` without `-exec`/`-delete`: `find . -name "*.py" -type f`
- `find -exec` with safe inner commands: `find . -exec grep foo {} \;`
- `xargs` with safe inner commands: `ls | xargs wc -l`
- Git read-only subcommands: `git log`, `git diff`, `git status`, `git blame`

## What falls through to user prompt

- Write commands: `rm`, `cp`, `mv`, `mkdir`, `touch`, `chmod`
- Interpreters/shells: `python3`, `bash`, `node`, `perl`, `ruby`
- Shell escape hatches: `eval`, `exec`, `source`, `sudo`
- Output redirections: `ls > file.txt`, `echo foo >> bar`
- `sed -i` (in-place editing)
- `find -delete`, `find -fprint`
- Git write subcommands: `git push`, `git commit`, `git merge`
- `awk` (has `system()` for arbitrary execution — see `AWK_SAFE_MODE` below)
- Anything not on the whitelist

## Configuration (optional)

Configuration is a Python module. Create `readonly_bash_config.py` next to the hook script, or skip it entirely for defaults.

```python
# readonly_bash_config.py — only set what you want to change

# Add commands to the built-in whitelist
EXTRA_COMMANDS = ["kubectl", "helm", "terraform", "gcloud"]

# Remove commands from the built-in whitelist (if you disagree with a default)
REMOVE_COMMANDS = []

# Feature flags — opt into categories of safe writes
GIT_LOCAL_WRITES = False   # allow git branch, tag, stash, add, config (local only)
AWK_SAFE_MODE = False      # allow awk when program has no system()/pipes/redirects
```

That's it. Three knobs:
1. **EXTRA_COMMANDS** — add domain-specific read-only tools to the whitelist
2. **REMOVE_COMMANDS** — remove commands you consider unsafe
3. **Feature flags** — opt into safe-write categories

Everything else (never-approve list, wrapper commands, git subcommand classification, handler dispatch) is baked into the hook code. These are security invariants, not user preferences.

## Debug logging

Set `READONLY_HOOK_DEBUG` env var. Logs to `~/.claude/hooks/readonly_bash.log`.

- `1` — decisions only (approved / fell-through and why)
- `2` — fragment extraction details
- `3` — full AST dump, config loading, each evaluation step

---

# Part 2 — Implementation Spec

## Architecture overview

```
stdin (JSON) → detect event type → bail if not Bash
  → pre-strip `time` keyword → bashlex.parse()
  → recursive AST walk → flat list of CommandFragments
  → evaluate every fragment (7-step pipeline)
  → ALL pass? → emit event-appropriate approval JSON
  → ANY fail? → exit 0, no output (fall through)
```

### Two-stage design: parsing and evaluation are separate

The hook has two distinct stages with a clean boundary between them:

1. **Parsing stage** — reads the command string, invokes bashlex, walks the AST, and produces a flat list of `CommandFragment` objects. This stage knows about shell syntax (pipes, subshells, substitutions, control flow) but knows nothing about which commands are safe or dangerous.

2. **Evaluation stage** — receives `CommandFragment` objects and runs each through the 7-step pipeline. This stage knows about command safety (whitelists, never-approve lists, dangerous modes, git subcommands) but knows nothing about shell syntax.

The `CommandFragment` is the interface between them:

```python
@dataclass
class CommandFragment:
    executable: str          # resolved basename (e.g., "ls", "git")
    args: list[str]          # arguments after the executable
    has_output_redirect: bool  # True if fragment has > or >> redirect
    # Future: source_node, position info for debug logging
```

**This separation is a deliberate architectural constraint.** It means:

- **New evaluation logic** (feature flags, new safety categories, domain-specific rules) is added by extending the pipeline — new steps, new handlers, new feature flags. The parser is never touched.
- **New shell syntax support** (if bashlex improves, or for pre-parse workarounds) is handled in the parser. Evaluation logic is never touched.
- **The pipeline is an ordered, extensible sequence.** Steps can be added, reordered, or replaced independently. Each step receives a `CommandFragment` and returns one of: `APPROVE`, `REJECT` (fall through), or `NEXT` (no opinion, pass to next step).

The existing feature flags already follow this pattern: `GIT_LOCAL_WRITES` toggles behavior in step 5, `AWK_SAFE_MODE` toggles handler registration in step 4 — neither affects parsing. Future flags (`SAFE_FILE_WRITES`, `NETWORK_READS`, `ALLOW_OUTPUT_REDIRECTIONS`) will do the same: register new handlers or modify step behavior, with the parser unchanged.

## Hook event modes: PreToolUse vs PermissionRequest

The permission evaluation order is: **PreToolUse → Deny rules → Allow rules → Ask rules → PermissionRequest → canUseTool**.

The hook auto-detects the event from `hook_event_name` in stdin JSON. Core logic is identical — only output format differs:

| Aspect | PreToolUse | PermissionRequest |
|---|---|---|
| **When fires** | Before every Bash tool call | Only when permission dialog would show |
| **Approval output** | `hookSpecificOutput.permissionDecision: "allow"` | `hookSpecificOutput.decision.behavior: "allow"` |
| **Decision values** | `"allow"` / `"deny"` / `"ask"` (3-way) | `"allow"` / `"deny"` (2-way) |
| **Empty exit 0** | Falls through to permission system | Shows permission dialog |
| **Exit 2** | Blocks tool call, stderr fed to Claude | Denies permission, stderr fed to Claude |

**PermissionRequest** (recommended): fires only when declarative rules didn't resolve. Plays well with `permissions.allow`/`permissions.deny`. Less overhead.

**PreToolUse**: fires on every Bash call. Single source of truth, bypasses declarative rules for approved commands. More control, more invocations.

Do not wire to both events simultaneously (redundant).

### Output formats

**PreToolUse approval:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "allow",
    "permissionDecisionReason": "Read-only command: ls -la"
  }
}
```

**PermissionRequest approval:**
```json
{
  "hookSpecificOutput": {
    "hookEventName": "PermissionRequest",
    "decision": {
      "behavior": "allow"
    }
  }
}
```

**Fall-through (both events):** output nothing, exit 0.

### Exit codes

- **Exit 0**: success. Stdout parsed for JSON. Empty or no decision → fall through.
- **Exit 2**: blocking error. Denies tool call, stderr fed to Claude. We avoid this — all rejections fall through silently.
- **Other**: non-blocking error. stderr shown in verbose mode. Falls through.

## Parser: bashlex

`shlex.split()` tokenizes but doesn't understand shell structure. `bashlex` produces a proper AST. Install: `pip install bashlex`.

### Pre-parse workarounds

bashlex has limitations. Before calling `bashlex.parse()`:

1. **Strip `time` keyword** — bash reserved word, raises `NotImplementedError`. Strip `time` and its flags (`-p`) from the front of the command string.
2. **Consider pre-replacing `$((...))`** — arithmetic expansion raises `NotImplementedError`. Always safe, but causes unnecessary fall-throughs. Replace with a placeholder literal.
3. **Consider pre-replacing `[[ ... ]]`** — extended test raises `ParsingError`. Always safe. Replace with `true`.

Other bashlex limitations (all fall through safely):
- `case` statements → `NotImplementedError`
- `select` → `NotImplementedError`
- `coproc` → `NotImplementedError`
- C-style `for (( ))` → `ParsingError`
- `(( x++ ))` → misparsed (accidentally safe)

**Important**: catch both `bashlex.errors.ParsingError` and `NotImplementedError` (or broadly `Exception`) and fall through on either.

### AST node catalog

**Command-bearing nodes** (walker must recurse):

| Node | Structure | Recurse into |
|---|---|---|
| `CommandNode` | single command | `.parts` (contains WordNode, RedirectNode, AssignmentNode) |
| `PipelineNode` | `cmd \| cmd` | `.parts` (CommandNode + PipeNode interleaved) |
| `ListNode` | `cmd && cmd`, `cmd ; cmd` | `.parts` (CommandNode + OperatorNode interleaved) |
| `CompoundNode` | `(...)` or `{...}` | `.list` |
| `ForNode` | `for x in ...; do ...; done` | `.parts` |
| `WhileNode` | `while ...; do ...; done` | `.parts` |
| `UntilNode` | `until ...; do ...; done` | `.parts` |
| `IfNode` | `if ...; then ...; fi` | `.parts` |
| `FunctionNode` | `f() { ...; }` | `.body` (a CompoundNode) |
| `CommandsubstitutionNode` | `$(...)` | `.command` |
| `ProcesssubstitutionNode` | `<(...)` or `>(...)` | `.command` — also flag `>(...)` as output channel |

**Leaf/structural nodes** (skip):

`WordNode` (but check `.parts` for nested substitutions), `RedirectNode`, `AssignmentNode`, `ReservedwordNode`, `OperatorNode`, `PipeNode`, `ParameterNode`, `TildeNode`, `HeredocNode`.

### Default-deny walker rule

If the walker encounters any AST node kind not in an explicit set of known kinds, it MUST force fall-through. This protects against omissions and future bashlex additions.

### Recursive walking details

- `CommandNode`: iterate `.parts`. For each `WordNode`, check `.parts` for nested substitution nodes.
- `CompoundNode`: walk `.list`.
- `ForNode`, `WhileNode`, `UntilNode`, `IfNode`: recurse into `.parts`.
- `FunctionNode`: recurse into `.body`. Non-read-only commands in body → fall-through.
- `CommandsubstitutionNode`, `ProcesssubstitutionNode`: walk `.command`.

The walker produces a flat list of `CommandFragment` objects, each with: executable name, args list, and output-redirection flag.

## The 7-step evaluation pipeline

Every `CommandFragment` is evaluated through these steps in order. ALL fragments must pass for the command to be approved.

### Step 1 — REJECT (structural): output redirections

If fragment has `>` or `>>` redirect → fall through. `>&` for fd duplication (e.g., `2>&1`) is NOT a file write — allowed. Input redirects (`<`, `<<`, `<<<`) and pipes are fine. Output process substitution `>(cmd)` is flagged by the walker as an output channel → fall through.

### Step 2 — NORMALIZE: resolve and unwrap

1. **Resolve basename**: `/usr/bin/ls` → `ls`.
2. **Unwrap wrapper commands** — iteratively strip these (code constants, not configurable):
   - `env`: skip `VAR=val` tokens and flags (`-i`, `-u NAME`, `-S`). `--` terminates flags.
   - `nice`: skip flags (`-n 10`). `--` terminates flags.
   - `time`: skip flags (`-p`). `--` terminates flags.
   - `command`: if followed by `-v`/`-V` → approve immediately (lookup, not execution). `-p` stripped, continue unwrapping. `--` terminates flags.
   - `nohup`: no flags, next token is the command.
3. After stripping, the next token is the real executable. Loop back to resolve basename and check for another wrapper.

### Step 3 — REJECT (unconditional): never-approve gate

Hard-coded list (code constant, not configurable):

```python
NEVER_APPROVE = {
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
# awk/gawk/mawk/nawk added here dynamically when AWK_SAFE_MODE is disabled
```

Rationale: these commands can bypass the safety model entirely. They are not merely "write commands" — they are interpreters and escape hatches. Destructive commands like `rm`, `cp`, `mv` are safely handled by simply not being on the whitelist (fall through at step 7).

### Step 4 — REJECT (conditional): dangerous-modes handlers

Commands that are on the whitelist but have modes that write. Dispatched via a handler registry:

```python
DANGEROUS_MODE_HANDLERS = {
    "sed": handle_sed,
    "find": handle_find,
    "xargs": handle_xargs,
    "awk": handle_awk,      # registered only when AWK_SAFE_MODE enabled
    "gawk": handle_awk,
    "mawk": handle_awk,
    "nawk": handle_awk,
}
```

If a handler returns `REJECT` → fall through. If it returns `PASS` → continue to step 6 (whitelist check). If no handler exists for the command → skip to step 5.

#### `handle_sed`

Reject if any arg is `-i`, starts with `-i`, is `--in-place`, starts with `--in-place=`, or is a combined short flag containing `i` (like `-ni`, `-Ei`). Otherwise → PASS.

#### `handle_find`

Scan args for:
- **Destructive actions** (`-delete`, `-fprint`, `-fprint0`, `-fprintf`): → REJECT.
- **Exec actions** (`-exec`, `-execdir`, `-ok`, `-okdir`): extract tokens between the action flag and its terminator (`;` or `+`). Strip `{}` placeholders. Feed the remaining tokens (command + args) through the **same 7-step pipeline** recursively. Multiple exec blocks are evaluated independently. ALL must pass.
- No dangerous flags → PASS.

#### `handle_xargs`

Strip known flags to find the inner command:
- **Flags with args** (consume next token): `-d`, `-a`, `-I`, `-L`, `-n`, `-P`, `-s`, `-E`, `--max-args`, `--max-procs`, `--max-chars`, `--delimiter`, `--arg-file`, `--replace`, `--max-lines`, `--eof`. Long `=` syntax (`--max-args=10`) is a single token.
- **Flags without args** (skip): `-0`, `-r`, `-t`, `-p`, `-x`, `--null`, `--no-run-if-empty`, `--verbose`, `--interactive`, `--exit`, `--open-tty`.

After stripping, remaining tokens are the inner command + args. Feed through the **same 7-step pipeline**. If no inner command remains → defaults to `echo` → PASS.

#### `handle_awk` (only when `AWK_SAFE_MODE` enabled)

Scan the awk program string (first non-flag arg, or arg after `-f`) for:
- `system(` → REJECT
- `|` in print/pipe context (`print ... |`, `... | getline`) → REJECT
- `>` or `>>` (awk's file output operators) → REJECT
- `-f script.awk` (reads program from file, static analysis impossible) → REJECT
- None of the above → PASS (purely read-only filtering/transforming to stdout)

This is best-effort textual scan, not a full awk parser. On any doubt → REJECT.

### Step 5 — APPROVE (domain): git subcommand evaluation

If command is `git`: extract the subcommand (first non-flag arg after skipping git's global flags).

Git global flags that consume an argument (skip both flag and value): `-C`, `-c`, `--git-dir`, `--work-tree`, `--namespace`. Flags without arguments (skip): `--no-pager`, `--bare`, `--no-replace-objects`. If no subcommand found → fall through.

Classification (code constants):

```python
GIT_READONLY = {
    "blame", "diff", "log", "ls-files", "ls-tree",
    "rev-parse", "show", "show-ref", "status",
}

GIT_LOCAL_WRITES = {
    "branch", "tag", "remote", "stash", "add",
    "config",  # with arg-level guard: reject --global and --system
}

# Everything else (push, pull, fetch, commit, merge, rebase, reset,
# checkout, switch, restore, rm, clean, cherry-pick, revert, am, apply)
# always falls through.
```

- Subcommand in `GIT_READONLY` → APPROVE.
- Subcommand in `GIT_LOCAL_WRITES` and `GIT_LOCAL_WRITES` flag enabled → APPROVE (with arg guard for `config`).
- Otherwise → fall through.

`git` must NOT appear on the general whitelist — its approval is handled entirely here.

### Step 6 — APPROVE (general): whitelist check

If executable basename is in the effective whitelist → APPROVE.

The effective whitelist = `DEFAULT_COMMANDS + config.EXTRA_COMMANDS - config.REMOVE_COMMANDS`.

```python
DEFAULT_COMMANDS = {
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
```

### Step 7 — REJECT (default): fall through

Not in whitelist → fall through to user prompt.

## Config-as-code module

The hook imports `readonly_bash_config` (Python module next to the hook script). If the module doesn't exist → all defaults apply. If it exists but has missing attributes → defaults for those attributes.

```python
# In the hook:
try:
    import readonly_bash_config as cfg
except ImportError:
    cfg = None

def get_config(attr, default):
    return getattr(cfg, attr, default) if cfg else default

extra = set(get_config("EXTRA_COMMANDS", []))
remove = set(get_config("REMOVE_COMMANDS", []))
effective_whitelist = (DEFAULT_COMMANDS | extra) - remove

git_local_writes = get_config("GIT_LOCAL_WRITES", False)
awk_safe_mode = get_config("AWK_SAFE_MODE", False)
```

### What is configurable vs hard-coded

| Aspect | User configurable? | Where |
|---|---|---|
| Whitelist additions/removals | Yes | `EXTRA_COMMANDS`, `REMOVE_COMMANDS` |
| Feature flags | Yes | `GIT_LOCAL_WRITES`, `AWK_SAFE_MODE` |
| Default whitelist | No | `DEFAULT_COMMANDS` in hook code |
| Never-approve list | No | `NEVER_APPROVE` in hook code |
| Wrapper commands | No | `WRAPPER_COMMANDS` in hook code |
| Git subcommand classification | No | `GIT_READONLY`, `GIT_LOCAL_WRITES` in hook code |
| Dangerous-mode handlers | No | `DANGEROUS_MODE_HANDLERS` in hook code |
| Output format (PreToolUse/PermissionRequest) | Auto-detected | from `hook_event_name` in stdin |

## Commands intentionally excluded from the whitelist

These fall through at step 7 (not on whitelist, not on never-approve):
- `tee` — always writes to files
- `curl`, `wget` — network access
- `cp`, `mv`, `rm`, `mkdir`, `touch`, `chmod`, `chown` — obvious writes
- `make`, `pip`, `npm`, `cargo`, `docker` — side-effecting build/install tools
- `dd` — writes via `of=` without shell redirections
- `ln` — creates links
- `install`, `patch` — modifies files
- `truncate`, `shred` — destructive
- `xdg-open`, `open` — launches external programs
- `date` — read-only without `-s`, but `date -s` sets the clock; excluded for simplicity
- `tar` — list mode is read-only, but extract/create write; excluded for simplicity

## Feature flags

All default to `False`. Opt into categories of safe, local, reversible writes.

### `GIT_LOCAL_WRITES`

When enabled, additionally approves: `branch`, `tag`, `remote`, `stash`, `add`, and `config` (with guard: reject `--global`/`--system`). These are local-only, trivially reversible writes. `push`, `pull`, `commit`, `merge`, `rebase`, etc. always fall through.

### `AWK_SAFE_MODE`

When enabled, removes `awk`/`gawk`/`mawk`/`nawk` from the never-approve list and runs them through `handle_awk` instead (step 4). Safe awk programs (no `system()`, no pipes, no redirects) are approved. Dangerous or unanalyzable programs fall through.

### Future flags (not yet implemented)

- `ALLOW_OUTPUT_REDIRECTIONS` — allow `>` and `>>` to files
- `NETWORK_READS` — allow read-only network commands (`curl -s` GET, `wget -O -`, `ping`, `dig`)
- `SAFE_FILE_WRITES` — allow `mkdir -p`, `touch` (create empty files only)

## Performance

Each invocation spawns a new Python process:
- Python cold-start: ~30-80ms
- bashlex import: ~20-50ms
- Config loading: Python import, cached by interpreter for the invocation
- Total: ~50-150ms per call, acceptable for interactive use

Optimizations if needed:
- `#!/usr/bin/env python3 -S` to skip site-packages scan
- Pre-compile to `.pyc`
- Persistent daemon (socket-based) for sub-10ms — likely over-engineering for v1

---

# Part 3 — Test Reference

## Test categories (~150 cases)

### Simple commands
- `ls -la` → approve
- `cat file.txt` → approve
- `rm file.txt` → fall through (not on whitelist)
- `python3 script.py` → fall through (never-approve)

### Pipelines
- `ls | grep foo | sort | head -5` → approve (all whitelisted)
- `ls | rm` → fall through (`rm` not whitelisted)
- `ls -la | sort > sorted.txt` → fall through (`>` on last stage)

### Compound commands
- `ls && cat file` → approve
- `grep foo bar || echo "not found"` → approve
- `ls & rm foo` → fall through (`rm`)

### Control flow
- `for f in *.txt; do cat "$f"; done` → approve
- `for f in *.txt; do rm "$f"; done` → fall through
- `while read line; do echo "$line"; done` → approve
- `if true; then rm foo; fi` → fall through
- `ls() { rm -rf /; }; ls` → fall through (function body has `rm`)
- `f() { grep foo bar; }; f` → fall through (`f` invocation not in whitelist)

### Redirections
- `grep foo 2>&1` → approve (fd duplication, not file write)
- `ls > file.txt` → fall through
- `cat < input.txt` → approve (input redirect)
- `ls >&output.txt` → fall through (file redirect via `>&`)

### Substitutions
- `echo $(ls)` → approve
- `echo $(rm -rf /)` → fall through
- `echo $(echo $(rm -rf /))` → fall through (nested)
- `diff <(sort file1) <(sort file2)` → approve (input process sub)
- `cat foo >(rm bar)` → fall through (output process sub)
- `ls > >(tee /tmp/log)` → fall through (output channel)

### Wrapper unwrapping
- `env ls` → approve
- `nice -n 10 cat file` → approve
- `nohup ls` → approve
- `command -v git` → approve (lookup)
- `command -p ls` → approve (`-p` stripped)
- `env -- rm -rf /` → fall through (`rm` extracted)
- `env nice bash -c 'anything'` → fall through (`bash` underneath)

### Path resolution
- `/usr/bin/ls` → approve
- `/usr/bin/rm file.txt` → fall through
- `./script.sh` → fall through (not in whitelist)

### sed
- `sed 's/foo/bar/' file.txt` → approve (no `-i`)
- `sed -i 's/foo/bar/' file.txt` → fall through
- `sed -Ei 's/foo/bar/' file.txt` → fall through (combined flag with `i`)
- `sed --in-place=.bak 's/foo/bar/' file.txt` → fall through

### find
- `find . -name "*.py"` → approve
- `find . -name "*.pyc" -delete` → fall through
- `find . -exec rm {} \;` → fall through (`rm` fails pipeline)
- `find . -exec grep foo {} \;` → approve (`grep` passes pipeline)
- `find . -name "*.py" -exec grep foo {} \; -exec wc -l {} \;` → approve (both pass)
- `find . -name "*.py" -exec grep foo {} \; -exec rm {} \;` → fall through (second fails)
- `find . -fprint /tmp/out.txt` → fall through
- `find . -exec {} \;` → fall through (no command after stripping `{}`)
- `find . -exec sed -i 's/x/y/' {} \;` → fall through (`sed -i` rejected by handler)

### xargs
- `ls | xargs grep foo` → approve
- `ls | xargs rm` → fall through
- `ls | xargs -I{} grep foo {}` → approve (flags stripped)
- `ls | xargs -0 -P4 wc -l` → approve
- `ls | xargs --max-args=10 wc -l` → approve (`=` syntax)
- `ls | xargs` → approve (defaults to `echo`)
- `ls | xargs -I{} sh -c 'echo {}'` → fall through (`sh` on never-approve)

### Nested special-cases (recursive evaluation)
- `find . -exec xargs grep foo {} \;` → approve
- `xargs find . -name "*.py"` → approve
- `find . -exec git log {} \;` → approve
- `xargs git push` → fall through

### awk (feature-flag dependent)
- `awk '{print $1}' file.txt` → fall through by default; approve if `AWK_SAFE_MODE`
- `awk '{system("rm -rf /")}' file` → fall through (always)
- `awk '{print > "out.txt"}' file` → fall through (always)
- `awk -f script.awk file` → fall through (always; `-f` prevents analysis)

### git
- `git log --oneline` → approve
- `git diff HEAD~3` → approve
- `git -C /tmp/repo log` → approve (global flag skipped)
- `git --no-pager diff` → approve
- `git -c core.pager=less log` → approve (global flag with arg skipped)
- `git` (no subcommand) → fall through
- `git unknown-subcommand` → fall through
- `git branch feature-x` → fall through by default; approve if `GIT_LOCAL_WRITES`
- `git config user.name "foo"` → fall through by default; approve if `GIT_LOCAL_WRITES`
- `git config --global user.name "foo"` → fall through (always; `--global` guard)
- `git config --system core.editor vim` → fall through (always; `--system` guard)
- `git push origin main` → fall through (always)
- `git commit -m "msg"` → fall through (always)

### Assignments
- `FOO=bar` (no command) → approve (pure assignment)
- `FOO=$(rm -rf /)` → fall through (substitution has `rm`)
- `env` with only VAR=val → approve

### Variable expansion and unknowable commands
- `$CMD foo` → fall through (command is a variable)
- `${MY_TOOL} --version` → fall through
- `"$(which grep)" foo bar` → fall through

### Empty/whitespace/comments
- `""` → approve (no-op)
- `"   "` → approve (no-op)
- `ls # rm -rf /` → approve (comment ignored, `ls` whitelisted)
- `# just a comment` → approve

### Multiline and heredocs
- `ls -la &&\ngrep foo bar &&\nwc -l` → approve
- `cat <<'EOF'\nhello\nEOF` → approve (if bashlex parses it)
- `python3 <<'EOF'\nprint("hi")\nEOF` → fall through (`python3` on never-approve)

### Subshells and compound
- `(ls; rm foo) | grep bar` → fall through (`rm` in subshell)
- `{ ls && cat file; }` → approve
- `ls &` → approve (backgrounded)
- `! grep foo bar` → approve (negation)

### Aliases and builtins
- Claude Code runs via `bash -c` — no interactive aliases loaded. Safe.
- `test -f file.txt` → approve
- `[ -f file.txt ]` → approve

### Parallel (always never-approve)
- `cat files.txt | parallel rm` → fall through
