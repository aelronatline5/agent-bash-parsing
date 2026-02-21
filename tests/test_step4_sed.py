"""Unit tests for Step 4 handler: handle_sed.

sed is on the whitelist, but -i / --in-place modes write to files.
handle_sed rejects if any argument is or contains -i or --in-place.
Combined flags like -Ei or -ni also trigger rejection.
"""

from __future__ import annotations

import pytest

from readonly_bash_hook import PASS, REJECT, handle_sed


@pytest.mark.parametrize("args, expected", [
    # Safe: no -i
    (["s/foo/bar/", "file.txt"], PASS),
    (["-E", "s/foo/bar/", "file.txt"], PASS),
    (["-n", "s/foo/bar/", "file.txt"], PASS),
    (["-e", "s/foo/bar/", "-e", "s/baz/qux/", "file.txt"], PASS),
    (["1,5p", "file.txt"], PASS),

    # Reject: -i standalone
    (["-i", "s/foo/bar/", "file.txt"], REJECT),

    # Reject: -i with backup suffix (e.g., -i.bak)
    (["-i.bak", "s/foo/bar/", "file.txt"], REJECT),

    # Reject: --in-place
    (["--in-place", "s/foo/bar/", "file.txt"], REJECT),

    # Reject: --in-place=.bak
    (["--in-place=.bak", "s/foo/bar/", "file.txt"], REJECT),

    # Reject: combined short flags containing i
    (["-Ei", "s/foo/bar/", "file.txt"], REJECT),
    (["-ni", "s/foo/bar/", "file.txt"], REJECT),
    (["-iE", "s/foo/bar/", "file.txt"], REJECT),

    # Edge: -i at end of combined flag
    (["-nEi", "s/foo/bar/", "file.txt"], REJECT),

    # Edge: flag that starts with -i but is not -i (none exist for sed, but test robustness)
    # -i is always treated as in-place when it starts with -i
])
def test_handle_sed(args, expected):
    result = handle_sed(args)
    assert result == expected, f"handle_sed({args}) = {result}, expected {expected}"
