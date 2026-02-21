"""Unit tests for Step 1 — REJECT (structural): output redirections.

Step 1 rejects any CommandFragment that has an output redirect (> or >>).
fd duplication (e.g., 2>&1) is NOT a file write and is allowed.
Input redirects (<, <<, <<<) are fine.
Output process substitution >(cmd) is flagged by the walker as an output channel.
"""

from __future__ import annotations

import pytest

from readonly_bash_hook import (
    REJECT,
    NEXT,
    CommandFragment,
    step1_redirections,
)


@pytest.mark.parametrize("executable, args, has_output_redirect, expected", [
    # Output redirect → REJECT
    ("ls", ["-la"], True, REJECT),
    ("echo", ["foo"], True, REJECT),
    ("cat", ["file"], True, REJECT),

    # No output redirect → NEXT (pass to next step)
    ("ls", ["-la"], False, NEXT),
    ("grep", ["foo", "bar"], False, NEXT),
    ("cat", ["file.txt"], False, NEXT),

    # Edge: empty args, no redirect
    ("ls", [], False, NEXT),

    # Edge: empty args, with redirect
    ("ls", [], True, REJECT),
])
def test_step1(executable, args, has_output_redirect, expected):
    frag = CommandFragment(
        executable=executable,
        args=args,
        has_output_redirect=has_output_redirect,
    )
    assert step1_redirections(frag) == expected
