"""Reproduce the baseline/current architecture inventory without importing crapkit."""
import ast
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
BASE = "20f00e1371334f84aa70bba6f7b23bfc4bdae0f6"
OUT = Path(__file__).parent


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def inventory(baseline):
    paths = (git("ls-tree", "-r", "--name-only", BASE).decode().splitlines()
             if baseline else sorted(p.relative_to(ROOT).as_posix()
                                     for folder in ("src", "tests", "tools")
                                     for p in (ROOT / folder).rglob("*.py")))
    modules = []
    for name in paths:
        if not name.endswith(".py"):
            continue
        source = (git("show", f"{BASE}:{name}").decode("utf-8-sig") if baseline
                  else (ROOT / name).read_text(encoding="utf-8-sig"))
        tree = ast.parse(source)
        functions = [n for n in ast.walk(tree)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        imports = [{"module": n.module, "level": n.level,
                    "names": [a.name for a in n.names], "line": n.lineno}
                   for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        modules.append({"path": name, "lines": len(source.splitlines()),
                        "functions": len(functions), "imports": imports})
    result = {"modules": modules}
    for label, prefix in (("production", "src/"), ("tests", "tests/"), ("tools", "tools/")):
        owned = [m for m in modules if m["path"].startswith(prefix)]
        result[label] = {"files": len(owned),
                         "lines": sum(m["lines"] for m in owned),
                         "functions": sum(m["functions"] for m in owned)}
    return result


before, after = inventory(True), inventory(False)
result = {"baseline": BASE, "before": before, "after": after,
          "current_commit": git("rev-parse", "HEAD").decode().strip(),
          "method": "AST inventory of the release and current working Python source; lines include comments and blanks"}
(OUT / "structure-measurements.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps({"before": {k: v for k, v in before.items() if k != "modules"},
                  "after": {k: v for k, v in after.items() if k != "modules"}}, indent=2))
