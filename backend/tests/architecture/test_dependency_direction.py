from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = "strategy_workbench"
SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / PACKAGE


def _node_roots() -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for init_file in SRC_ROOT.glob("**/facade/__init__.py"):
        node_root = init_file.parent.parent
        node = ".".join(node_root.relative_to(SRC_ROOT).parts)
        roots[node] = node_root
    return roots


def _read_depends_on(init_file: Path) -> tuple[str, ...]:
    tree = ast.parse(init_file.read_text(encoding="utf-8"))
    for statement in tree.body:
        if (
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and statement.target.id == "DEPENDS_ON"
            and statement.value is not None
        ):
            value = ast.literal_eval(statement.value)
            assert isinstance(value, tuple)
            assert all(isinstance(item, str) for item in value)
            return value
    raise AssertionError(f"missing DEPENDS_ON declaration — file={init_file}")


def _owner(path: Path, roots: dict[str, Path]) -> str | None:
    candidates = [
        (node, root) for node, root in roots.items() if path == root or root in path.parents
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: len(item[1].parts))[0]


def _target_node(module: str, nodes: tuple[str, ...]) -> str | None:
    prefix = f"{PACKAGE}."
    if not module.startswith(prefix):
        return None
    local_module = module.removeprefix(prefix)
    candidates = [
        node for node in nodes if local_module == node or local_module.startswith(f"{node}.")
    ]
    return max(candidates, key=len, default=None)


def _package_of(path: Path) -> str:
    """Dotted name of the package a source file lives in, used to resolve its relative imports."""
    parts = path.relative_to(SRC_ROOT).parts
    if path.name != "__init__.py":
        parts = parts[:-1]
    return ".".join((PACKAGE, *parts))


def _relative_base(package: str, level: int) -> str | None:
    """Package a `level`-dotted import is relative to, or None when it escapes the package root."""
    parts = package.split(".")
    kept = len(parts) - (level - 1)
    if kept < 1:
        return None
    return ".".join(parts[:kept])


def _absolute_imports(path: Path, source: str) -> list[tuple[int, str]]:
    """Every module `source` imports, as an absolute dotted name.

    Relative imports are resolved against the file's own package instead of being skipped. The
    same edge spelled `from ...domain.strategy._x import Y` and spelled in full has to reach the
    checks below, or the boundary binds only whoever writes it the long way (DEFECT-101).
    """
    imports: list[tuple[int, str]] = []
    package = _package_of(path)
    for statement in ast.walk(ast.parse(source)):
        if isinstance(statement, ast.Import):
            imports.extend((statement.lineno, alias.name) for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom):
            if statement.level == 0:
                if statement.module is not None:
                    imports.append((statement.lineno, statement.module))
                continue
            base = _relative_base(package, statement.level)
            if base is None:
                continue
            if statement.module is not None:
                imports.append((statement.lineno, f"{base}.{statement.module}"))
            else:
                # `from . import x`: each imported name is a submodule of the base package.
                imports.extend(
                    (statement.lineno, f"{base}.{alias.name}") for alias in statement.names
                )
    return imports


def _boundary_violations(
    path: Path,
    source: str,
    roots: dict[str, Path],
    dependencies: dict[str, tuple[str, ...]],
) -> list[str]:
    """Undeclared-edge and deep-import violations in one file.

    The source is a parameter, not read from `path`, so a regression test can plant an import
    without writing into the package tree.
    """
    source_node = _owner(path, roots)
    if source_node is None:
        return []
    nodes = tuple(roots)
    violations: list[str] = []
    for line, module in _absolute_imports(path, source):
        target_node = _target_node(module, nodes)
        if target_node is None or target_node == source_node:
            continue
        if target_node not in dependencies[source_node]:
            violations.append(
                f"{path.relative_to(SRC_ROOT)}:{line}: undeclared "
                f"{source_node} -> {target_node} ({module})"
            )
        target_suffix = module.removeprefix(f"{PACKAGE}.{target_node}")
        if target_suffix != ".facade" and not target_suffix.startswith(".facade."):
            violations.append(
                f"{path.relative_to(SRC_ROOT)}:{line}: deep import into {target_node} ({module})"
            )
    return violations


def test_declared_dependency_graph_is_acyclic() -> None:
    roots = _node_roots()
    dependencies = {
        node: _read_depends_on(root / "facade" / "__init__.py") for node, root in roots.items()
    }
    assert set(dependencies) == set(roots)
    for node, allowed in dependencies.items():
        unknown = set(allowed) - set(roots)
        assert not unknown, f"unknown DEPENDS_ON target — node={node} targets={sorted(unknown)}"

    state: dict[str, int] = {}

    def visit(node: str, trail: tuple[str, ...]) -> None:
        if state.get(node) == 1:
            raise AssertionError(f"dependency cycle — {' -> '.join((*trail, node))}")
        if state.get(node) == 2:
            return
        state[node] = 1
        for dependency in dependencies[node]:
            visit(dependency, (*trail, node))
        state[node] = 2

    for node in dependencies:
        visit(node, ())


def test_cross_node_imports_are_declared_and_use_facades() -> None:
    roots = _node_roots()
    dependencies = {
        node: _read_depends_on(root / "facade" / "__init__.py") for node, root in roots.items()
    }
    violations: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        violations.extend(
            _boundary_violations(path, path.read_text(encoding="utf-8"), roots, dependencies)
        )
    assert not violations, "backend dependency boundary violations:\n" + "\n".join(violations)


RELATIVE_IMPORT_PROBE = (
    "from ...domain.strategy._constraints import STRATEGY_SCALAR_CONSTRAINTS\n"
    "from ...adapters.outbound.strategy_memory._repository import InMemoryStrategyRepository\n"
)


def test_relative_imports_reach_the_same_checks_as_absolute_ones() -> None:
    """DEFECT-101: `statement.level == 0` dropped every relative import, so both checks above
    stayed silent for a violation spelled with leading dots — and both of these do resolve."""
    roots = _node_roots()
    dependencies = {
        node: _read_depends_on(root / "facade" / "__init__.py") for node, root in roots.items()
    }
    path = SRC_ROOT / "application" / "strategy_design" / "_service.py"
    source = RELATIVE_IMPORT_PROBE + path.read_text(encoding="utf-8")

    violations = _boundary_violations(path, source, roots, dependencies)

    origin = f"{path.relative_to(SRC_ROOT)}"
    assert violations == [
        f"{origin}:1: deep import into domain.strategy ({PACKAGE}.domain.strategy._constraints)",
        f"{origin}:2: undeclared application.strategy_design -> "
        f"adapters.outbound.strategy_memory "
        f"({PACKAGE}.adapters.outbound.strategy_memory._repository)",
        f"{origin}:2: deep import into adapters.outbound.strategy_memory "
        f"({PACKAGE}.adapters.outbound.strategy_memory._repository)",
    ]


def test_relative_import_resolution_matches_the_absolute_spelling() -> None:
    """The resolver climbs `level - 1` packages, so a single-dot import stays inside its node."""
    path = SRC_ROOT / "application" / "portfolio_design" / "ports" / "outgoing" / "probe.py"
    source = "from ..._models import EngineCompatibility\nfrom .raw_observations import RawFrame\n"

    assert _absolute_imports(path, source) == [
        (1, f"{PACKAGE}.application.portfolio_design._models"),
        (2, f"{PACKAGE}.application.portfolio_design.ports.outgoing.raw_observations"),
    ]
