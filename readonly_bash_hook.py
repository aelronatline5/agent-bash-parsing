#!/usr/bin/env python3
"""Hook entry point script — delegates to the readonly_bash_hook package."""

import sys

from readonly_bash_hook.output import process_hook_input

stdin_data = sys.stdin.read()
result = process_hook_input(stdin_data)
if result:
    print(result)
sys.exit(0)
