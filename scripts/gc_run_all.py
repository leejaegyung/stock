#!/usr/bin/env python3
"""Run all GC checks and print a summary."""

import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
CHECKS = [
    "gc_check_print_statements.py",
    "gc_check_large_files.py",
    "gc_check_formula_in_agents.py",
]

passed = []
failed = []

for script in CHECKS:
    result = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / script)],
        capture_output=True,
        text=True,
    )
    print(f"\n── {script} ──")
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    if result.returncode == 0:
        passed.append(script)
    else:
        failed.append(script)

print("\n══ GC Summary ══")
print(f"  Passed: {len(passed)}")
print(f"  Failed: {len(failed)}")
if failed:
    print("  Failures:")
    for f in failed:
        print(f"    - {f}")
    sys.exit(1)
else:
    print("  All clean.")
