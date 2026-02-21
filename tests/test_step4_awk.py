"""Unit tests for Step 4 handler: handle_awk.

handle_awk is only registered when AWK_SAFE_MODE is enabled.
It performs best-effort textual scanning of the awk program string for:
  - system() calls → REJECT
  - pipe operators (print ... |, ... | getline) → REJECT
  - file output (> or >> in awk context) → REJECT
  - -f flag (program from file, can't analyze) → REJECT
  - None of the above → PASS
"""

from __future__ import annotations

import pytest

from readonly_bash_hook import PASS, REJECT, handle_awk


@pytest.mark.parametrize("args, expected", [
    # Safe: simple print
    (["{print $1}", "file.txt"], PASS),
    (["{print $0}", "file.txt"], PASS),

    # Safe: field separator flag
    (["-F:", "{print $1}", "/etc/passwd"], PASS),
    (["-F", ":", "{print $1}", "/etc/passwd"], PASS),

    # Safe: assignment in awk
    (["{x=$1; print x}", "file"], PASS),

    # Safe: multiple patterns
    (["/foo/{print $1}", "file"], PASS),
    (["BEGIN{x=0} {x+=$1} END{print x}", "file"], PASS),

    # Reject: system() call
    (['{system("rm -rf /")}', "file"], REJECT),
    (['{system("ls")}', "file"], REJECT),
    (["{x=system(\"echo hi\")}", "file"], REJECT),

    # Reject: pipe in print context
    (['{print | "sort"}', "file"], REJECT),
    (['{print $1 | "sort -n"}', "file"], REJECT),

    # Reject: getline with pipe
    (['{cmd | getline line}', "file"], REJECT),

    # Reject: file output operators
    (['{print > "out.txt"}', "file"], REJECT),
    (['{print >> "out.txt"}', "file"], REJECT),

    # Reject: -f flag (reads program from file)
    (["-f", "script.awk", "file"], REJECT),
    (["-f", "prog.awk", "data.txt"], REJECT),

    # Edge: multiple -v assignments (safe)
    (["-v", "x=1", "-v", "y=2", "{print x, y}", "file"], PASS),
])
def test_handle_awk(args, expected):
    result = handle_awk(args)
    assert result == expected, f"handle_awk({args}) = {result}, expected {expected}"
