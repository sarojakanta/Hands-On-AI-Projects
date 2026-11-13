import ast
import pathlib
import sys

problems = []
for root in ("app", "dashboard", "tests", "migrations"):
    for path in pathlib.Path(root).rglob("*.py"):
        if "versions" in path.parts or "__pycache__" in path.parts:
            continue
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            problems.append(f"{path}: SYNTAX {exc}")
            continue
        imported = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported[(a.asname or a.name).split(".")[0]] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name != "*":
                        imported[a.asname or a.name] = node.lineno
        used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        used |= {n.value.id for n in ast.walk(tree) if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)}
        # names referenced only inside string annotations or __all__
        text_refs = src
        if path.name == "__init__.py":
            continue
        for name, line in imported.items():
            if name in used or name == "annotations":
                continue
            # string annotations (from __future__ import annotations keeps them as names; check raw text too)
            if text_refs.count(name) > 1:
                # appears elsewhere as text (e.g. in a quoted annotation): verify it is not just the import itself
                occurrences = [i for i in range(len(text_refs)) if text_refs.startswith(name, i)]
                if len(occurrences) > 1 and f'"{name}"' in text_refs or f"'{name}'" in text_refs:
                    continue
            problems.append(f"{path}:{line}: unused import {name}")
print("\n".join(problems) or "no unused imports / syntax errors")
sys.exit(1 if problems else 0)
