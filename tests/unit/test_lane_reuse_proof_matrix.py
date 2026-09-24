"""`--reuse-unchanged` reuses a lane exactly when nothing it reads moved, and names what did.

Two proofs, one per lane shape (docs/lanes.md). A lane that lists no `inputs`
is proved by HEAD, a clean working tree, crapkit.toml, its lane table, the
inherited environment and the crapkit version. A lane that lists them is proved
by the tree under them at its stamp's commit against the working tree. Both then
check the digests the stamp recorded, which is what catches the one edit git's
index cannot see: same size, old modification time put back.

Every row runs the coverage command's own lane runner to write the stamp,
applies one event from stale_tree.EVENTS or one of the environment and
by-product rows below, and asks `lane_reuse_verdict`, the decision
`--reuse-unchanged` takes per lane. "" is reuse; any other expectation is a
word the rerun reason must hold, so a reader learns which file or part moved.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import stale_tree
from stale_tree import EVENTS, REL

INPUTS = ("src", "make_cov.py")

# The whole-tree proof reads git's dirty set and HEAD, so every move git sees
# reruns, and a same-size edit git's index misses is caught by the digests.
WHOLE_TREE = {
    "norefresh-control": "", "touch": "", "touch-norefresh": "",
    "crlf-autocrlf-true": "", "crlf-touch-norefresh": "",
    "autocrlf-input-crlf-bytes": "", "autocrlf-input-crlf-bytes-norefresh": "",
    "eol-attr-touch": "", "eol-attr-touch-norefresh": "",
    "ident-filter-touch": "", "ident-filter-touch-norefresh": "",
    "detached-head": "", "fresh-clone": "", "case-only-rename": "",
    "autocrlf-false-crlf-bytes": REL, "same-size-one-tick": "git's index calls them unchanged",
    "content-change": REL, "delete": REL, "rename": REL, "add-in-scope": "src/added.ts",
    "mode-change-staged": REL, "symlink-add": "src/link.ts",
    "shallow-clone-scope-changed": "HEAD is", "shallow-clone-scope-unchanged": "HEAD is",
    "amend-message-only": "HEAD is", "sibling-commit-same-scope-bytes": "HEAD is",
    "git-missing": "git executable not found",
}

# The inputs proof compares trees, not history: an amend or a sibling commit
# with the same inputs reuses, and a clone missing the stamp commit says so.
WITH_INPUTS = {
    **WHOLE_TREE,
    "amend-message-only": "", "sibling-commit-same-scope-bytes": "",
    "shallow-clone-scope-changed": "which this clone does not hold",
    "shallow-clone-scope-unchanged": "which this clone does not hold",
}


def _verdict(root: Path) -> str:
    from crapkit.lanes import lane_reuse_verdict

    return lane_reuse_verdict(root, stale_tree.config(root).lanes[0]).reason


def _after(name: str, tmp_path: Path, monkeypatch, **build_args) -> Path:
    if name == "symlink-add" and not stale_tree.symlinks_work(tmp_path):
        pytest.skip("needs os.symlink: developer mode or elevation on Windows (runs on ubuntu CI)")
    root = EVENTS[name].prepare(tmp_path, **build_args)
    if name == "git-missing":
        monkeypatch.setenv("PATH", str(tmp_path))
    return root


def _expected(name: str, root: Path, table: dict) -> str:
    """A case-only rename is a new file where the filesystem keeps case (ext4)."""
    if name == "case-only-rename" and not (root / REL).exists():
        return "src/App.ts"
    return table[name]


def _judge(reason: str, expected: str) -> None:
    if not expected:
        assert reason == "", reason
        return
    assert expected in reason, reason


@pytest.mark.parametrize("name", sorted(WHOLE_TREE))
def test_a_lane_without_inputs_reuses_only_an_unmoved_tree(name, tmp_path, monkeypatch):
    root = _after(name, tmp_path, monkeypatch)

    _judge(_verdict(root), _expected(name, root, WHOLE_TREE))


@pytest.mark.parametrize("name", sorted(WITH_INPUTS))
def test_a_lane_with_inputs_reuses_only_unmoved_inputs(name, tmp_path, monkeypatch):
    root = _after(name, tmp_path, monkeypatch, inputs=INPUTS)

    _judge(_verdict(root), _expected(name, root, WITH_INPUTS))


def test_a_touch_under_the_inputs_while_measuring_still_stamps_a_proof(tmp_path):
    """diff.autoRefreshIndex=false and a same-bytes touch before the run: the
    proof taken as the lane starts used to read the touch as an edit and stamp
    no proof, so reuse was lost until the next clean rerun."""
    root = stale_tree.build(tmp_path / "repo", inputs=INPUTS)
    stale_tree.git(root, "config", "diff.autoRefreshIndex", "false")
    stale_tree.touch(root / REL)
    stale_tree.measure(root)

    assert stale_tree.stamp(root)["coverage/coverage-final.json"]["proof"]
    assert _verdict(root) == ""


# --- the lane's own by-products ----------------------------------------------

@pytest.mark.parametrize("inputs", [(), INPUTS], ids=["whole-tree", "inputs"])
@pytest.mark.parametrize("byproduct", [".coverage", "src/__pycache__/app.cpython-311.pyc"])
def test_a_file_the_lane_writes_does_not_void_its_own_proof(byproduct, inputs, tmp_path):
    """.gitignore holds only `.crapkit/`, which is what `crapkit init` writes. A
    pytest-cov lane leaves `.coverage` at the root and a python lane with
    bytecode on leaves `__pycache__` under its scopes; the stamp said "measured
    with uncommitted changes" and the lane never reused again."""
    root = stale_tree.measure(stale_tree.build(tmp_path / "repo", byproduct=byproduct,
                                               inputs=inputs, ignore=".crapkit/\n"))

    stamp = stale_tree.stamp(root)["coverage/coverage-final.json"]
    assert stamp["byproducts"] == [byproduct] and stamp["proof"]
    assert _verdict(root) == ""


