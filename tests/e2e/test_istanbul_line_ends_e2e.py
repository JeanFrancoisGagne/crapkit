"""A function's coverage stays its own when its producer numbers the file's lines
unlike the reader.

The lane replays what vitest 4.1.10 wrote for each source (the recorded cases in
tests/fixtures/recorded/vitest_line_ends.json): `f` runs both ways, `g` never runs.
@vitest/coverage-v8 put `f` five lines above where the reader has it in a JavaScript
file with five lone CRs, and the TypeScript source map put it five lines below in a
file with five U+2028 in a string. The worklist gave `f` the coverage of `g`, or the
reverse.
"""
import json
from pathlib import Path
import sys

import pytest

from conftest import git_commit_all, git_init_repo, run_cli

CASES = json.loads((Path(__file__).parent.parent / "fixtures/recorded/vitest_line_ends.json")
                   .read_text(encoding="utf-8"))


def replay_repo(tmp_path: Path, case: str) -> None:
    fixture = CASES[case]
    language = "typescript" if fixture["file"].endswith(".ts") else "javascript"
    git_init_repo(tmp_path)
    (tmp_path / fixture["file"]).write_bytes(fixture["source"].encode("utf-8"))
    artifact = json.dumps({fixture["file"]: fixture["record"]})
    (tmp_path / "emit.py").write_text(
        "from pathlib import Path\n"
        "Path('.crapkit').mkdir(exist_ok=True)\n"
        f"Path('.crapkit/cov.json').write_text({artifact!r}, encoding='utf-8')\n",
        encoding="utf-8")
    command = json.dumps('"' + sys.executable.replace("\\", "/") + '" emit.py')
    (tmp_path / "crapkit.toml").write_text(
        f'[crapkit]\ntarget=1\n[[scope]]\nname="src"\npaths=["{fixture["file"]}"]\n'
        f'languages=["{language}"]\n[[lane]]\nname="unit"\nscopes=["src"]\n'
        f'command={command}\nartifact=".crapkit/cov.json"\nparser="istanbul"\n',
        encoding="utf-8")
    git_commit_all(tmp_path, case)


@pytest.mark.parametrize("case", ["v8_js_lone_cr", "v8_ts_ls_string"])
def test_the_worklist_gives_each_function_its_own_coverage(tmp_path, case):
    replay_repo(tmp_path, case)
    result = run_cli(tmp_path, "coverage")
    assert result.returncode == 0, (result.stdout, result.stderr)

    rows = json.loads(run_cli(tmp_path, "worklist", "--json").stdout)["active"]

    assert sorted((row["handle"], row["cov"]) for row in rows) == [("f", 1.0), ("g", 0.0)]
