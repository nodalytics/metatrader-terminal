"""Annotations the terminal's Python can evaluate.

**This is not style.** The API runs inside the terminal's container on a
Python older than 3.10. A `float | None` written where Python evaluates it at
runtime - a module-level variable annotation, a default, a dataclass field -
raises `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`
at import, and supervisord then restarts the server once a second forever. The
container reports itself up the whole time.

One line of that shipped on 2026-11-05 and took the bridge down. CI could not
catch it: the test suite runs on 3.11, where the syntax is fine, so every test
passed on code that cannot import where it actually runs.

`from __future__ import annotations` makes annotations strings and the syntax
harmless, so a module that has it is exempt. A module without it is checked.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[2] / "app"


def _sources() -> list[pathlib.Path]:
    return sorted(p for p in APP.rglob("*.py") if "__pycache__" not in p.parts)


def _is_union(node: ast.AST | None) -> bool:
    """`X | Y` anywhere inside an annotation, including nested."""
    if node is None:
        return False
    return any(
        isinstance(child, ast.BinOp) and isinstance(child.op, ast.BitOr)
        for child in ast.walk(node)
    )


def _offenders(tree: ast.AST) -> list[int]:
    found: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and _is_union(node.annotation):
            found.append(node.lineno)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_union(node.returns):
                found.append(node.lineno)
            args = node.args
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs]:
                if _is_union(arg.annotation):
                    found.append(arg.lineno)
    return found


@pytest.mark.parametrize("path", _sources(), ids=lambda p: p.name)
def test_a_module_without_future_annotations_uses_no_pep604_union(path):
    """Either the future import, or no `X | Y` in an annotation."""
    source = path.read_text()
    tree = ast.parse(source)
    exempt = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(a.name == "annotations" for a in node.names)
        for node in ast.walk(tree)
    )
    if exempt:
        return

    lines = _offenders(tree)
    assert not lines, (
        f"{path.relative_to(APP.parent)} lines {lines} use `X | Y` in an annotation and "
        f"the module has no `from __future__ import annotations`. The terminal's Python "
        f"evaluates these at import and raises TypeError, which crash-loops the API."
    )


def test_the_check_can_fail():
    """The control. A guard that cannot fail is the thing it is guarding
    against, one level up."""
    tree = ast.parse("x: float | None = None\n")

    assert _offenders(tree) == [1]
