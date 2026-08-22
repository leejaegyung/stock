#!/usr/bin/env python3
"""
GC check: detect EV/Kelly calculations directly implemented in agents/ directory.
All quantitative calculations must live in app/core/formulas.py.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
AGENTS_DIR = ROOT / "app" / "core" / "agents"

# Patterns that indicate inline formula implementation (not just calling formulas.py)
FORMULA_PATTERNS = [
    (re.compile(r"win_prob\s*\*\s*\w+\s*-\s*\(1\s*-\s*win_prob\)"), "expected_value inline"),
    (re.compile(r"=\s*p\s*-\s*\(1\s*-\s*p\)\s*/\s*r", re.IGNORECASE), "kelly inline"),
    (re.compile(r"kelly\s*=\s*.+\*\s*.+/\s*2"), "half-kelly inline"),
]

violations: list[str] = []

for py in AGENTS_DIR.rglob("*.py"):
    if py.name in ("base.py", "__init__.py"):
        continue
    source = py.read_text(encoding="utf-8")
    for pattern, label in FORMULA_PATTERNS:
        for i, line in enumerate(source.splitlines(), 1):
            if pattern.search(line):
                rel = str(py.relative_to(ROOT))
                violations.append(
                    f"{rel}:{i}: suspected {label} — use app.core.formulas instead. "
                    "See docs/golden-principles/FORMULAS.md"
                )

if violations:
    print(f"[FAIL] {len(violations)} formula-in-agent violation(s):")
    for v in violations:
        print(f"  {v}")
    sys.exit(1)
else:
    print("[OK] No inline formula implementations in agents/.")
