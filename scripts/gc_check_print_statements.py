#!/usr/bin/env python3
"""GC check: raw print() calls in app/ (use logger instead)."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
TARGET = ROOT / "app"
PATTERN = re.compile(r"^\s*print\(", re.MULTILINE)

violations: list[str] = []
for py in TARGET.rglob("*.py"):
    source = py.read_text(encoding="utf-8")
    for i, line in enumerate(source.splitlines(), 1):
        if PATTERN.match(line):
            violations.append(f"{py.relative_to(ROOT)}:{i}: {line.strip()}")

if violations:
    print(f"[FAIL] {len(violations)} raw print() call(s) found. Use logger instead:")
    for v in violations:
        print(f"  {v}")
    sys.exit(1)
else:
    print("[OK] No raw print() calls.")
