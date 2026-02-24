"""Hook entry point: python -m readonly_bash_hook"""  # pragma: no cover

import sys  # pragma: no cover

from .output import process_hook_input  # pragma: no cover

stdin_data = sys.stdin.read()  # pragma: no cover
result = process_hook_input(stdin_data)  # pragma: no cover
if result:  # pragma: no cover
    print(result)  # pragma: no cover
sys.exit(0)  # pragma: no cover
