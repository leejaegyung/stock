#!/usr/bin/env python3
"""GC check: files exceeding line limits. 300 = warning, 500 = error."""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
TARGET = ROOT / "app"

WARN_LINES = 300
ERROR_LINES = 500

warnings: list[str] = []
errors: list[str] = []

for py in TARGET.rglob("*.py"):
    count = len(py.read_text(encoding="utf-8").splitlines())
    rel = str(py.relative_to(ROOT))
    if count >= ERROR_LINES:
        errors.append(f"{rel}: {count} lines (limit {ERROR_LINES})")
    elif count >= WARN_LINES:
        warnings.append(f"{rel}: {count} lines (warn at {WARN_LINES})")

for w in warnings:
    print(f"[WARN] {w}")
for e in errors:
    print(f"[FAIL] {e}")

if errors:
    sys.exit(1)
elif not warnings:
    print("[OK] All files within size limits.")
