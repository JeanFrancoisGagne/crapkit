"""A store that exists but holds no run with rows says so, not `no snapshot`.

A commit gate override writes a hook run, which scores nothing. On a store that
holds only such runs, `duplication` and `worklist` said `no snapshot in <root>`
while `.crapkit/crap.sqlite` sat on disk, and `next-item` on the same store said
`no scored run`. The command each one names is right; the missing thing was
misnamed.
"""
from hand_scored_repo import make_repo, run, write_run


def _hook_only(tmp_path):
    root = make_repo(tmp_path / "repo")
    write_run(root, [], kind="hook")
    return root


def test_duplication_names_the_missing_run_not_a_missing_store(tmp_path, capsys):
    root = _hook_only(tmp_path)

    code, _, err = run(root, capsys, "duplication")

    assert code == 1
    assert err.startswith(f"crapkit: no run with rows in {root} — run `"), err
    assert "inventory` first" in err and "no snapshot" not in err, err


def test_worklist_names_the_missing_run_not_a_missing_store(tmp_path, capsys):
    root = _hook_only(tmp_path)

    code, _, err = run(root, capsys, "worklist")

    assert code == 1
    assert err.startswith(f"crapkit: no run with rows in {root} — run `"), err
    assert "coverage` first" in err and "no snapshot" not in err, err


def test_a_repo_with_no_store_still_says_no_snapshot(tmp_path, capsys):
    root = make_repo(tmp_path / "repo")

    for command in ("duplication", "worklist"):
        code, _, err = run(root, capsys, command)
        assert code == 1
        assert err.startswith(f"crapkit: no snapshot in {root}"), (command, err)
