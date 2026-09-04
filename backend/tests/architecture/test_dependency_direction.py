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


def _absolute_imports(path: Path) -> list[tuple[int, str]]:
    imports: list[tuple[int, str]] = []
    for statement in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(statement, ast.Import):
            imports.extend((statement.lineno, alias.name) for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom) and statement.level == 0:
            if statement.module is not None:
                imports.append((statement.lineno, statement.module))
    return imports


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
    nodes = tuple(roots)
    dependencies = {
        node: _read_depends_on(root / "facade" / "__init__.py") for node, root in roots.items()
    }
    violations: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        source_node = _owner(path, roots)
        if source_node is None:
            continue
        for line, module in _absolute_imports(path):
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
                    f"{path.relative_to(SRC_ROOT)}:{line}: deep import into "
                    f"{target_node} ({module})"
                )
    assert not violations, "backend dependency boundary violations:\n" + "\n".join(violations)