def test_a_by_product_still_names_every_other_untracked_file(tmp_path):
    root = stale_tree.measure(stale_tree.build(tmp_path / "repo", byproduct=".coverage",
                                               ignore=".crapkit/\n"))
    stale_tree.write(root / "notes.txt", "draft\n")

    assert _verdict(root) == "the working tree has 1 uncommitted change(s): notes.txt"


# --- the rest of the whole-tree proof ------------------------------------------

@pytest.mark.parametrize("variable", ["SSH_AUTH_SOCK", "TMUX", "VSCODE_GIT_IPC_HANDLE"])
def test_a_session_socket_moving_between_runs_reruns_nothing(variable, tmp_path, monkeypatch):
    monkeypatch.setenv(variable, "/tmp/session-one")
    root = stale_tree.measure(stale_tree.build(tmp_path / "repo"))
    monkeypatch.setenv(variable, "/tmp/session-two")

    assert _verdict(root) == ""


def test_any_other_inherited_variable_moving_is_named(tmp_path, monkeypatch):
    monkeypatch.setenv("CRAPKIT_MATRIX_MODE", "a")
    root = stale_tree.measure(stale_tree.build(tmp_path / "repo"))
    monkeypatch.setenv("CRAPKIT_MATRIX_MODE", "b")

    assert _verdict(root) == "1 environment variable(s) changed: CRAPKIT_MATRIX_MODE"


def test_a_switch_from_powershell_to_git_bash_reruns_and_names_the_variables(tmp_path,
                                                                             monkeypatch):
    """Kept on purpose: PATHEXT decides what cmd.exe starts for a lane's first
    word, so two shells that disagree on it can run different programs."""
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD;.CPL")
    monkeypatch.setenv("PSModulePath", "C:/modules")
    root = stale_tree.measure(stale_tree.build(tmp_path / "repo"))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    monkeypatch.delenv("PSModulePath")

    reason = _verdict(root)

    assert "2 environment variable(s) changed" in reason and "PATHEXT" in reason


def test_crapkit_toml_checked_out_again_as_crlf_is_the_same_config(tmp_path):
    root = stale_tree.measure(stale_tree.build(tmp_path / "repo"))
    stale_tree.git(root, "config", "core.autocrlf", "true")
    stale_tree.reverted_checkout(root, "crapkit.toml")
    assert b"\r\n" in (root / "crapkit.toml").read_bytes()

    assert _verdict(root) == ""


def test_a_crapkit_toml_edit_is_named(tmp_path):
    root = stale_tree.measure(stale_tree.build(tmp_path / "repo"))
    with (root / "crapkit.toml").open("ab") as config:
        config.write(b"# a comment\n")
    stale_tree.git(root, "commit", "-qam", "comment")

    assert "HEAD is" in _verdict(root)


