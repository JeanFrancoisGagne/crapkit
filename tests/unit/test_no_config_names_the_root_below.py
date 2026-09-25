"""Where no crapkit.toml is, the refusal names the one that sits below.

Git runs a pre-commit hook from the repository top, and a CI step starts there.
In a monorepo whose crapkit.toml sits in packages/api, README's hook line
refused every commit with `no crapkit.toml at <top> - nothing to analyze`: the
directory crapkit looked in, and not a word about the one it wanted, because
the walk goes up from where the caller stands (ADR 0002), never down.
"""
import re
import subprocess
from pathlib import Path

import pytest

from crapkit.cli._shared import _load_repo_config
from crapkit.cli.parser import build_parser
from crapkit.errors import ConfigError

ROOT = Path(__file__).resolve().parents[2]


def _repo(top: Path, *configs: str) -> Path:
    """A repository holding a tracked crapkit.toml in each of `configs`."""
    top.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=top, check=True)
    for directory in configs:
        (top / directory).mkdir(parents=True)
        (top / directory / "crapkit.toml").write_text("[crapkit]\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=top, check=True)
    return top


def _refusal(root: Path) -> str:
    with pytest.raises(ConfigError) as refused:
        _load_repo_config(root)
    return str(refused.value)


def test_the_one_below_is_named_with_the_flag_that_reaches_it(tmp_path, monkeypatch):
    top = _repo(tmp_path / "repo", "packages/api")
    monkeypatch.chdir(top)

    message = _refusal(top)

    assert message == (f"no crapkit.toml at {top} - nothing to analyze; "
                       "packages/api/crapkit.toml sits below it: pass --repo packages/api")
    assert message.isascii()


def test_a_root_named_from_elsewhere_is_spelled_from_where_the_caller_stands(tmp_path, monkeypatch):
    """`--repo repo` from the parent: the flag the refusal hands back has to
    work from the same place."""
    top = _repo(tmp_path / "repo", "packages/api")
    monkeypatch.chdir(tmp_path)

    assert _refusal(top).endswith("pass --repo repo/packages/api")


def test_several_below_are_named_and_the_choice_is_the_callers(tmp_path, monkeypatch):
    top = _repo(tmp_path / "repo", "packages/web", "packages/api", "tools/lint", "tools/zz")
    monkeypatch.chdir(top)

    assert _refusal(top).endswith("; crapkit.toml sits below it in packages/api, packages/web, "
                                  "tools/lint and 1 more: pass --repo with the one to score")


def test_an_untracked_config_or_no_repository_names_no_root_below(tmp_path, monkeypatch):
    """The read is the index: an untracked file costs a walk of the working
    tree, and outside a repository there is no index to read. With no root
    below, the refusal names `init` and --repo instead."""
    top = _repo(tmp_path / "repo")
    (top / "packages" / "api").mkdir(parents=True)
    (top / "packages" / "api" / "crapkit.toml").write_text("[crapkit]\n", encoding="utf-8")
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.chdir(tmp_path)

    for root in (top, bare):
        refusal = _refusal(root)
        assert refusal.startswith(f"no crapkit.toml at {root} - nothing to analyze; run `"), refusal
        assert "init` there to adopt it, or pass --repo DIR" in refusal, refusal
        assert "sits below it" not in refusal, refusal


def test_a_root_that_is_a_file_is_refused_the_way_it_was(tmp_path, monkeypatch):
    """`--repo notes.txt` names no directory to list; the refusal must not
    turn into git's traceback."""
    top = _repo(tmp_path / "repo", "packages/api")
    (top / "notes.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.chdir(top)

    assert _refusal(top / "notes.txt") == f"no crapkit.toml at {top / 'notes.txt'} - nothing to analyze"


# --- what the pages tell a monorepo to write ------------------------------------

@pytest.mark.parametrize("page", ["README.md", "docs/lanes.md"])
def test_the_pages_quote_the_refusal_the_gate_prints_at_the_top(tmp_path, monkeypatch, page):
    top = _repo(tmp_path / "repo", "packages/api")
    monkeypatch.chdir(top)
    after = _refusal(top).split(" - nothing to analyze", 1)[1]

    text = " ".join((ROOT / page).read_text(encoding="utf-8").split())

    assert f"no crapkit.toml at /repo - nothing to analyze{after}" in text


def test_route_three_args_reach_the_gate_as_its_repo_flag():
    """The framework appends `args` to the manifest's entry and runs it from
    the top; the README's list has to parse as the flag that names the root."""
    yaml = pytest.importorskip("yaml")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    spelled = re.search(r"`args: (\[[^]]*\])`", readme)
    assert spelled, "README names no args line for Route 3"
    entry = yaml.safe_load((ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8"))[0]["entry"]

    argv = entry.split()[1:] + yaml.safe_load(spelled.group(1))

    assert build_parser().parse_args(argv).repo == "packages/api"
