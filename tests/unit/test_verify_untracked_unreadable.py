"""verify over an untracked file whose name git gives in bytes that are not UTF-8.

verify judges git-tracked files only; an untracked file in a scope is named as
not judged, never scored. The scope assignment refuses a claimed name that is
not UTF-8 with exit 3, and verify once sent its untracked names through that
assignment, so a stray Latin-1 file nobody added stopped every verify. An
untracked name is neither left out nor refused: verify names it as not judged,
in its `\\xNN` spelling, like any other untracked file.

Windows cannot hold such a name, so git's listing is replaced here with the
surrogateescape spelling gitpaths hands over on Linux; the Linux e2e row
`test_an_untracked_name_is_neither_left_out_nor_refused` covers the real tree.
"""
import json

from cli_inproc_repo import repo, seed_artifacts, template_repo  # noqa: F401

import pytest

from crapkit import gitio
from crapkit.cli import main

LATIN1 = b"caf\xe9".decode("utf-8", "surrogateescape")


def run(argv: list[str], repo, capsys) -> tuple[int, str, str]:
    code = main([*argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture()
def baselined(repo, capsys):
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


@pytest.mark.parametrize("names, listed", [
    ([f"src/{LATIN1}.ts"], ["src/caf\\xe9.ts"]),
    ([f"src/{LATIN1}.ts", "src/added.ts"], ["src/added.ts", "src/caf\\xe9.ts"]),
    ([f"{LATIN1}.log", f"src/{LATIN1}.md"], []),
    (["src/added.ts"], ["src/added.ts"]),
], ids=["scoped-latin1", "beside-a-readable-name", "no-scope-takes-it", "readable-control"])
def test_an_untracked_name_that_is_not_utf8_is_named_as_not_judged(baselined, capsys, monkeypatch,
                                                                   names, listed):
    monkeypatch.setattr(gitio, "untracked_files", lambda root: list(names))

    code, out, err = run(["verify", "--reuse-artifacts", "--json"], baselined, capsys)

    assert code == 0, err
    assert json.loads(out)["untracked_in_scope"] == listed
    assert "is in scope" not in err and "crapkit: left out" not in err, err
    if listed:
        assert f"({', '.join(listed)}): verify scores git-tracked files only" in err, err
