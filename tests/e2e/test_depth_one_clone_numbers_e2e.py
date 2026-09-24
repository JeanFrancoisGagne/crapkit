"""worklist, next-item and brief in a depth-1 clone say the history is cut.

`risk` is ccn times churn, and churn counts the commits git holds. A `git clone
--depth 1`, the default `actions/checkout` depth, holds one: src/hot.py, edited
in six commits, reads one commit like src/cold.py, and the ranking falls back
to ccn order. The Action renders that ranking into its pull-request comment.
Nothing said so: stderr was empty and the JSON carried no field for it.

Each reader now prints one stderr line naming the fetch that fixes it and
carries `shallow: true`; the Action's comment repeats the line. The counts
keep their values in this schema. A full clone of the same repo is the
control: `shallow: false` and no line. The clone is made through `file://`,
the transport that honours --depth.
"""
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner

run_cli = cli_runner(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
FETCH = ("this shallow clone does not hold every commit: set fetch-depth: 0 on the checkout "
         "or run git fetch --unshallow")
CHURN_LINE = f"warning: churn counts read only the commits this clone holds; {FETCH}"
BRIEF_LINE = f"warning: churn counts and mark ages read only the commits this clone holds; {FETCH}"
KNOT = ("def {name}(a, b, c):\n    if a:\n        return 1\n    if b:\n        return 2\n"
        "    if c:\n        return 3\n    return {value}\n")
TOML = """[crapkit]
target = 6

[[scope]]
name = "src"
paths = ["src"]
languages = ["python"]

[[lane]]
name = "py"
command = "python -c pass"
artifact = "cov.json"
parser = "istanbul"
scopes = ["src"]
"""


def git(root: Path, *args: str, date: str | None = None) -> None:
    env = dict(os.environ)
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false",
                    *args], cwd=root, check=True, capture_output=True, env=env)


def write(root: Path, rel: str, text: str) -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8", newline="\n")


def commit(root: Path, message: str, date: str) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", message, date=date)


def origin(root: Path) -> Path:
    """src/hot.py changed in six commits in September, src/cold.py once in June."""
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, "crapkit.toml", TOML)
    write(root, ".gitignore", ".crapkit/\ncov.json\n")
    write(root, "src/cold.py", KNOT.format(name="cold", value=0))
    write(root, "src/hot.py", KNOT.format(name="hot", value=0))
    commit(root, "init", "2026-06-01T12:00:00+00:00")
    for i in range(1, 7):
        write(root, "src/hot.py", KNOT.format(name="hot", value=i))
        commit(root, f"hot {i}", f"2026-09-{10 + i:02d}T12:00:00+00:00")
    return root


def measure(root: Path) -> None:
    """An istanbul artifact keyed by this checkout's own paths, then a run."""
    data = {}
    for rel, name in (("src/hot.py", "hot"), ("src/cold.py", "cold")):
        key = str(root / rel)
        data[key] = {"path": key, "branchMap": {}, "b": {}, "f": {"0": 1},
                     "fnMap": {"0": {"name": name, "decl": {"start": {"line": 1}},
                                     "loc": {"start": {"line": 1}, "end": {"line": 8}}}},
                     "statementMap": {"0": {"start": {"line": 2}, "end": {"line": 2}}},
                     "s": {"0": 0}}
    (root / "cov.json").write_text(json.dumps(data), encoding="utf-8")
    res = run_cli(root, "coverage", "--reuse-artifacts")
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.fixture(scope="module")
def checkouts(tmp_path_factory) -> dict[str, Path]:
    base = tmp_path_factory.mktemp("clones")
    source = origin(base / "origin")
    for name, depth in (("full", ()), ("shallow", ("--depth", "1"))):
        git(base, "clone", "-q", *depth, source.as_uri(), str(base / name))
        measure(base / name)
    return {"full": base / "full", "shallow": base / "shallow"}


# (commits on src/hot.py, the flag, whether stderr carries the line)
EXPECTED = {"full": (7, False), "shallow": (1, True)}


def commits_of(payload: dict) -> dict:
    return {e["path"]: e["commits"] for e in payload["active"] + payload["dormant_top"]}


@pytest.mark.parametrize("checkout", ["full", "shallow"])
def test_worklist_json_carries_shallow_beside_counts_that_keep_their_values(checkouts, checkout):
    hot, shallow = EXPECTED[checkout]

    res = run_cli(checkouts[checkout], "worklist", "--json")

    payload = json.loads(res.stdout)
    assert res.returncode == 0, res.stderr
    assert payload["shallow"] is shallow
    assert commits_of(payload) == {"src/hot.py": hot, "src/cold.py": 1}
    assert res.stderr.count(CHURN_LINE) == (1 if shallow else 0), res.stderr


@pytest.mark.parametrize("checkout", ["full", "shallow"])
def test_the_plain_worklist_prints_the_line_once(checkouts, checkout):
    res = run_cli(checkouts[checkout], "worklist")

    assert res.returncode == 0 and res.stdout.startswith("worklist @ ")
    assert res.stderr.count(CHURN_LINE) == (1 if EXPECTED[checkout][1] else 0), res.stderr


@pytest.mark.parametrize("checkout", ["full", "shallow"])
def test_next_item_carries_shallow_and_prints_the_line(checkouts, checkout):
    hot, shallow = EXPECTED[checkout]

    res = run_cli(checkouts[checkout], "next-item", "--top", "2")

    payload = json.loads(res.stdout)
    assert res.returncode == 0, res.stderr
    assert payload["shallow"] is shallow
    assert {i["path"]: i["commits"] for i in payload["items"]}["src/hot.py"] == hot
    assert res.stderr.count(CHURN_LINE) == (1 if shallow else 0), res.stderr


@pytest.mark.parametrize("checkout", ["full", "shallow"])
def test_brief_carries_shallow_beside_its_churn(checkouts, checkout):
    hot, shallow = EXPECTED[checkout]

    res = run_cli(checkouts[checkout], "brief", "src/hot.py", "hot", "--json")

    packet = json.loads(res.stdout)
    assert res.returncode == 0, res.stderr
    assert (packet["shallow"], packet["churn"]["commits"]) == (shallow, hot)
    assert res.stderr.count(BRIEF_LINE) == (1 if shallow else 0), res.stderr


@pytest.mark.parametrize("checkout", ["full", "shallow"])
def test_the_action_comment_repeats_the_line_above_its_table(checkouts, checkout):
    """The comment is built from worklist's own --json, which the Action saves;
    the job log is the only other place the line went."""
    spec = importlib.util.spec_from_file_location("crapkit_action_comment",
                                                  ROOT / "tools" / "action" / "comment.py")
    comment = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(comment)
    payload = json.loads(run_cli(checkouts[checkout], "worklist", "--json").stdout)

    body = comment.body(None, None, 0, payload, [], 5)

    assert body.count(CHURN_LINE) == (1 if EXPECTED[checkout][1] else 0), body
    assert "| `src/hot.py:1` |" in body
