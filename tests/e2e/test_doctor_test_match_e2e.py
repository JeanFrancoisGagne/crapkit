"""The test file `crapkit doctor` names beside "all flagged untested", read off
the command's own stdout: the one beside the code, not the first of twenty
same-named tests alphabetically, and never a test in another language: not
one below the directory, and not one beside unscored .js files that sit next
to the scored Python."""
from pathlib import Path

from conftest import git_commit_all, git_init_repo, run_cli

from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

_TS = "export function f(a: number) { return a ? 1 : 2; }\n"
_PY = "def f(a):\n    return 1 if a else 2\n"

_CONFIG = ('[crapkit]\ntarget = 6\n\n'
           '[[scope]]\nname = "ts"\npaths = ["src", "extensions"]\nlanguages = ["typescript"]\n\n'
           '[[scope]]\nname = "py"\npaths = ["scripts"]\nlanguages = ["python"]\n\n'
           '[exclude]\nglobs = ["make_cov.py"]\n\n'
           '[[lane]]\nname = "unit"\ncommand = "python make_cov.py"\n'
           'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["ts", "py"]\n')

_FILES = {
    "extensions/rpc/handler.ts": _TS,
    "extensions/rpc/handler.test.ts": "",
    "extensions/acpx/config.ts": _TS,
    "extensions/acpx/config.test.ts": "",
    "src/hooks/boot/handler.ts": _TS,
    "src/hooks/boot/handler.test.ts": "",
    "scripts/nlp/config.py": _PY,
    "scripts/docs/mermaid.py": _PY,
    "docs/_mermaid_test.md": "# not code\n",
    "scripts/sam_admin/app.py": _PY,
    "scripts/sam_admin/static/api.js": "export const a = 1;\n",
    "scripts/sam_admin/static/__tests__/api.test.js": "",
    "scripts/tool/run.py": _PY,
    "scripts/tool/run.js": "export const a = 1;\n",
    "scripts/tool/widget.test.js": "",
    "extensions/lib/run.test.js": "",
    "make_cov.py": "print('stub')\n",
    ".gitignore": ".crapkit/\n",
    "crapkit.toml": _CONFIG,
}


def _untested(scope: str, path: str) -> ScoredRow:
    return ScoredRow(scope, path, "f( a )", 1, 1, 3, 3, 3, 5, 1, 1, 0.0, "untested",
                     3.0, "add-tests", 2)


def _repo(tmp_path: Path) -> Path:
    for rel, body in _FILES.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    git_init_repo(tmp_path)
    git_commit_all(tmp_path, "i")
    (tmp_path / ".crapkit").mkdir()
    store = SnapshotStore(tmp_path / ".crapkit" / "crap.sqlite")
    store.write_run(commit="c0", tool_versions={}, lanes={"unit": {}}, rows=[
        _untested("ts", "extensions/rpc/handler.ts"),
        _untested("ts", "src/hooks/boot/handler.ts"),
        _untested("py", "scripts/nlp/config.py"),
        _untested("py", "scripts/docs/mermaid.py"),
        _untested("py", "scripts/sam_admin/app.py"),
        _untested("py", "scripts/tool/run.py"),
    ])
    return tmp_path


def test_doctor_names_the_test_beside_the_code_and_no_test_in_another_language(tmp_path: Path):
    res = run_cli(_repo(tmp_path), "doctor", encoding="utf-8")
    warns = [line for line in res.stdout.splitlines() if "all flagged untested" in line]

    assert warns == [
        "WARN extensions/rpc: 1 function(s) all flagged untested while "
        "extensions/rpc/handler.test.ts exists — tests exist but no lane measures them",
        "WARN src/hooks/boot: 1 function(s) all flagged untested while "
        "src/hooks/boot/handler.test.ts exists — tests exist but no lane measures them",
    ], res.stdout + res.stderr
    assert res.returncode == 0, res.stdout + res.stderr
