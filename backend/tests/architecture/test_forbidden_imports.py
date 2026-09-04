"""Libraries that must stay out of the pure layers (WORKFLOW P1-04, parser ADR D1).

- domain/application never import pydantic: the inbound schema adapter owns discriminator metadata.
- strategy_workbench never imports PyYAML: ruamel.yaml (YAML 1.2) is the only parser (adapter).
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "strategy_workbench"


def _imports(path: Path) -> set[str]:
    modules: set[str] = set()
    for statement in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(statement, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom) and statement.module and statement.level == 0:
            modules.add(statement.module.split(".")[0])
    return modules


def test_domain_and_application_do_not_import_pydantic() -> None:
    offenders = [
        str(path.relative_to(SRC_ROOT))
        for layer in ("domain", "application")
        for path in (SRC_ROOT / layer).rglob("*.py")
        if "pydantic" in _imports(path)
    ]
    assert offenders == [], f"pydantic leaked into pure layers — files={offenders}"


def test_nobody_imports_pyyaml() -> None:
    offenders = [
        str(path.relative_to(SRC_ROOT))
        for path in SRC_ROOT.rglob("*.py")
        if "yaml" in _imports(path)
    ]
    assert offenders == [], f"PyYAML import found — files={offenders} (use the ruamel codec)"
