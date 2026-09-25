"""Inventory the reviewed commit without changing the checkout."""
import ast
import collections
import json
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1]).resolve()
out = Path(sys.argv[2]).resolve()
tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode().split("\0")
tracked = [p for p in tracked if p]
groups = collections.defaultdict(lambda: {"files": 0, "bytes": 0})
rows = []
private_imports = []
for rel in tracked:
    p = root / rel
    group = "architecture evidence" if rel.startswith("docs/architecture/") else rel.split("/")[0]
    groups[group]["files"] += 1
    groups[group]["bytes"] += p.stat().st_size
    if p.suffix != ".py" or rel.startswith("docs/architecture/"):
        continue
    source = p.read_text(encoding="utf-8-sig")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        continue
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names = [a.name for a in node.names]
            imports.append({"module": node.module, "level": node.level, "names": names, "line": node.lineno})
            if rel.startswith("tests/") and node.module and node.module.startswith("crapkit"):
                for name in names:
                    if name.startswith("_"):
                        private_imports.append({"file": rel, "module": node.module, "name": name, "line": node.lineno})
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    rows.append({"path": rel, "lines": len(source.splitlines()), "functions": len(functions),
                 "one_statement_functions": sum(len(n.body) == 1 for n in functions), "imports": imports})
result = {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
          "tracked_files": len(tracked), "groups": dict(groups), "python": rows,
          "private_test_imports": private_imports}
out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"commit": result["commit"], "tracked_files": len(tracked), "groups": groups,
                  "private_test_imports": len(private_imports),
                  "private_names": len({(p['module'], p['name']) for p in private_imports}),
                  "largest_production": sorted([r for r in rows if r['path'].startswith('src/')], key=lambda r: -r['lines'])[:10]},
                 indent=2))
