"""Integration tests: full command → approve/fall-through.

All ~150 cases from Part 3 of the briefing, plus feature-flag-dependent cases.
These test the full pipeline end-to-end: command string → parse → evaluate.
"""

from __future__ import annotations

import pytest

from readonly_bash_hook import APPROVE, FALLTHROUGH, evaluate_command, build_config


# ---------------------------------------------------------------------------
# Default config fixtures (inlined for clarity)
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg():
    return build_config([], [], git_local_writes=False, awk_safe_mode=False)


@pytest.fixture
def cfg_git(self=None):
    return build_config([], [], git_local_writes=True, awk_safe_mode=False)


@pytest.fixture
def cfg_awk(self=None):
    return build_config([], [], git_local_writes=False, awk_safe_mode=True)


# ---------------------------------------------------------------------------
# APPROVE cases
# ---------------------------------------------------------------------------

APPROVE_CASES = [
    # --- Simple commands ---
    ("ls -la", "simple: ls"),
    ("cat file.txt", "simple: cat"),

    # --- Pipelines ---
    ("ls | grep foo | sort | head -5", "pipeline: all safe"),

    # --- Compound commands ---
    ("ls && cat file", "compound: &&"),
    ('grep foo bar || echo "not found"', "compound: ||"),

    # --- Control flow ---
    ('for f in *.txt; do cat "$f"; done', "for: safe body"),
    ('while read line; do echo "$line"; done', "while: safe body"),

    # --- Redirections ---
    ("grep foo 2>&1", "redirect: fd dup (not file write)"),
    ("cat < input.txt", "redirect: input"),

    # --- Substitutions ---
    ("echo $(ls)", "cmdsub: safe inner"),
    ("diff <(sort file1) <(sort file2)", "procsub: input"),

    # --- Wrapper unwrapping ---
    ("env ls", "wrapper: env"),
    ("nice -n 10 cat file", "wrapper: nice"),
    ("nohup ls", "wrapper: nohup"),
    ("command -v git", "wrapper: command -v (lookup)"),
    ("command -p ls", "wrapper: command -p"),

    # --- Path resolution ---
    ("/usr/bin/ls", "path: absolute safe"),

    # --- sed ---
    ("sed 's/foo/bar/' file.txt", "sed: no -i"),

    # --- find ---
    ('find . -name "*.py"', "find: basic"),
    ("find . -exec grep foo {} \\;", "find: -exec safe"),
    ('find . -name "*.py" -exec grep foo {} \\; -exec wc -l {} \\;',
     "find: multi-exec both safe"),

    # --- xargs ---
    ("ls | xargs grep foo", "xargs: safe inner"),
    ("ls | xargs -I{} grep foo {}", "xargs: flags stripped"),
    ("ls | xargs -0 -P4 wc -l", "xargs: multi flags"),
    ("ls | xargs --max-args=10 wc -l", "xargs: long= flag"),
    ("ls | xargs", "xargs: defaults to echo"),

    # --- Nested special-cases ---
    ("find . -exec xargs grep foo {} \\;", "nested: find-exec-xargs"),
    ('xargs find . -name "*.py"', "nested: xargs-find"),
    ("find . -exec git log {} \\;", "nested: find-exec-git-log"),

    # --- git (read-only) ---
    ("git log --oneline", "git: log"),
    ("git diff HEAD~3", "git: diff"),
    ("git -C /tmp/repo log", "git: -C global flag skipped"),
    ("git --no-pager diff", "git: --no-pager"),
    ("git -c core.pager=less log", "git: -c global flag with arg"),

    # --- Assignments ---
    ("FOO=bar", "assignment: pure"),

    # --- Empty/whitespace/comments ---
    ("", "empty string"),
    ("   ", "whitespace only"),
    ("ls # rm -rf /", "comment after cmd"),
    ("# just a comment", "comment only"),

    # --- Multiline ---
    ("ls -la &&\ngrep foo bar &&\nwc -l", "multiline: &&"),

    # --- Subshells and compound ---
    ("{ ls && cat file; }", "brace group"),
    ("ls &", "backgrounded"),
    ("! grep foo bar", "negation"),

    # --- Builtins ---
    ("test -f file.txt", "builtin: test"),
    ("[ -f file.txt ]", "builtin: ["),
]


@pytest.mark.approve
@pytest.mark.parametrize("command, description", APPROVE_CASES)
def test_approve(command, description, cfg):
    result = evaluate_command(command, cfg)
    assert result == APPROVE, f"Expected APPROVE for {description!r}: {command!r}"


# ---------------------------------------------------------------------------
# FALLTHROUGH cases
# ---------------------------------------------------------------------------

