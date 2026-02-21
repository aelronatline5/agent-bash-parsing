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
5. **Never-approve list** — hard-reject interpreters and escape hatches: `eval`, `exec`, `source`, `.`, `sudo`, `su`, `bash`, `sh`, `zsh`, `fish`, `dash`, `csh`, `ksh`, `python`, `python3`, `perl`, `ruby`, `node`, `deno`, `bun`.
6. **Special-case `git`** — only approve if subcommand is in an explicit read-only list: `blame`, `branch`, `config`, `diff`, `log`, `ls-files`, `ls-tree`, `remote`, `rev-parse`, `show`, `show-ref`, `status`, `tag`. All others (push, commit, checkout, merge, rebase, reset, stash, add, rm, clean, etc.) fall through.
7. **General whitelist check** — approve if executable basename is in `allowed_commands`.
8. **Default** — not in whitelist → fall through.

## Recursive walking details

Command substitution (`$(rm -rf /)` inside `echo $(rm -rf /)`) and process substitution (`<(rm foo)` inside `cat <(rm foo)`) MUST be checked. These appear as nested nodes inside `WordNode.parts`. The walker must:

- When processing a `CommandNode`, iterate its `.parts`. For each `WordNode` child, check if it has `.parts` containing substitution nodes, and recursively extract those.
- When encountering `CompoundNode` (subshells), walk `.list`.
- When encountering `CommandsubstitutionNode` or `ProcesssubstitutionNode`, walk `.command`.

## Config file (JSON)

Store separately from the hook script for easy editing. Contains:

- `allowed_commands`: flat list of command basenames (ls, cat, grep, find, sort, wc, head, tail, awk, jq, rg, fd, tree, stat, du, df, ps, etc.)
- `git_readonly_subcommands`: list of safe git subcommands
- `wrapper_commands`: list of prefix commands to unwrap (env, nice, time, command)
- `allow_output_redirections`: boolean (should be false)

### Commands intentionally excluded from the whitelist

- `tee` — always writes to files by design.
- `sed` is included but `-i` is special-cased to reject.
- `awk` is included — it's typically read-only in pipelines. If `awk` writing to files via its built-in `>` operator is a concern, it could be special-cased, but this is uncommon in Claude Code usage.
- `curl`, `wget` — network access, not read-only.
- `cp`, `mv`, `rm`, `mkdir`, `touch`, `chmod`, `chown` — obvious writes.
- `make`, `pip`, `npm`, `cargo`, `docker` — side-effecting build/install tools.

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
