# Briefing: Read-Only Bash PermissionRequest Hook for Claude Code

## Goal

Build a Claude Code `PermissionRequest` hook (Python) that auto-approves Bash tool uses when the entire command is strictly read-only. Non-read-only commands fall through silently to the normal user prompt — never hard-denied.

## Why PermissionRequest (not PreToolUse)

The permission evaluation order is: PreToolUse → Deny rules → Allow rules → Ask rules → PermissionRequest → canUseTool. PermissionRequest fires only when the declarative rules in settings.json didn't already resolve the decision. This means you can still use `permissions.deny` for hard blocks like `rm -rf *`, and this hook handles the nuanced compound-command analysis for everything that falls through. If we used PreToolUse instead, we'd be duplicating work the declarative rules already handle.

## Parser: bashlex (not shlex)

`shlex.split()` is not sufficient — it tokenizes but doesn't understand shell structure. `bashlex` (pip package) produces a proper AST. Install with `pip install bashlex`.

### AST node types we care about

- `CommandNode` — a single simple command. `.parts` contains `WordNode`, `RedirectNode`, `AssignmentNode` children.
- `PipelineNode` — `cmd | cmd`. `.parts` contains `CommandNode` and `PipeNode` interleaved.
- `ListNode` — `cmd && cmd`, `cmd || cmd`, `cmd ; cmd`, `cmd &`. `.parts` contains `CommandNode` and `OperatorNode` interleaved.
- `CompoundNode` — subshell `(...)` or brace group `{...}`. Has `.list` attribute containing inner nodes.
- `CommandsubstitutionNode` — `$(...)`. Has `.command` attribute.
- `ProcesssubstitutionNode` — `<(...)` or `>(...)`. Has `.command` attribute.
- `WordNode` — a token. May itself contain nested `CommandsubstitutionNode`/`ProcesssubstitutionNode` in `.parts`.
- `RedirectNode` — has `.type` (`>`, `>>`, `<`, `<<`, `<<<`, `>&`). Output redirects (`>`, `>>`) are the ones that matter for write detection.
- `AssignmentNode` — `VAR=val` preceding a command.

### bashlex limitations discovered

- `time` is a bash reserved word that bashlex cannot parse. Pre-strip `time` (and its flags like `-p`) from the front of the command string before feeding to bashlex. `time` itself is always safe.
- Heredocs work but can be tricky with expansion. On any parse failure, fall through to user prompt.
- Brace expansion in unusual positions may fail. Same approach — fall through.

## Architecture

The hook reads JSON from stdin (Claude Code hook protocol), extracts `tool_name` and `tool_input.command`, then:

