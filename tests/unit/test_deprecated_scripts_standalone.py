from __future__ import annotations

import ast
from pathlib import Path


def test_deprecated_one_off_scripts_do_not_import_ymca() -> None:
    scripts_dir = Path(__file__).resolve().parents[2] / "deprecated" / "one_off_scripts"

    for script_path in sorted(scripts_dir.glob("*.py")):
        module = ast.parse(script_path.read_text(encoding="utf-8"), filename=str(script_path))
        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                imported_modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported_modules = [] if node.module is None else [node.module]
            else:
                continue

            assert all(
                imported != "ymca" and not imported.startswith("ymca.")
                for imported in imported_modules
            ), f"{script_path.name} still imports YMCA runtime code."
