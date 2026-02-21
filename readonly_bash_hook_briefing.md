# Briefing: Read-Only Bash PermissionRequest Hook for Claude Code

## Goal

Build a Claude Code `PermissionRequest` hook (Python) that auto-approves Bash tool uses when the entire command is strictly read-only. Non-read-only commands fall through silently to the normal user prompt — never hard-denied.

## Why PermissionRequest (not PreToolUse)

The permission evaluation order is: PreToolUse → Deny rules → Allow rules → Ask rules → PermissionRequest → canUseTool. PermissionRequest fires only when the declarative rules in settings.json didn't already resolve the decision. This means you can still use `permissions.deny` for hard blocks like `rm -rf *`, and this hook handles the nuanced compound-command analysis for everything that falls through. If we used PreToolUse instead, we'd be duplicating work the declarative rules already handle.

## Parser: bashlex (not shlex)

`shlex.split()` is not sufficient — it tokenizes but doesn't understand shell structure. `bashlex` (pip package) produces a proper AST. Install with `pip install bashlex`.

### AST node types we care about

**Command-bearing nodes** (walker must recurse into these):
- `CommandNode` — a single simple command. `.parts` contains `WordNode`, `RedirectNode`, `AssignmentNode` children.
- `PipelineNode` — `cmd | cmd`. `.parts` contains `CommandNode` and `PipeNode` interleaved. May also contain `ReservedwordNode` for `!` negation (skip it).
- `ListNode` — `cmd && cmd`, `cmd || cmd`, `cmd ; cmd`, `cmd &`. `.parts` contains `CommandNode` and `OperatorNode` interleaved.
- `CompoundNode` — subshell `(...)` or brace group `{...}`. Has `.list` attribute containing inner nodes.
- `ForNode` — `for x in ...; do ...; done`. Has `.parts` containing `ReservedwordNode`, `WordNode`, `ListNode`, and `CommandNode` children. Walker must recurse into `.parts`.
- `WhileNode` — `while ...; do ...; done`. Has `.parts` containing condition and body commands. Walker must recurse into `.parts`.
- `UntilNode` — `until ...; do ...; done`. Same structure as `WhileNode`.
- `IfNode` — `if ...; then ...; fi`. Has `.parts` containing condition and body commands. Walker must recurse into `.parts`.
- `FunctionNode` — `f() { ...; }`. Has `.name`, `.body` (a `CompoundNode`), and `.parts`. Walker MUST recurse into `.body` to evaluate all commands in the function definition. A function body containing non-read-only commands causes fall-through.
- `CommandsubstitutionNode` — `$(...)`. Has `.command` attribute.
- `ProcesssubstitutionNode` — `<(...)` or `>(...)`. Has `.command` attribute. Note: `>(...)` is an output channel (see evaluation logic step 1).

**Leaf/structural nodes** (no recursion needed):
- `WordNode` — a token. May itself contain nested `CommandsubstitutionNode`/`ProcesssubstitutionNode` in `.parts`.
- `RedirectNode` — has `.type` (`>`, `>>`, `<`, `<<`, `<<<`, `>&`). Output redirects (`>`, `>>`) are the ones that matter for write detection. `>&` for fd duplication (e.g., `2>&1`) is NOT a file-writing redirect and is allowed.
- `AssignmentNode` — `VAR=val` preceding a command.
- `ReservedwordNode` — keywords like `for`, `do`, `done`, `if`, `then`, `fi`, `{`, `}`. Structural markers, skip.
- `OperatorNode` — `;`, `&&`, `||`, `&`. Structural markers, skip.
- `PipeNode` — `|` between pipeline stages. Structural marker, skip.
- `ParameterNode` — `$var` or `${var}` inside words. Leaf, no action.
- `TildeNode` — `~` expansion. Leaf, no action.
- `HeredocNode` — heredoc content. Leaf, no action.

### bashlex limitations discovered