def test_another_crapkit_version_reruns_the_lane(tmp_path, monkeypatch):
    """A crapkit that parses or scores an artifact differently must read it
    fresh; the version was outside the proof, so an upgrade reused every lane."""
    import crapkit.lanes

    root = stale_tree.measure(stale_tree.build(tmp_path / "repo"))
    monkeypatch.setattr(crapkit.lanes, "__version__", "99.0.0")

    assert _verdict(root) == "the crapkit version changed"


def test_an_ignored_file_the_suite_reads_stays_outside_the_proof(tmp_path):
    """Documented in docs/lanes.md: ignored inputs other than crapkit.toml,
    tools outside the repository and services are outside the proof, and a
    change there needs a fresh run by hand."""
    root = stale_tree.measure(stale_tree.build(
        tmp_path / "repo", extra={"local.settings": "mode=a\n"},
        ignore=".crapkit/\ncoverage/\nlocal.settings\n"))
    stale_tree.write(root / "local.settings", "mode=b\n")

    assert _verdict(root) == ""


def test_an_inherited_variable_stays_outside_an_inputs_lane_proof(tmp_path, monkeypatch):
    """Documented in docs/configuration.md: an inputs lane is proved by its
    paths, its lane table and its own `env`, so it reuses across shells; a
    variable its command reads belongs in `env`."""
    monkeypatch.setenv("CRAPKIT_MATRIX_MODE", "a")
    root = stale_tree.measure(stale_tree.build(tmp_path / "repo", inputs=INPUTS))
    monkeypatch.setenv("CRAPKIT_MATRIX_MODE", "b")

    assert _verdict(root) == ""


def _commit_file(root: Path, rel: str, text: str) -> None:
    stale_tree.write(root / rel, text)
    stale_tree.git(root, "add", "-A")
    stale_tree.git(root, "commit", "-q", "-m", f"add {rel}")


def test_an_inputs_lane_reuses_across_a_rebase_onto_a_change_outside_them(tmp_path):
    """Measured on a feature commit, then rebased onto an upstream commit that
    touched only README.md: HEAD no longer descends from the stamp's commit,
    and the tree under the inputs is the one the lane measured."""
    root = stale_tree.build(tmp_path / "repo", inputs=INPUTS)
    base = stale_tree.git(root, "rev-parse", "HEAD").strip()
    _commit_file(root, "NOTES.md", "feature\n")
    stale_tree.measure(root)
    stale_tree.git(root, "checkout", "-q", "-b", "upstream", base)
    _commit_file(root, "README.md", "upstream\n")
    stale_tree.git(root, "checkout", "-q", "main")
    stale_tree.git(root, "rebase", "-q", "upstream")

    assert _verdict(root) == ""


# --- edits git's own diff does not compare ---------------------------------------

@pytest.mark.parametrize("inputs", [(), INPUTS], ids=["whole-tree", "inputs"])
@pytest.mark.parametrize("flag", ["--skip-worktree", "--assume-unchanged"])
def test_an_edit_to_a_file_git_was_told_to_skip_reruns_the_lane(flag, inputs, tmp_path):
    """make_cov.py stands in for a test file: outside the scopes, so no source
    digest covers it, and read by the lane's command."""
    root = stale_tree.measure(stale_tree.build(tmp_path / "repo", inputs=inputs))
    stale_tree.git(root, "update-index", flag, "make_cov.py")
    with (root / "make_cov.py").open("ab") as script:
        script.write(b"# edited\n")

    assert "make_cov.py" in _verdict(root)


@pytest.mark.parametrize("inputs", [(), (*INPUTS, "sub")], ids=["whole-tree", "inputs"])
def test_an_edit_inside_a_submodule_git_ignores_when_dirty_reruns_the_lane(inputs, tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    stale_tree.git(library, "init", "-q", "-b", "main")
    stale_tree.write(library / "lib.py", "def f():\n    return 1\n")
    stale_tree.git(library, "add", "-A")
    stale_tree.git(library, "commit", "-q", "-m", "lib")
    root = stale_tree.build(tmp_path / "repo", inputs=inputs)
    stale_tree.git(root, "-c", "protocol.file.allow=always", "submodule", "--quiet", "add",
                   library.as_uri(), "sub")
    stale_tree.git(root, "config", "-f", ".gitmodules", "submodule.sub.ignore", "dirty")
    stale_tree.git(root, "commit", "-q", "-am", "sub")
    stale_tree.measure(root)
    stale_tree.write(root / "sub" / "lib.py", "def f():\n    return 2\n")

    assert "sub" in _verdict(root).split(": ")[-1]
