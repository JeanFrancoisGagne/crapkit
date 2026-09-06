"""Read-only checks against the integrated root, with temporary fixtures."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import venv

import crapkit

ROOT = Path(__file__).resolve().parents[3]
assert Path(crapkit.__file__).resolve() == ROOT / "src/crapkit/__init__.py", crapkit.__file__
print("VERIFIED_IMPORT", crapkit.__file__)


def schemas(source):
    assignments = []
    for node in ast.parse(source).body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            assignments.append(node)
            target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0]
            if isinstance(target, ast.Name) and target.id == "TOOLS":
                break
    namespace = {}
    exec(compile(ast.Module(body=assignments, type_ignores=[]), "schemas", "exec"), namespace)
    return namespace["TOOLS"]


old_source = subprocess.check_output(
    ["git", "show", "20f00e1371334f84aa70bba6f7b23bfc4bdae0f6:src/crapkit/mcp_server.py"], cwd=ROOT, text=True, encoding="utf-8")
new_source = (ROOT / "src/crapkit/mcp_server.py").read_text(encoding="utf-8")
print("MCP_TOOLS_EQUAL", schemas(old_source) == schemas(new_source))

with TemporaryDirectory(prefix="crapkit-probe-review-") as directory:
    fixture = Path(directory)
    (fixture / "sitecustomize.py").write_text(
        "import sys\nprint('BENIGN_STARTUP_WARNING', file=sys.stderr)\n", encoding="utf-8")
    (fixture / "src").mkdir()
    (fixture / "src/logic.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    command = f'"{sys.executable}" -m pytest --cov=src --cov-branch --cov-report=json:cov.json'
    config = (
        '[[scope]]\nname="src"\npaths=["src"]\nlanguages=["python"]\n'
        '[[lane]]\nname="py"\ncommand=' + json.dumps(command) + '\n'
        'artifact="cov.json"\nparser="coveragepy"\nscopes=["src"]\n')
    (fixture / "crapkit.toml").write_text(config, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(fixture)], check=True)
    subprocess.run(["git", "add", "src", "crapkit.toml"], cwd=fixture, check=True)
    env = dict(os.environ, PYTHONPATH=str(fixture) + os.pathsep + str(ROOT / "src"))
    checked_main = (
        "from pathlib import Path; import crapkit,runpy; "
        f"assert Path(crapkit.__file__).resolve() == Path({str(ROOT / 'src/crapkit/__init__.py')!r}); "
        "runpy.run_module('crapkit',run_name='__main__')")
    done = subprocess.run(
        [sys.executable, "-B", "-c", checked_main, "doctor", "--repo", str(fixture)],
        cwd=fixture, env=env, capture_output=True, text=True, encoding="utf-8")
    print("DOCTOR_RETURN_CODE", done.returncode)
    print("DOCTOR_STDOUT", done.stdout)
    print("DOCTOR_STDERR", done.stderr)

    interpreter_root = fixture / "Jos\u00e9"
    venv.EnvBuilder(system_site_packages=True, with_pip=False).create(interpreter_root)
    launcher = interpreter_root / "Scripts/python.exe"
    from crapkit.cli import admin

    os.environ["PYTHONIOENCODING"] = "cp1252"
    admin._runner_report.cache_clear()
    report = admin._runner_report(str(launcher))
    print("UNICODE_EXPECTED", json.dumps(str(launcher)))
    print("UNICODE_REPORT", json.dumps(report))
    print("UNICODE_EQUAL", report is not None and report[0] == str(launcher))