- `time` is a bash reserved word that bashlex raises `NotImplementedError` on (not `ParsingError`). Pre-strip `time` (and its flags like `-p`) from the front of the command string before feeding to bashlex. Note: step 2 strips the bash keyword `time` before parsing (because bashlex cannot handle it). Step 3 handles `time`/`/usr/bin/time` appearing as a wrapper command within already-parsed fragments. Both are needed.
- `case` statements raise `NotImplementedError` (not `ParsingError`): `case $x in a) rm foo;; esac`.
- `select` raises `NotImplementedError`: `select x in a b c; do echo $x; done`.
- `coproc` raises `NotImplementedError`: `coproc cat`.
- `$((arithmetic))` expansion raises `NotImplementedError`. Arithmetic is always safe but any command using it will fall through. Very common in bash — consider pre-replacing `$((` expressions with a placeholder before parsing.
- `[[ ... ]]` extended test raises `ParsingError`. Security-safe (test expressions don't execute commands) but causes unnecessary fall-throughs for common constructs. Consider pre-replacing `[[ ... ]]` with `true` before parsing.
- C-style `for (( i=0; i<10; i++ ))` raises `ParsingError`.
- `(( x++ ))` arithmetic command is misparsed as nested subshells — accidentally safe (falls through) but unreliable.
- Heredocs work but can be tricky with expansion. On any parse failure, fall through to user prompt.
- Brace expansion in unusual positions may fail. Same approach — fall through.

**Important**: bashlex raises two different exception types: `bashlex.errors.ParsingError` for unparseable input and `NotImplementedError` for recognized-but-unimplemented constructs. The hook MUST catch both (or catch `Exception` broadly) and fall through on either.

## Architecture

The hook reads JSON from stdin (Claude Code hook protocol), extracts `tool_name` and `tool_input.command`, then:

1. **Bail early** if `tool_name != "Bash"`.
2. **Pre-strip `time`** keyword (bashlex can't parse it).
3. **Parse** with `bashlex.parse()`. On parse error → fall through.
4. **Recursively walk** the AST to extract a flat list of `CommandFragment` objects, each with: executable name, args list, and whether it has output redirections.
5. **Evaluate every fragment** against the config. ALL must pass for approval.
6. **Output** the PermissionRequest approval JSON on stdout + exit 0 if approved. Output nothing + exit 0 if any fragment fails (falls through to user prompt).

## Fragment evaluation logic (in order)

1. **Output redirection check** — if fragment has `>` or `>>` redirect, reject. `>&` for fd duplication (e.g., `2>&1`) is NOT a file-writing redirect and is allowed. Input redirects (`<`, `<<`, `<<<`) and pipes are fine. Additionally, output process substitution `>(cmd)` is an output channel — the walker must flag it as such when encountered (in addition to recursing into the inner command).
2. **Resolve basename** — `/usr/bin/ls` → `ls`.
3. **Unwrap wrapper commands** — iteratively strip `env`, `nice`, `time`, `command`, `nohup`:
   - `env`: skip `VAR=val` tokens and flags (`-i`, `-u NAME`, `-S`). `--` terminates flag processing; the next token after `--` is always the real executable.
   - `nice`: skip flags (`-n 10`). `--` terminates flag processing.
   - `time`: skip flags (`-p`). `--` terminates flag processing.
   - `command`: if followed by `-v`/`-V`, approve immediately (it's a lookup). `-p` uses default PATH but still executes — strip it and continue unwrapping. `--` terminates flag processing.
   - `nohup`: takes no flags, next token is the command.
   - After stripping, the next token is the real executable.
4. **Special-case `sed`** — reject if any arg is `-i`, starts with `-i`, is `--in-place`, starts with `--in-place=` (handles `--in-place=SUFFIX`), or is a combined short flag containing `i` (like `-ni`, `-Ei`). If no `-i` flag found, continue to step 10 (whitelist) for approval. Steps 4-6 are pre-filters: they only reject dangerous modes of otherwise-whitelisted commands. Approval comes from the whitelist at step 10.
5. **Special-case `find`** — scan the argument list for:
   - **Destructive actions** (`-delete`, `-fprint`, `-fprint0`, `-fprintf`): reject on sight, no inner command to inspect.
   - **Exec actions** (`-exec`, `-execdir`, `-ok`, `-okdir`): extract the tokens between the action flag and its terminator (`;` or `+`). Strip placeholder tokens (`{}`) — these are path arguments, not commands. Feed the remaining tokens (command name + its args) through the same fragment evaluation logic used everywhere else. The inner command must pass the whitelist, never-approve list, and all other checks.
   - Multiple exec blocks can be chained in a single `find` invocation (`find . -name "*.py" -exec grep foo {} \; -exec wc -l {} \;`). Each one is extracted and evaluated independently. ALL must pass.
   - `find` with none of these flags (just predicates like `-name`, `-type`, `-mtime`, etc.) is purely read-only and approved as-is.
6. **Special-case `xargs`** — strip known flags to find the inner command:
   - **Flags with args** (consume next token): `-d`, `-a`, `-I`, `-L`, `-n`, `-P`, `-s`, `-E`, `--max-args`, `--max-procs`, `--max-chars`, `--delimiter`, `--arg-file`, `--replace`, `--max-lines`, `--eof`. Long flags also support `=` syntax (e.g., `--max-args=10`) — treat as a single token, do not consume the next token.
   - **Flags without args** (skip): `-0`, `-r`, `-t`, `-p`, `-x`, `--null`, `--no-run-if-empty`, `--verbose`, `--interactive`, `--exit`, `--open-tty`.
   - After stripping flags, the remaining tokens are the inner command + its args. Feed through the same fragment evaluation logic. The inner command must pass all checks.
   - If no inner command remains after flag stripping, `xargs` defaults to `echo` — approve.
7. **Special-case `awk`** (only if `awk_safe_mode` feature flag is enabled) — scan the awk program string for `system(`, `|` in print/pipe context, `>`, `>>`. Reject if found; approve if clean. If `awk_safe_mode` is disabled, awk falls through to step 8 (never-approve).
8. **Never-approve list** — hard-reject interpreters, escape hatches, and commands with built-in code execution: `eval`, `exec`, `source`, `.`, `sudo`, `su`, `bash`, `sh`, `zsh`, `fish`, `dash`, `csh`, `ksh`, `python`, `python3`, `perl`, `ruby`, `node`, `deno`, `bun`, `parallel`, `awk`, `gawk`, `mawk`, `nawk` (awk only if `awk_safe_mode` is disabled).
9. **Special-case `git`** — extract the subcommand (first non-flag arg after skipping git's global flags). Git global flags that consume an argument must have their values skipped: `-C <path>`, `-c <key=value>`, `--git-dir=<path>`, `--work-tree=<path>`, `--namespace=<name>`. Flags without arguments (`--no-pager`, `--bare`, `--no-replace-objects`) are simply skipped. If no subcommand is found (bare `git` or only flags), fall through. Evaluation depends on the `git_local_writes` feature flag:
   - **Always approved** (strictly read-only): `blame`, `diff`, `log`, `ls-files`, `ls-tree`, `rev-parse`, `show`, `show-ref`, `status`.
   - **Approved only if `git_local_writes` is enabled**: `branch`, `tag`, `remote`, `stash`, `add`, and `config` (with arg-level guard: reject if `--global` or `--system` present).
   - **Always fall through**: `push`, `pull`, `fetch`, `commit`, `merge`, `rebase`, `reset`, `checkout`, `switch`, `restore`, `rm`, `clean`, `cherry-pick`, `revert`, `am`, `apply`, and anything not explicitly listed.
10. **General whitelist check** — approve if executable basename is in `allowed_commands`.
11. **Default** — not in whitelist → fall through.

## Recursive walking details

Command substitution (`$(rm -rf /)` inside `echo $(rm -rf /)`) and process substitution (`<(rm foo)` inside `cat <(rm foo)`) MUST be checked. These appear as nested nodes inside `WordNode.parts`. The walker must:

- When processing a `CommandNode`, iterate its `.parts`. For each `WordNode` child, check if it has `.parts` containing substitution nodes, and recursively extract those.
- When encountering `CompoundNode` (subshells/brace groups), walk `.list`.
- When encountering `ForNode`, `WhileNode`, `UntilNode`, or `IfNode`, recurse into `.parts` to find and evaluate all contained `CommandNode`, `ListNode`, `PipelineNode`, and `CompoundNode` children.
- When encountering `FunctionNode`, recurse into `.body` (a `CompoundNode`) and evaluate all commands within. A function body containing non-read-only commands causes fall-through.
- When encountering `CommandsubstitutionNode` or `ProcesssubstitutionNode`, walk `.command`.
- Skip leaf/structural nodes: `ReservedwordNode`, `OperatorNode`, `PipeNode`, `ParameterNode`, `TildeNode`, `HeredocNode`.

### Default-deny rule (defense in depth)

If the walker encounters any AST node kind it does not explicitly handle, it MUST force a fall-through rather than silently skipping it. This protects against both current omissions and future bashlex additions. The walker should maintain an explicit set of known node kinds and reject on anything outside that set.

## Config file (JSON)

Store separately from the hook script for easy editing. Contains:

- `allowed_commands`: flat list of command basenames (ls, cat, grep, find, sort, wc, head, tail, jq, rg, fd, tree, stat, du, df, ps, etc.). Note: `sed`, `find`, `xargs` should be in this list — their special-case steps (4-6) only reject dangerous modes; approval comes from the whitelist at step 10. `git` must NOT appear in this list — its approval is handled entirely by step 9; adding it here would bypass subcommand checks.
- `git_readonly_subcommands`: list of strictly read-only git subcommands (see git section)
- `wrapper_commands`: list of prefix commands to unwrap (env, nice, time, command, nohup)
- `never_approve`: list of commands that should never be auto-approved (interpreters, escape hatches). Can be hardcoded or configurable. See step 7.
- `feature_flags`: object with boolean flags for opt-in safe-write categories (all default to false)
- `version`: schema version number (start at `1`). The hook checks this on load and warns on unknown versions.

### Example config

```json
{
  "version": 1,
  "allowed_commands": [
    "ls", "cat", "grep", "sed", "find", "xargs", "sort", "wc", "head", "tail",
    "jq", "rg", "fd", "tree", "stat", "du", "df", "ps", "file", "diff", "cmp",
    "readlink", "realpath", "basename", "dirname", "which", "type", "whereis",
    "id", "whoami", "groups", "uname", "hostname", "uptime", "printenv",
    "cut", "paste", "tr", "uniq", "comm", "join", "fmt", "column", "nl", "tac", "rev",
    "sha256sum", "sha1sum", "md5sum", "cksum", "xxd", "hexdump", "od",
    "echo", "printf", "true", "false", "test", "[", "read", "strings", "locate"
  ],
  "git_readonly_subcommands": [
    "blame", "diff", "log", "ls-files", "ls-tree", "rev-parse", "show", "show-ref", "status"
  ],
  "wrapper_commands": ["env", "nice", "time", "command", "nohup"],
  "never_approve": [
    "eval", "exec", "source", ".", "sudo", "su",
    "bash", "sh", "zsh", "fish", "dash", "csh", "ksh",
    "python", "python3", "perl", "ruby", "node", "deno", "bun",
    "parallel", "awk", "gawk", "mawk", "nawk"
  ],
  "feature_flags": {
    "git_local_writes": false,
    "awk_safe_mode": false
  }
}
```

### Error handling

- **Missing config file**: log a warning to stderr, fall through on all commands (exit 0, no output). The hook should never crash.
- **Malformed JSON**: treat as missing config — fall through on everything.
- **Unknown fields**: ignore silently (forward compatibility).
- **Missing required fields**: use safe defaults — empty `allowed_commands` means nothing is approved, empty `never_approve` means nothing is hard-blocked. Missing `feature_flags` means all flags are `false`.
- **Type errors** (e.g., `allowed_commands` is a string instead of list): treat as missing field, use default.

## Feature flags

The hook is strictly read-only by default. Feature flags opt into categories of low-risk, local, easily-reversible write operations. Each flag is a boolean in `config.feature_flags`, defaulting to `false` when absent.

### `git_local_writes`

When **disabled** (default): git evaluation only approves subcommands from `git_readonly_subcommands` — a strictly read-only list: `blame`, `diff`, `log`, `ls-files`, `ls-tree`, `rev-parse`, `show`, `show-ref`, `status`.

When **enabled**: additionally approves these subcommands, which perform local-only writes that are trivially reversible:
- `branch` — creates/deletes local branches. Never touches the remote.
- `config` — writes local git config. Excludes `--global` and `--system` (those fall through).
- `tag` — creates/deletes local tags. Never touches the remote.
- `remote` — adds/removes remote definitions (local config only, no network).
- `stash` — saves/restores working directory state. Always reversible.
- `add` — stages files. Reversible with `git restore --staged`.

Even with this flag enabled, the following always fall through: `push`, `pull`, `fetch`, `commit`, `merge`, `rebase`, `reset`, `checkout`, `switch`, `restore`, `rm`, `clean`, `cherry-pick`, `revert`, `am`, `apply`.

### `git_local_writes` arg-level guards

Some subcommands enabled by `git_local_writes` need arg-level checks to prevent non-local effects:
- `git config`: reject if args contain `--global` or `--system` (these write outside the repo). `--local` and no scope flag are fine.

### `awk_safe_mode`

When **disabled** (default): `awk`/`gawk`/`mawk`/`nawk` are on the never-approve list (step 7). All `awk` invocations fall through to user prompt.

When **enabled**: `awk` is removed from the never-approve list and instead gets a special-case evaluation (inserted between steps 6 and 7). The awk program argument (typically the first non-flag arg, or the arg after `-f`) is scanned for dangerous constructs:
- **Reject** if the program contains: `system(`, `|` in a print/pipe context (`print ... |`, `... | getline`), `>` or `>>` (awk's built-in file output operators).
- **Approve** if none of the above are found — the awk invocation is purely read-only (filtering/transforming to stdout).
- This is a best-effort textual scan of the awk program string, not a full awk parser. On any doubt (e.g., obfuscated code, variables in redirection targets), fall through.

Note: `awk -f script.awk` reads the program from a file, making static analysis impossible. This always falls through when `awk_safe_mode` is enabled.

### Future feature flags (not yet implemented)

Placeholders for future safe-write categories, to be designed as needed:
- `allow_output_redirections` — allow `>` and `>>` to files (currently always rejected).
- `network_reads` — allow read-only network commands like `curl -s` (GET only), `wget -O -`, `ping`, `dig`, `nslookup`, `host`.
- `safe_file_writes` — allow low-risk file operations like `mkdir -p`, `touch` (create empty files only).

### Commands on the whitelist with special handling

- `sed` — on the whitelist, but `-i`/`--in-place` is special-cased to reject (step 4).
- `find` — on the whitelist, but `-exec`/`-delete`/`-fprint` are special-cased (step 5).
- `xargs` — on the whitelist, but inner command is extracted and evaluated (step 6). `xargs` with no inner command (defaults to `echo`) is approved.

### Commands on the never-approve list (with rationale)

These are handled at step 8 and always cause fall-through regardless of context:
- `eval`, `exec`, `source`, `.` — shell escape hatches, can run anything.
- `sudo`, `su` — privilege escalation.
- `bash`, `sh`, `zsh`, `fish`, `dash`, `csh`, `ksh` — shell interpreters.
- `python`, `python3`, `perl`, `ruby`, `node`, `deno`, `bun` — language interpreters.
- `awk`, `gawk`, `mawk`, `nawk` — despite common use as text filters, `awk` has `system()` for arbitrary shell execution, can pipe to commands via `|`, and can write to files via its built-in `>` operator. Functionally an interpreter.
- `parallel` — GNU parallel accepts shell snippet strings as commands, too flexible to parse reliably.

Note: destructive commands like `rm`, `cp`, `mv` are NOT on the never-approve list. They are safely handled by simply not being on the whitelist (fall through at step 11). The never-approve list is specifically for commands that could bypass the safety model entirely (interpreters, privilege escalation, eval).

### Commands intentionally excluded from the whitelist

These are not on the whitelist and not on the never-approve list. They fall through at step 11:
- `tee` — always writes to files by design.
- `curl`, `wget` — network access, not read-only.
- `cp`, `mv`, `rm`, `mkdir`, `touch`, `chmod`, `chown` — obvious writes.
- `make`, `pip`, `npm`, `cargo`, `docker` — side-effecting build/install tools.
- `dd` — can overwrite devices/files without shell-level redirections (`of=`).
- `ln` — creates symlinks/hard links.
- `install` — copies files with permissions.
- `patch` — modifies files.
- `truncate`, `shred` — destructive file operations.
- `xdg-open`, `open` — launches external programs.
- `date` — read-only without `-s`, but `date -s` sets the clock. Excluded for simplicity.
- `tar` — list mode (`tar tf`) is read-only, but extract/create modes write. Excluded for simplicity.

### Additional commands recommended for the whitelist

Beyond the basics (`ls`, `cat`, `grep`, `find`, `sort`, `wc`, `head`, `tail`, `jq`, `rg`, `fd`, `tree`, `stat`, `du`, `df`, `ps`), consider:
- **Text processing**: `sed`, `cut`, `paste`, `tr`, `uniq`, `comm`, `join`, `fmt`, `column`, `nl`, `tac`, `rev`, `fold`, `expand`, `unexpand`
- **File info**: `file`, `readlink`, `realpath`, `basename`, `dirname`
- **Diffing**: `diff`, `cmp`
- **Command lookup**: `which`, `type`, `whereis`
- **User/system info**: `id`, `whoami`, `groups`, `uname`, `hostname`, `uptime`, `printenv`
- **Checksums**: `sha256sum`, `sha1sum`, `md5sum`, `cksum`, `b2sum`
- **Binary viewers**: `xxd`, `hexdump`, `od`
- **Builtins**: `echo`, `printf`, `true`, `false`, `test`, `[`, `read` (stdin-only)
- **Search**: `locate`, `strings`

## Hook protocol summary

- **Stdin**: JSON with `session_id`, `cwd`, `permission_mode`, `hook_event_name`, `tool_name`, `tool_input`, `transcript_path`.
- **Stdout on exit 0**: parsed as JSON. The PermissionRequest event requires the `hookSpecificOutput` wrapper:
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
  If stdout is empty or has no `hookSpecificOutput` → fall through to user prompt.
- **Exit 0 with no decision**: fall through to user prompt.
- **Exit 2**: denies the permission and shows stderr to Claude. We deliberately avoid this — all rejections fall through silently via empty stdout + exit 0.
- **Stderr**: ignored by Claude Code (unless exit 2), safe for debug logging.

Note: this hook will only fire for Bash commands that were not already resolved by declarative allow/deny rules in `settings.json`. If you have `permissions.allow` entries for Bash commands, those will be auto-approved before this hook runs.

## Wiring

In `.claude/settings.json` or `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PermissionRequest": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 /absolute/path/to/readonly_bash_hook.py"
          }
        ]
      }
    ]
  }
}
```

## Debug logging

Set `READONLY_HOOK_DEBUG` env var. Logs to `~/.claude/hooks/readonly_bash.log`. Never logs to stdout (that's the protocol channel).
- `READONLY_HOOK_DEBUG=1` — decisions only (approved/fell-through and why).
- `READONLY_HOOK_DEBUG=2` — fragment extraction details.
- `READONLY_HOOK_DEBUG=3` — full AST dump, config loading, each evaluation step.

## Performance considerations

This hook fires on every Bash tool call that reaches the PermissionRequest stage. Each invocation spawns a new Python process.

- **Python cold-start**: ~30-80ms for interpreter startup.
- **bashlex import**: additional ~20-50ms.
- **Config loading**: JSON read from disk on every invocation.
- **bashlex.parse()**: negligible for typical commands (<1ms).
- **Total per-invocation**: ~50-150ms, acceptable for interactive use.

For agentic sessions with many sequential Bash calls, this latency can add up. Potential optimizations if needed:
- Use `#!/usr/bin/env python3 -S` to skip site-packages scan.
- Pre-compile to `.pyc` via `py_compile` for faster loading.
- For sub-10ms response, consider a persistent daemon (socket-based) or a compiled language (Go/Rust). This is likely over-engineering for the initial version.

## Test suite

~150 test cases covering: simple commands, pipelines, compound commands (&&, ||, ;), control flow (for, while, until, if), function definitions, git read-only vs write subcommands (parametrized with `git_local_writes` on/off), output vs input vs fd-duplication redirections, dangerous commands, shell interpreters, eval/exec, command substitution (including nested), process substitution (both `<()` and `>()`), subshells with mixed safe/unsafe, wrapper command unwrapping (including `--` and `nohup`), absolute paths, pure assignments (including with command substitution in values), sed -i variants, find -exec/-delete, xargs with inner command extraction, awk_safe_mode on/off, variable expansion in command position, empty/whitespace commands, comments, multiline commands, backgrounded commands, negation, and nested special-cases (find-in-xargs, git-in-find-exec).

## Edge cases to keep in mind

### Assignments and wrappers
- `FOO=bar` with no command → pure assignment, harmless, approve. But `FOO=$(rm -rf /)` must still check the command substitution inside the value — the `rm` causes fall-through.
- `env` with only VAR=val and no trailing command → harmless, approve.
- `command -v git` → lookup, not execution, approve.
- `command -p ls` → `-p` stripped, `ls` is whitelisted, approve.
- `env -- rm -rf /` → `--` terminates env flags, `rm` extracted, falls through.
- `nohup ls` → `nohup` unwrapped, `ls` whitelisted, approve.
- `env nice bash -c 'anything'` → wrappers unwrapped, `bash` found underneath, falls through.

### Redirections
- `ls -la | sort > sorted.txt` → the `>` on the last pipeline stage causes fall-through.
- `grep foo 2>&1` → fd duplication, NOT a file write, approve.
- `ls >&output.txt` → file redirection via `>&`, falls through.

### Path resolution
- `/usr/bin/rm file.txt` → basename resolves to `rm`, falls through.
- `./script.sh` → basename resolves to `script.sh`, not in whitelist, falls through.
- `~/bin/my-tool` → not in whitelist, falls through.

### sed
- `sed -Ei 's/foo/bar/' file.txt` → combined flag `-Ei` contains `i`, falls through.
- `sed --in-place=.bak 's/foo/bar/' file.txt` → `--in-place=` prefix detected, falls through.
- `sed 's/foo/bar/' file.txt` → no `-i`, reaches whitelist, approve.

### find
- `find . -name "*.py"` → no exec/delete actions, approve.
- `find . -name "*.pyc" -delete` → `-delete` detected, falls through.
- `find . -exec rm {} \;` → inner command `rm` not in whitelist, falls through.
- `find . -exec grep foo {} \;` → inner command `grep` is whitelisted, approve.
- `find . -name "*.py" -exec grep foo {} \; -exec wc -l {} \;` → both inner commands whitelisted, approve.
- `find . -name "*.py" -exec grep foo {} \; -exec rm {} \;` → second inner command `rm` fails, falls through.
- `find . -execdir chmod 755 {} \;` → inner command `chmod` not in whitelist, falls through.
- `find . -fprint /tmp/out.txt` → `-fprint` detected, falls through.
- `find . -exec {} \;` → after stripping `{}`, no command remains, falls through.
- `find . -exec sed -i 's/x/y/' {} \;` → inner command `sed` with `-i` rejected by step 4, falls through.

### xargs
- `ls | xargs grep foo` → inner command `grep` is whitelisted, approve.
- `ls | xargs rm` → inner command `rm` not in whitelist, falls through.
- `ls | xargs -I{} grep foo {}` → flags stripped, inner command `grep` whitelisted, approve.
- `ls | xargs -0 -P4 wc -l` → flags stripped, inner command `wc` whitelisted, approve.
- `ls | xargs --max-args=10 wc -l` → `--max-args=10` treated as single flag token, `wc` extracted, approve.
- `ls | xargs` → no inner command, defaults to `echo`, approve.
- `ls | xargs -I{} sh -c 'echo {}'` → inner command `sh` on never-approve list, falls through.
- `cat files.txt | parallel rm` → `parallel` on never-approve list, falls through.

### Nested special-cases (recursive evaluation)
- `find . -exec xargs grep foo {} \;` → inner command `xargs` with inner command `grep`, both evaluated recursively, approve.
- `xargs find . -name "*.py"` → inner command `find` with no exec/delete, approve.
- `find . -exec git log {} \;` → inner command `git` with read-only subcommand, approve.
- `xargs git push` → inner command `git` with `push` (always falls through), falls through.

### awk (feature-flag dependent)
- `awk '{print $1}' file.txt` → falls through by default; approved if `awk_safe_mode` enabled (no dangerous constructs).
- `awk '{system("rm -rf /")}' file` → falls through by default; also falls through with `awk_safe_mode` (`system(` detected).
- `awk '{print > "out.txt"}' file` → falls through by default; also falls through with `awk_safe_mode` (`>` detected).
- `awk -f script.awk file` → always falls through (even with `awk_safe_mode`, `-f` makes static analysis impossible).

### git
- `git log --oneline` → always read-only, approve.
- `git diff HEAD~3` → always read-only, approve.
- `git -C /tmp/repo log` → global flag `-C` with arg skipped, subcommand `log` extracted, approve.
- `git --no-pager diff` → global flag `--no-pager` skipped, subcommand `diff` extracted, approve.
- `git -c core.pager=less log` → global flag `-c` with arg skipped, subcommand `log` extracted, approve.
- `git` (no subcommand) → falls through.
- `git unknown-subcommand` → not in any list, falls through.
- `git branch feature-x` → falls through by default; approved if `git_local_writes` enabled.
- `git config user.name "foo"` → falls through by default; approved if `git_local_writes` enabled.
- `git config --global user.name "foo"` → always falls through (even with `git_local_writes`, `--global` is rejected).
- `git config --system core.editor vim` → always falls through (even with `git_local_writes`, `--system` is rejected).
- `git tag v1.0` → falls through by default; approved if `git_local_writes` enabled.
- `git remote -v` → falls through by default; approved if `git_local_writes` enabled.
- `git remote add origin url` → falls through by default; approved if `git_local_writes` enabled.
- `git stash` → falls through by default; approved if `git_local_writes` enabled.
- `git add .` → falls through by default; approved if `git_local_writes` enabled.
- `git push origin main` → always falls through regardless of feature flags.
- `git commit -m "msg"` → always falls through regardless of feature flags.

### Control flow and functions
- `for f in *.txt; do cat "$f"; done` → walker recurses into `ForNode`, `cat` whitelisted, approve.
- `for f in *.txt; do rm "$f"; done` → walker recurses into `ForNode`, `rm` not whitelisted, falls through.
- `while read line; do echo "$line"; done` → walker recurses into `WhileNode`, `read` and `echo` whitelisted, approve.
- `if true; then rm foo; fi` → walker recurses into `IfNode`, `rm` causes fall-through.
- `ls() { rm -rf /; }; ls` → walker recurses into `FunctionNode` body, `rm` causes fall-through. (Without function body inspection, `ls` invocation would falsely match whitelist.)
- `f() { grep foo bar; }; f` → function body contains whitelisted `grep`, but `f` invocation is not in whitelist — falls through. (Function definitions with safe bodies do NOT add the function name to the whitelist.)

### Substitutions
- `echo $(rm -rf /)` → the `rm` fragment extracted from the substitution causes fall-through.
- `echo $(echo $(rm -rf /))` → nested substitution, `rm` extracted at inner level, falls through.
- `diff <(sort file1) <(sort file2)` → input process substitutions, inner `sort` whitelisted, approve.
- `cat foo >(rm bar)` → output process substitution, inner `rm` not whitelisted, falls through.
- `ls > >(tee /tmp/log)` → `>(...)` is output channel, falls through.

### Subshells and compound commands
- `(ls; rm foo) | grep bar` → the `rm` inside the subshell causes fall-through.
- `{ ls && cat file; }` → brace group, both whitelisted, approve.
- `ls &` → backgrounded, `ls` whitelisted, approve.
- `ls & rm foo` → `rm` not whitelisted, falls through.
- `! grep foo bar` → negation, `grep` whitelisted, approve.

### Variable expansion and unknowable commands
- `$CMD foo` → command name is a variable, cannot determine at static analysis time, falls through.
- `${MY_TOOL} --version` → same, falls through.
- `"$(which grep)" foo bar` → outer command is a substitution result, falls through.

### Empty/whitespace/comments
- `""` (empty command) → no fragments, approve (no-op).
- `"   "` (whitespace only) → no fragments, approve (no-op).
- `ls # rm -rf /` → comment after `#` ignored by parser, `ls` whitelisted, approve.
- `# just a comment` → no command, approve.

### Multiline and heredocs
- `ls -la &&\ngrep foo bar &&\nwc -l` → multiline with `&&`, all whitelisted, approve.
- `cat <<'EOF'\nhello\nEOF` → heredoc input to `cat`, approve (if bashlex parses it; fall through on parse error).
- `python3 <<'EOF'\nprint("hi")\nEOF` → `python3` on never-approve list, falls through.

### Aliases and builtins
- The hook evaluates the literal command string, not alias-expanded forms. This is safe because Claude Code runs commands via `bash -c` which does not load interactive aliases from `.bashrc`.
- `test -f file.txt` → `test` whitelisted, approve.
- `[ -f file.txt ]` → `[` whitelisted, approve.
