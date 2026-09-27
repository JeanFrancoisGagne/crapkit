"""A JavaScript or TypeScript function below a comment that holds U+2028, U+2029, a
form feed or NEL keeps its own coverage, whichever provider measured it.

Each case in tests/fixtures/recorded/vitest_comment_line_ends.json is what vitest
4.1.10 wrote to coverage-final.json for the source beside it: a comment holding five
of one character, then `f`, which runs both ways, and `g`, which never runs.
@vitest/coverage-v8 numbers a JavaScript file at LF only and puts `f` on line 2.
Babel (@vitest/coverage-istanbul) and a TypeScript file's source map also end a line
at U+2028 and U+2029 and put it on line 7. No producer ends one at a form feed or NEL.
crapkit counts a comment one line per LF and places each producer's numbers on those
lines, so `f` starts on line 2 and reads 100% under every producer.
"""
import json
from pathlib import Path
import sys

import pytest

from conftest import git_commit_all, git_init_repo, run_cli

CASES = json.loads((Path(__file__).parent.parent / "fixtures/recorded/vitest_comment_line_ends.json")
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


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_function_below_the_comment_keeps_its_own_coverage(tmp_path, case):
    replay_repo(tmp_path, case)
    result = run_cli(tmp_path, "coverage")
    assert result.returncode == 0, (result.stdout, result.stderr)

    rows = json.loads(run_cli(tmp_path, "worklist", "--json").stdout)["active"]

    assert sorted((row["handle"], row["start"], row["cov"]) for row in rows) == [
        ("f", 2, 1.0), ("g", 9, 0.0)]
