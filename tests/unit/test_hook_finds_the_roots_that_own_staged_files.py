"""The commit gate finds the crapkit root that owns each staged file, and says
what it did when none does.

git runs a pre-commit hook at the repository's top. With crapkit.toml in
packages/api, README Route 2's hook, and the handbook's, ran there, found no
configuration at or above the top, and refused every commit with `no
crapkit.toml at TOP - nothing to analyze`, a docs-only commit included. A gate
armed before `crapkit init` refused every commit the same way. That line named
neither `crapkit init` nor `--repo`.
"""
import shutil
from pathlib import Path

import pytest
from cli_inproc_repo import add_knotty, commit_all, git, repo, template_repo  # noqa: F401

from crapkit.cli import main


@pytest.fixture(autouse=True)
def in_a_commit(monkeypatch):
    """git sets GIT_INDEX_FILE for the hooks `git commit` runs."""
    monkeypatch.setenv("GIT_INDEX_FILE", ".git/index")


def run(argv: list[str], cwd: Path, capsys, monkeypatch) -> tuple[int, str, str]:
    monkeypatch.chdir(cwd)
    code = main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture()
def mono(template_repo: Path, tmp_path: Path) -> Path:
    """The template repo's tree adopted in packages/api, one repository at the top,
    and a packages/web with no configuration."""
    top = tmp_path / "mono"
    shutil.copytree(template_repo, top / "packages" / "api", ignore=shutil.ignore_patterns(".git"))
    (top / "packages" / "web").mkdir()
    (top / "packages" / "web" / "index.js").write_text("function f(a) { return a }\n",
                                                        encoding="utf-8")
    git(top, "init", "-q")
    commit_all(top, "mono")
    return top


def test_a_docs_only_commit_at_the_top_passes_with_one_note(mono, capsys, monkeypatch):
    (mono / "NOTES.md").write_text("# notes\n", encoding="utf-8")
    git(mono, "add", "NOTES.md")

    code, out, err = run(["hook-precommit"], mono, capsys, monkeypatch)

    assert (code, out) == (0, ""), err
    assert "no staged file sits under a crapkit.toml" in err, err
    assert "no crapkit.toml at" not in err, err


def test_a_breach_staged_below_the_top_is_refused_by_the_root_that_owns_it(mono, capsys, monkeypatch):
    add_knotty(mono / "packages" / "api")
    git(mono, "add", "-A")

    code, out, err = run(["hook-precommit"], mono, capsys, monkeypatch)

    assert code == 6, out + err
    assert "crapkit gate: 1 staged function(s) exceed the complexity ceiling of 6:" in out, out
    assert "ccn   8  packages/api/src/app.ts:" in out and "knotty" in out, out
    assert "decompose before committing" in out, out
    assert f"using crapkit.toml at {(mono / 'packages' / 'api').resolve()}" in err, err


def test_a_gate_armed_before_init_passes_and_names_init(repo, capsys, monkeypatch):
    (repo / "crapkit.toml").unlink()
    (repo / "NOTES.md").write_text("notes\n", encoding="utf-8")
    git(repo, "add", "-A")

    code, _, err = run(["hook-precommit"], repo, capsys, monkeypatch)

    assert code == 0, err
    assert "crapkit init" in err, err


def test_a_command_run_beside_the_root_names_init_repo_and_the_root_it_missed(
        mono, capsys, monkeypatch):
    code, _, err = run(["next-item"], mono / "packages" / "web", capsys, monkeypatch)

    assert code == 3, err
    assert "nothing to analyze" in err and "init` there" in err and "--repo" in err, err
    assert "../api/crapkit.toml" in err, err


def test_an_explicit_repo_with_no_configuration_is_still_refused(mono, capsys, monkeypatch):
    code, _, err = run(["hook-precommit", "--repo", "packages/web"], mono, capsys, monkeypatch)

    assert code == 3, err
    assert "no crapkit.toml at" in err and "init` there" in err, err