1. **Bail early** if `tool_name != "Bash"`.
2. **Pre-strip `time`** keyword (bashlex can't parse it).
3. **Parse** with `bashlex.parse()`. On parse error → fall through.
4. **Recursively walk** the AST to extract a flat list of `CommandFragment` objects, each with: executable name, args list, and whether it has output redirections.
5. **Evaluate every fragment** against the config. ALL must pass for approval.
6. **Output** `{"decision": "approve", "reason": "..."}` on stdout + exit 0 if approved. Output nothing + exit 0 if any fragment fails (falls through to user prompt).

## Fragment evaluation logic (in order)

1. **Output redirection check** — if fragment has `>` or `>>` redirect, reject. Input redirects (`<`, `<<`, `<<<`) and pipes are fine.
2. **Resolve basename** — `/usr/bin/ls` → `ls`.
3. **Unwrap wrapper commands** — iteratively strip `env`, `nice`, `time`, `command`:
   - `env`: skip `VAR=val` tokens and flags (`-i`, `-u NAME`, `-S`).
   - `nice`: skip flags (`-n 10`).
   - `time`: skip flags (`-p`).
   - `command`: if followed by `-v`/`-V`, approve immediately (it's a lookup). Otherwise unwrap.
   - After stripping, the next token is the real executable.
4. **Special-case `sed`** — reject if any arg is `-i`, starts with `-i`, is `--in-place`, or is a combined short flag containing `i` (like `-ni`, `-Ei`). sed without `-i` is read-only (prints to stdout).
5. **Special-case `find`** — scan the argument list for:
   - **Destructive actions** (`-delete`, `-fprint`, `-fprint0`, `-fprintf`): reject on sight, no inner command to inspect.
   - **Exec actions** (`-exec`, `-execdir`, `-ok`, `-okdir`): extract the tokens between the action flag and its terminator (`;` or `+`). Strip placeholder tokens (`{}`) — these are path arguments, not commands. Feed the remaining tokens (command name + its args) through the same fragment evaluation logic used everywhere else. The inner command must pass the whitelist, never-approve list, and all other checks.
   - Multiple exec blocks can be chained in a single `find` invocation (`find . -name "*.py" -exec grep foo {} \; -exec wc -l {} \;`). Each one is extracted and evaluated independently. ALL must pass.
   - `find` with none of these flags (just predicates like `-name`, `-type`, `-mtime`, etc.) is purely read-only and approved as-is.
6. **Special-case `xargs`** — strip known flags to find the inner command:
   - **Flags with args** (consume next token): `-d`, `-a`, `-I`, `-L`, `-n`, `-P`, `-s`, `-E`, `--max-args`, `--max-procs`, `--max-chars`, `--delimiter`, `--arg-file`, `--replace`, `--max-lines`, `--eof`.
   - **Flags without args** (skip): `-0`, `-r`, `-t`, `-p`, `-x`, `--null`, `--no-run-if-empty`, `--verbose`, `--interactive`, `--exit`, `--open-tty`.
   - After stripping flags, the remaining tokens are the inner command + its args. Feed through the same fragment evaluation logic. The inner command must pass all checks.
   - If no inner command remains after flag stripping, `xargs` defaults to `echo` — approve.
7. **Never-approve list** — hard-reject interpreters and escape hatches: `eval`, `exec`, `source`, `.`, `sudo`, `su`, `bash`, `sh`, `zsh`, `fish`, `dash`, `csh`, `ksh`, `python`, `python3`, `perl`, `ruby`, `node`, `deno`, `bun`, `parallel`.
8. **Special-case `git`** — extract the subcommand (first non-flag arg). Evaluation depends on the `git_local_writes` feature flag:
   - **Always approved** (strictly read-only): `blame`, `diff`, `log`, `ls-files`, `ls-tree`, `rev-parse`, `show`, `show-ref`, `status`.
   - **Approved only if `git_local_writes` is enabled**: `branch`, `tag`, `remote`, `stash`, `add`, and `config` (with arg-level guard: reject if `--global` or `--system` present).
   - **Always fall through**: `push`, `pull`, `fetch`, `commit`, `merge`, `rebase`, `reset`, `checkout`, `switch`, `restore`, `rm`, `clean`, `cherry-pick`, `revert`, `am`, `apply`, and anything not explicitly listed.
9. **General whitelist check** — approve if executable basename is in `allowed_commands`.
10. **Default** — not in whitelist → fall through.

## Recursive walking details

Command substitution (`$(rm -rf /)` inside `echo $(rm -rf /)`) and process substitution (`<(rm foo)` inside `cat <(rm foo)`) MUST be checked. These appear as nested nodes inside `WordNode.parts`. The walker must:

- When processing a `CommandNode`, iterate its `.parts`. For each `WordNode` child, check if it has `.parts` containing substitution nodes, and recursively extract those.
- When encountering `CompoundNode` (subshells), walk `.list`.
- When encountering `CommandsubstitutionNode` or `ProcesssubstitutionNode`, walk `.command`.

## Config file (JSON)

Store separately from the hook script for easy editing. Contains:

- `allowed_commands`: flat list of command basenames (ls, cat, grep, find, sort, wc, head, tail, awk, jq, rg, fd, tree, stat, du, df, ps, etc.)
- `git_readonly_subcommands`: list of strictly read-only git subcommands (see git section)
- `wrapper_commands`: list of prefix commands to unwrap (env, nice, time, command)
- `allow_output_redirections`: boolean (should be false)
- `feature_flags`: object with boolean flags for opt-in safe-write categories (all default to false)

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

### Future feature flags (not yet implemented)

Placeholders for future safe-write categories, to be designed as needed:
- `allow_output_redirections` — allow `>` and `>>` to files (currently always rejected).
- `network_reads` — allow read-only network commands like `curl -s` (GET only), `wget -O -`, `ping`, `dig`, `nslookup`, `host`.
- `safe_file_writes` — allow low-risk file operations like `mkdir -p`, `touch` (create empty files only).

### Commands intentionally excluded from the whitelist

- `tee` — always writes to files by design.
- `sed` is included but `-i` is special-cased to reject.
- `awk` is included — it's typically read-only in pipelines. If `awk` writing to files via its built-in `>` operator is a concern, it could be special-cased, but this is uncommon in Claude Code usage.
- `curl`, `wget` — network access, not read-only.
- `cp`, `mv`, `rm`, `mkdir`, `touch`, `chmod`, `chown` — obvious writes.
- `make`, `pip`, `npm`, `cargo`, `docker` — side-effecting build/install tools.
- `parallel` — GNU parallel accepts shell snippet strings as commands, too flexible to parse reliably. On the never-approve list.
- `xargs` is included but special-cased: inner command is extracted and evaluated. `xargs` with no inner command (defaults to `echo`) is approved.

## Hook protocol summary

- **Stdin**: JSON with `session_id`, `cwd`, `permission_mode`, `hook_event_name`, `tool_name`, `tool_input`.
- **Stdout on exit 0**: parsed as JSON. If `{"decision": "approve", "reason": "..."}` → auto-approve. If empty or no `decision` field → fall through.
- **Exit 0 with no decision**: fall through to user prompt.
- **Exit 2**: hard deny (we don't use this — always fail open to user prompt).
- **Stderr**: ignored by Claude Code, safe for debug logging.

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

Set `READONLY_HOOK_DEBUG=1` env var. Logs to `~/.claude/hooks/readonly_bash.log`. Never logs to stdout (that's the protocol channel).

## Test suite

105 test cases covering: simple commands, pipelines, compound commands (&&, ||, ;), git read-only vs write subcommands, output vs input redirections, dangerous commands, shell interpreters, eval/exec, command substitution with dangerous inner commands, process substitution, subshells with mixed safe/unsafe, wrapper command unwrapping, absolute paths, pure assignments, sed -i, tee, and mixed safe/unsafe compound commands.

## Edge cases to keep in mind

- `FOO=bar` with no command → pure assignment, harmless, approve.
- `env` with only VAR=val and no trailing command → harmless, approve.
- `command -v git` → lookup, not execution, approve.
- `echo $(rm -rf /)` → the `rm` fragment extracted from the substitution causes fall-through.
- `(ls; rm foo) | grep bar` → the `rm` inside the subshell causes fall-through.
- `ls -la | sort > sorted.txt` → the `>` on the last pipeline stage causes fall-through.
- `/usr/bin/rm file.txt` → basename resolves to `rm`, falls through.
- `env nice bash -c 'anything'` → wrappers unwrapped, `bash` found underneath, falls through.
- `sed -Ei 's/foo/bar/' file.txt` → combined flag `-Ei` contains `i`, falls through.
- `find . -name "*.py"` → no exec/delete actions, approve.
- `find . -name "*.pyc" -delete` → `-delete` detected, falls through.
- `find . -exec rm {} \;` → inner command `rm` not in whitelist, falls through.
- `find . -exec grep foo {} \;` → inner command `grep` is whitelisted, approve.
- `find . -name "*.py" -exec grep foo {} \; -exec wc -l {} \;` → both inner commands whitelisted, approve.
- `find . -name "*.py" -exec grep foo {} \; -exec rm {} \;` → second inner command `rm` fails, falls through.
- `find . -execdir chmod 755 {} \;` → inner command `chmod` not in whitelist, falls through.
- `find . -fprint /tmp/out.txt` → `-fprint` detected, falls through.
- `ls | xargs grep foo` → inner command `grep` is whitelisted, approve.
- `ls | xargs rm` → inner command `rm` not in whitelist, falls through.
- `ls | xargs -I{} grep foo {}` → flags stripped, inner command `grep` whitelisted, approve.
- `ls | xargs -0 -P4 wc -l` → flags stripped, inner command `wc` whitelisted, approve.
- `ls | xargs` → no inner command, defaults to `echo`, approve.
- `cat files.txt | parallel rm` → `parallel` on never-approve list, falls through.
- `git log --oneline` → always read-only, approve.
- `git diff HEAD~3` → always read-only, approve.
- `git branch feature-x` → falls through by default; approved if `git_local_writes` enabled.
- `git config user.name "foo"` → falls through by default; approved if `git_local_writes` enabled.
- `git config --global user.name "foo"` → always falls through (even with `git_local_writes`, `--global` is rejected).
- `git tag v1.0` → falls through by default; approved if `git_local_writes` enabled.
- `git stash` → falls through by default; approved if `git_local_writes` enabled.
- `git add .` → falls through by default; approved if `git_local_writes` enabled.
- `git push origin main` → always falls through regardless of feature flags.
- `git commit -m "msg"` → always falls through regardless of feature flags.
