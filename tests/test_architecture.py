"""
Architecture boundary test — mechanical enforcement of layer rules.
This test always runs (no API key needed). It is a ratchet:
KNOWN_VIOLATIONS can only shrink, never grow without explicit review.

Layer order (lower = more fundamental):
  1: app/core/formulas.py
  2: app/db/
  3: app/core/datasources/
  4: app/core/agents/
  5: app/core/pipeline.py
  6: app/entrypoints/
  0: app/config.py (allowed by all)

Rules:
  - Layer 1 (formulas): NO app imports at all
  - Layer 2 (db): may import config (layer 0) only
  - Layer 3 (datasources): may import db, config
  - Layer 4 (agents): may import datasources, db, config, formulas
  - Layer 5 (pipeline): may import agents, datasources, db, config, formulas
  - Layer 6 (entrypoints): may import anything
"""

import ast
import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
APP_ROOT = REPO_ROOT / "app"

# Ratchet: only remove entries, never add without review
KNOWN_VIOLATIONS: list[str] = []


def _layer(path: Path) -> int:
    rel = path.relative_to(APP_ROOT)
    parts = rel.parts
    if parts[0] == "config.py":
        return 0
    if parts[0] == "core" and len(parts) > 1 and parts[1] in ("formulas.py", "quant.py", "market_scan.py"):
        return 1
    if parts[0] == "db":
        return 2
    if parts[0] == "core" and len(parts) > 1 and parts[1] == "datasources":
        return 3
    if parts[0] == "core" and len(parts) > 1 and parts[1] == "agents":
        return 4
    if parts[0] == "core" and parts[-1] == "pipeline.py":
        return 5
    if parts[0] == "entrypoints":
        return 6
    return -1  # unclassified (tests, __init__, etc.)


def _allowed_imports(layer: int) -> set[int]:
    """Returns the set of layers that this layer is allowed to import from."""
    perms: dict[int, set[int]] = {
        0: set(),
        1: set(),            # formulas: no app imports
        2: {0},              # db: config only
        3: {0, 1, 2},        # datasources: config, formulas, db
        4: {0, 1, 2, 3},     # agents: config, formulas, db, datasources
        5: {0, 1, 2, 3, 4},  # pipeline: all below
        6: {0, 1, 2, 3, 4, 5},  # entrypoints: all
    }
    return perms.get(layer, set(range(7)))


def _extract_app_imports(source: str) -> list[str]:
    """Return list of app.* module paths imported in source."""
    imports: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app."):
                    imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("app."):
                imports.append(node.module)
    return imports


def _module_to_layer(module: str) -> int:
    """Map an app.* module string to its layer number."""
    if module == "app.config":
        return 0
    if module in ("app.core.formulas", "app.core.quant", "app.core.market_scan"):
        return 1
    if module.startswith("app.db"):
        return 2
    if module.startswith("app.core.datasources"):
        return 3
    if module.startswith("app.core.agents"):
        return 4
    if module in ("app.core.pipeline",):
        return 5
    if module.startswith("app.entrypoints"):
        return 6
    return -1


def test_layer_boundaries():
    violations: list[str] = []

    for py_file in APP_ROOT.rglob("*.py"):
        if py_file.name == "__init__.py":
            continue
        file_layer = _layer(py_file)
        if file_layer < 0:
            continue

        source = py_file.read_text(encoding="utf-8")
        allowed = _allowed_imports(file_layer)

        for imp in _extract_app_imports(source):
            imp_layer = _module_to_layer(imp)
            if imp_layer < 0:
                continue
            if imp_layer not in allowed and imp_layer != file_layer:
                rel = str(py_file.relative_to(REPO_ROOT))
                msg = (
                    f"VIOLATION: {rel} imports {imp} — "
                    f"Layer {file_layer} cannot import from Layer {imp_layer}. "
                    f"See docs/architecture/LAYERS.md"
                )
                violations.append(msg)

    new_violations = [v for v in violations if v not in KNOWN_VIOLATIONS]
    if new_violations:
        formatted = "\n".join(new_violations)
        pytest.fail(
            f"{len(new_violations)} new architecture violation(s):\n{formatted}\n\n"
            "To acknowledge an intentional exception, add to KNOWN_VIOLATIONS "
            "in tests/test_architecture.py — but prefer fixing the import."
        )