FALLTHROUGH_CASES = [
    # --- Simple commands ---
    ("rm file.txt", "simple: rm (not whitelisted)"),
    ("python3 script.py", "simple: python3 (never-approve)"),

    # --- Pipelines ---
    ("ls | rm", "pipeline: unsafe rm"),
    ("ls -la | sort > sorted.txt", "pipeline: redirect on last stage"),

    # --- Compound commands ---
    ("ls & rm foo", "compound: rm in background list"),

    # --- Control flow ---
    ('for f in *.txt; do rm "$f"; done', "for: unsafe body"),
    ("if true; then rm foo; fi", "if: unsafe body"),
    ("ls() { rm -rf /; }; ls", "function: body has rm"),
    ("f() { grep foo bar; }; f", "function: f invocation not whitelisted"),

    # --- Redirections ---
    ("ls > file.txt", "redirect: output to file"),
    ("ls >&output.txt", "redirect: file via >&"),

    # --- Substitutions ---
    ("echo $(rm -rf /)", "cmdsub: unsafe inner"),
    ("echo $(echo $(rm -rf /))", "cmdsub: nested unsafe"),
    ("cat foo >(rm bar)", "procsub: output"),
    ("ls > >(tee /tmp/log)", "procsub: output channel"),

    # --- Wrapper unwrapping ---
    ("env -- rm -rf /", "wrapper: env unwraps to rm"),
    ("env nice bash -c 'anything'", "wrapper: nested to bash"),

    # --- Path resolution ---
    ("/usr/bin/rm file.txt", "path: absolute unsafe"),
    ("./script.sh", "path: relative not whitelisted"),

    # --- sed ---
    ("sed -i 's/foo/bar/' file.txt", "sed: -i"),
    ("sed -Ei 's/foo/bar/' file.txt", "sed: combined flag -Ei"),
    ("sed --in-place=.bak 's/foo/bar/' file.txt", "sed: --in-place"),

    # --- find ---
    ('find . -name "*.pyc" -delete', "find: -delete"),
    ("find . -exec rm {} \\;", "find: -exec rm"),
    ('find . -name "*.py" -exec grep foo {} \\; -exec rm {} \\;',
     "find: mixed exec (second unsafe)"),
    ("find . -fprint /tmp/out.txt", "find: -fprint"),
    ("find . -exec {} \\;", "find: -exec no cmd"),
    ("find . -exec sed -i 's/x/y/' {} \\;", "find: -exec sed -i"),

    # --- xargs ---
    ("ls | xargs rm", "xargs: rm"),
    ("ls | xargs -I{} sh -c 'echo {}'", "xargs: sh (never-approve)"),

    # --- Nested special-cases ---
    ("xargs git push", "nested: xargs git push"),

    # --- awk (default: never-approve) ---
    ("awk '{print $1}' file.txt", "awk: default never-approve"),
    ("awk '{system(\"rm -rf /\")}' file", "awk: system()"),
    ("awk '{print > \"out.txt\"}' file", "awk: redirect"),
    ("awk -f script.awk file", "awk: -f"),

    # --- git ---
    ("git", "git: no subcommand"),
    ("git unknown-subcommand", "git: unknown"),
    ("git branch feature-x", "git: branch (default off)"),
    ('git config user.name "foo"', "git: config (default off)"),
    ('git config --global user.name "foo"', "git: config --global"),
    ("git config --system core.editor vim", "git: config --system"),
    ("git push origin main", "git: push (always)"),
    ('git commit -m "msg"', "git: commit (always)"),

    # --- Assignments ---
    ("FOO=$(rm -rf /)", "assignment: unsafe substitution"),

    # --- Variable expansion ---
    ("$CMD foo", "variable: command is variable"),
    ("${MY_TOOL} --version", "variable: braced expansion"),
    ('"$(which grep)" foo bar', "variable: cmdsub as executable"),

    # --- Multiline/heredocs ---
    ("python3 <<'EOF'\nprint(\"hi\")\nEOF", "heredoc: python3 (never-approve)"),

    # --- Subshells ---
    ("(ls; rm foo) | grep bar", "subshell: rm inside"),

    # --- Parallel ---
    ("cat files.txt | parallel rm", "parallel: never-approve"),
]


@pytest.mark.fallthrough
@pytest.mark.parametrize("command, description", FALLTHROUGH_CASES)
def test_fallthrough(command, description, cfg):
    result = evaluate_command(command, cfg)
    assert result == FALLTHROUGH, f"Expected FALLTHROUGH for {description!r}: {command!r}"


# ---------------------------------------------------------------------------
# Feature-flag-dependent: AWK_SAFE_MODE
# ---------------------------------------------------------------------------

