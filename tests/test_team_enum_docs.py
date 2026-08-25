from __future__ import annotations

import ast
from pathlib import Path


TEAM_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "mycode" / "team"


def _enum_classes():
    for path in TEAM_SOURCE_ROOT.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if any(
                isinstance(base, ast.Name) and base.id == "Enum"
                for base in node.bases
            ) or any(
                isinstance(base, ast.Attribute) and base.attr == "Enum"
                for base in node.bases
            ):
                yield path, node


def test_every_team_enum_has_class_and_value_documentation() -> None:
    failures: list[str] = []
    for path, node in _enum_classes():
        if not ast.get_docstring(node):
            failures.append(f"{path}:{node.lineno} class {node.name} is missing a docstring")
        source_lines = path.read_text(encoding="utf-8").splitlines()
        for item in node.body:
            if not isinstance(item, ast.Assign):
                continue
            if not any(isinstance(target, ast.Name) for target in item.targets):
                continue
            line = source_lines[item.lineno - 1]
            if "#" not in line:
                failures.append(f"{path}:{item.lineno} enum value is missing an inline comment")
    assert failures == []