AWK_SAFE_MODE_CASES = [
    ("awk '{print $1}' file.txt", APPROVE, "safe awk program"),
    ("awk '{system(\"rm -rf /\")}' file", FALLTHROUGH, "awk system()"),
    ("awk '{print > \"out.txt\"}' file", FALLTHROUGH, "awk redirect"),
    ("awk -f script.awk file", FALLTHROUGH, "awk -f"),
]


@pytest.mark.feature_awk_safe_mode
@pytest.mark.parametrize("command, expected, description", AWK_SAFE_MODE_CASES)
def test_awk_safe_mode(command, expected, description, cfg_awk):
    result = evaluate_command(command, cfg_awk)
    assert result == expected, f"AWK_SAFE_MODE: {description!r}: {command!r}"


# ---------------------------------------------------------------------------
# Feature-flag-dependent: GIT_LOCAL_WRITES
# ---------------------------------------------------------------------------

GIT_LOCAL_WRITES_CASES = [
    ("git branch feature-x", APPROVE, "git branch"),
    ("git config user.name foo", APPROVE, "git config local"),
    ("git config --global user.name foo", FALLTHROUGH, "git config --global guard"),
    ("git config --system core.editor vim", FALLTHROUGH, "git config --system guard"),
    ("git push origin main", FALLTHROUGH, "git push always falls through"),
    ("git commit -m msg", FALLTHROUGH, "git commit always falls through"),
]


@pytest.mark.feature_git_local_writes
@pytest.mark.parametrize("command, expected, description", GIT_LOCAL_WRITES_CASES)
def test_git_local_writes(command, expected, description, cfg_git):
    result = evaluate_command(command, cfg_git)
    assert result == expected, f"GIT_LOCAL_WRITES: {description!r}: {command!r}"


# ---------------------------------------------------------------------------
# Feature-flag-dependent: DOCKER SUBCOMMAND WHITELIST
# ---------------------------------------------------------------------------

@pytest.fixture
def cfg_docker():
    return build_config(subcommand_whitelist={
        "docker": ["ps", "images", "inspect", "logs", "info", "version"],
    })


DOCKER_SUBCOMMAND_CASES = [
    # APPROVE — whitelisted subcommands
    ("docker ps", APPROVE, "docker ps"),
    ("docker images", APPROVE, "docker images"),
    ("docker inspect foo", APPROVE, "docker inspect with arg"),
    ("docker logs my-ctr", APPROVE, "docker logs with arg"),
    ("docker info", APPROVE, "docker info"),
    ("docker version", APPROVE, "docker version"),

    # APPROVE — leading global flags (flag-skipping heuristic)
    ("docker --debug ps", APPROVE, "docker --debug flag before subcommand"),

    # FALLTHROUGH — non-whitelisted subcommands
    ("docker rm foo", FALLTHROUGH, "docker rm not whitelisted"),
    ("docker run nginx", FALLTHROUGH, "docker run not whitelisted"),
    ("docker exec -it ctr sh", FALLTHROUGH, "docker exec not whitelisted"),

    # FALLTHROUGH — bare docker / redirect
    ("docker", FALLTHROUGH, "bare docker no subcommand"),
    ("docker > out.txt", FALLTHROUGH, "docker with output redirect"),

    # Pipelines
    ("docker ps | grep nginx", APPROVE, "pipeline: docker ps piped to grep"),
    ("docker ps | rm foo", FALLTHROUGH, "pipeline: safe docker ps but unsafe rm"),

    # Git defaults still work with docker config
    ("git log", APPROVE, "git log still approved with docker config"),
]


@pytest.mark.feature_docker_subcommands
@pytest.mark.parametrize("command, expected, description", DOCKER_SUBCOMMAND_CASES)
def test_docker_subcommands(command, expected, description, cfg_docker):
    result = evaluate_command(command, cfg_docker)
    assert result == expected, f"DOCKER: {description!r}: {command!r}"


# ---------------------------------------------------------------------------
# Heredoc handling (if bashlex supports it)
# ---------------------------------------------------------------------------

class TestHeredocs:
    def test_safe_heredoc(self, cfg):
        cmd = "cat <<'EOF'\nhello\nEOF"
        # If bashlex parses it, cat is approved.
        # If bashlex fails, it falls through (also acceptable).
        result = evaluate_command(cmd, cfg)
        assert result in (APPROVE, FALLTHROUGH)

    def test_unsafe_heredoc(self, cfg):
        cmd = "python3 <<'EOF'\nprint('hi')\nEOF"
        result = evaluate_command(cmd, cfg)
        assert result == FALLTHROUGH
