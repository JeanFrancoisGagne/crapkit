"""kit.goldens: normalized goldens, the lock over them, and the rule that a golden
changes only together with a declared change."""
import os
from pathlib import Path
import shutil
import sys

import pytest

import hang_guard
from accuracy.kit import corpus_run, drive, goldens, repos

KIT_RELATIVE = Path("tests") / "accuracy" / "kit" / "fixtures"
RUN = goldens.REPO / "tools" / "accuracy" / "run.py"
PATTERNS = goldens.SEED_PATTERNS


@pytest.fixture(scope="module")
def seed_goldens(tmp_path_factory):
    run = corpus_run.measure(corpus_run.SEED, tmp_path_factory.mktemp("goldens"))
    return goldens.goldens_of(run)


@pytest.mark.process
@pytest.mark.golden
def test_the_seed_run_equals_its_committed_goldens(seed_goldens):
    problems = goldens.compare(goldens.SEED_GOLDENS, seed_goldens)

    assert problems == [], "\n".join([*problems, goldens.SEED_FIX])


@pytest.mark.nightly
@pytest.mark.process
def test_two_runs_in_two_places_give_one_set_of_goldens(seed_goldens, tmp_path):
    elsewhere = corpus_run.measure(corpus_run.SEED, tmp_path / "some where")

    assert goldens.goldens_of(elsewhere) == seed_goldens


@pytest.mark.nightly
@pytest.mark.process
def test_goldens_hold_no_root_no_host_and_no_clock(seed_goldens, tmp_path_factory):
    text = "\n".join(seed_goldens.values())

    assert str(tmp_path_factory.getbasetemp()) not in text
    assert "<root>/repo/.crapkit/crap.sqlite" in text
    assert '"resources": "<resources>"' in seed_goldens["doctor.json"]
    assert '"created_at": "<created_at>"' in seed_goldens["runs.json"]
    assert "<time>" in seed_goldens["report.html"]
    assert not [name for name in seed_goldens if name.endswith(".stderr")]


def _stub_launcher(directory: Path, version: str) -> Path:
    """A crapkit launcher that answers `crapkit VERSION`: a batch file on
    Windows, an executable shell script elsewhere."""
    directory.mkdir(parents=True)
    if os.name == "nt":
        (directory / "crapkit.bat").write_bytes(f"@echo crapkit {version}\r\n".encode("ascii"))
    else:
        script = directory / "crapkit"
        script.write_bytes(f"#!/bin/sh\necho crapkit {version}\n".encode("ascii"))
        script.chmod(0o755)
    return directory


def _doctor_golden(repo_templates, top: Path) -> tuple[str, str]:
    """(what doctor --json printed, its golden form) on a fresh seed repo under `top`."""
    root = repo_templates.copy(repos.tree_spec(corpus_run.SEED), top / "repo").root
    printed = drive.Driver(root, spawn=True).run("doctor", "--json").stdout
    outputs = top / "outputs"
    outputs.mkdir()
    (outputs / "doctor.json").write_text(printed, encoding="utf-8")
    return printed, goldens.goldens_of(corpus_run.CorpusRun(root, outputs, 0, {}))["doctor.json"]


@pytest.mark.process
def test_the_host_s_other_crapkit_installs_move_no_golden(repo_templates, tmp_path, monkeypatch):
    """Doctor warns when PATH holds crapkit launchers that answer different
    versions. That describes the machine the suite runs on, not the corpus, so
    a developer's pipx or uv tool install of another release must leave
    doctor.json's golden as it is on a machine with one crapkit."""
    _, alone = _doctor_golden(repo_templates, tmp_path / "alone")
    stubs = [_stub_launcher(tmp_path / name / "bin", version)
             for name, version in (("old", "0.0.1"), ("new", "0.0.2"))]
    monkeypatch.setenv("PATH", os.pathsep.join([*map(str, stubs), os.environ.get("PATH", "")]))
    printed, crowded = _doctor_golden(repo_templates, tmp_path / "crowded")

    assert "crapkit launchers" in printed, "doctor never saw the two stub launchers"
    assert crowded == alone


def test_the_committed_seed_goldens_match_their_lock():
    changes = goldens.read_changes(goldens.SEED_CHANGES)

    assert goldens.check(goldens.SEED_LOCK, goldens.REPO, PATTERNS, changes,
                         goldens.SEED_FIX) == []


# --- the lock on a copy of the kit's tree -----------------------------------------

@pytest.fixture
def tree(tmp_path):
    """A copy of the kit's seed goldens, lock and changes at their repo paths."""
    shutil.copytree(goldens.REPO / KIT_RELATIVE, tmp_path / KIT_RELATIVE)
    return tmp_path


def _paths(base: Path):
    fixtures = base / KIT_RELATIVE
    return fixtures / "seed-goldens.lock", fixtures / "seed-changes.tsv", fixtures / "seed-goldens"


def _check(base: Path) -> list[str]:
    lock, changes, _ = _paths(base)
    return goldens.check(lock, base, PATTERNS, goldens.read_changes(changes))


def _edit(base: Path, name: str = "scored.tsv") -> str:
    target = _paths(base)[2] / name
    target.write_bytes(target.read_bytes().replace(b"\t", b"\t9", 1))
    return f"{KIT_RELATIVE.as_posix()}/seed-goldens/{name}"


# The tests declare under T ids: the kit's own seed-changes.tsv grows K rows,
# and a test id must never collide with one.
def _declare(base: Path, change_id: str = "T1", kind: str = "fix") -> list[str]:
    lock, changes, _ = _paths(base)
    change = {"id": change_id, "date": "2026-09-24", "kind": kind, "calcs": "CRAP score",
              "reason": "the seed's CRAP moved"}
    return goldens.declare(lock, base, PATTERNS, change, changes)


def test_an_untouched_copy_passes(tree):
    assert _check(tree) == []


def test_an_undeclared_golden_change_fails_and_names_the_fix(tree):
    path = _edit(tree)

    problems = _check(tree)

    assert len(problems) == 1
    assert problems[0].startswith(f"{path} changed with no declared change")
    assert goldens.FIX in problems[0]


def test_a_declared_golden_change_passes(tree):
    path = _edit(tree)
    before = goldens.read_lock(_paths(tree)[0])

    assert _declare(tree) == [path]

    lock = goldens.read_lock(_paths(tree)[0])
    assert _check(tree) == []
    assert lock[path] != before[path] and lock[path][1] == "T1"
    assert goldens.read_changes(_paths(tree)[1])["T1"]["kind"] == "fix"


def test_a_relock_under_an_old_change_fails_against_the_base(tree):
    lock_path, changes_path, _ = _paths(tree)
    base_lock, base_changes = goldens.read_lock(lock_path), goldens.read_changes(changes_path)
    path = _edit(tree)
    rows = dict(base_lock)
    rows[path] = (goldens.scan(tree, PATTERNS)[path], base_lock[path][1])
    goldens.write_lock(lock_path, rows)

    assert _check(tree) == []
    problems = goldens.against_base(base_lock, rows, base_changes, base_changes)
    assert [problem.split(" ")[0] for problem in problems] == [path]


def test_a_declared_change_passes_against_the_base(tree):
    lock_path, changes_path, _ = _paths(tree)
    base_lock, base_changes = goldens.read_lock(lock_path), goldens.read_changes(changes_path)
    _edit(tree)
    _declare(tree)

    assert goldens.against_base(base_lock, goldens.read_lock(lock_path), base_changes,
                                goldens.read_changes(changes_path)) == []


def test_a_new_golden_needs_a_lock_row(tree):
    (_paths(tree)[2] / "extra.json").write_bytes(b"{}\n")

    assert [p.split(" ")[0] for p in _check(tree)] == [
        f"{KIT_RELATIVE.as_posix()}/seed-goldens/extra.json"]
    assert "is not in the lock" in _check(tree)[0]


def test_a_removed_golden_needs_a_declared_change(tree):
    (_paths(tree)[2] / "trend.json").unlink()

    assert "is locked but gone" in _check(tree)[0]
    _declare(tree, "T2", "none")
    assert _check(tree) == []


def test_a_lock_row_naming_no_change_fails(tree):
    lock_path = _paths(tree)[0]
    rows = goldens.read_lock(lock_path)
    first = sorted(rows)[0]
    rows[first] = (rows[first][0], "T99")
    goldens.write_lock(lock_path, rows)

    assert _check(tree) == [f"{first} names change T99, which no CHANGES row declares"]


def test_declare_refuses_a_reused_id_an_unknown_kind_and_nothing_to_declare(tree):
    with pytest.raises(goldens.ChangeControlError, match="nothing moved"):
        _declare(tree)
    _edit(tree)
    with pytest.raises(goldens.ChangeControlError, match="K1 is already declared"):
        _declare(tree, "K1")
    with pytest.raises(goldens.ChangeControlError, match="kind 'tweak'"):
        _declare(tree, "T1", "tweak")


def _stale(base: Path) -> str:
    """The tree an older crapkit left: one golden and its lock row agree on other bytes."""
    path = _edit(base)
    lock_path = _paths(base)[0]
    rows = goldens.read_lock(lock_path)
    rows[path] = (goldens.scan(base, PATTERNS)[path], rows[path][1])
    goldens.write_lock(lock_path, rows)
    return path


@pytest.mark.nightly
@pytest.mark.process
def test_kit_goldens_rewrites_a_moved_seed_golden_and_declares_it(tree):
    path = _stale(tree)
    assert _check(tree) == []

    done = hang_guard.run([sys.executable, str(RUN), "kit-goldens", "--declare", "T1", "--kind",
                           "fix", "--reason", "the seed's output moved", "--base", str(tree)],
                          cwd=goldens.REPO, text=True, encoding="utf-8", errors="replace")

    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == f"relocked {path} under T1"
    assert _check(tree) == []
    assert (tree / path).read_bytes() == (goldens.REPO / path).read_bytes()
    assert goldens.read_lock(_paths(tree)[0])[path][1] == "T1"


@pytest.mark.nightly
@pytest.mark.process
def test_kit_goldens_refuses_when_nothing_moved(tree):
    done = hang_guard.run([sys.executable, str(RUN), "kit-goldens", "--declare", "T1", "--kind",
                           "none", "--reason", "nothing", "--base", str(tree)],
                          cwd=goldens.REPO, text=True, encoding="utf-8", errors="replace")

    assert done.returncode == 1
    assert "nothing moved since the lock" in done.stderr


# --- the store --------------------------------------------------------------------

def test_compare_names_missing_extra_and_the_first_differing_line(tmp_path):
    goldens.write(tmp_path, {"a.txt": "one\ntwo\n", "b.txt": "gone\n"})

    problems = goldens.compare(tmp_path, {"a.txt": "one\nTWO\n", "c.txt": "new\n"})

    assert problems == ["a.txt: line 2 differs\n  golden: two\n  now:    TWO",
                        "b.txt: a golden with no output behind it",
                        "c.txt: an output with no golden"]


def test_write_replaces_the_directory_and_keeps_bytes(tmp_path):
    goldens.write(tmp_path, {"old.txt": "x\n"})
    goldens.write(tmp_path, {"new.txt": "a\r\nb\n"})

    assert sorted(p.name for p in tmp_path.iterdir()) == ["new.txt"]
    assert (tmp_path / "new.txt").read_bytes() == b"a\r\nb\n"


def test_changes_refuse_a_duplicate_id_and_an_unknown_kind(tmp_path):
    header = "\t".join(goldens.CHANGE_COLUMNS)
    path = tmp_path / "CHANGES.tsv"
    path.write_text(f"{header}\nK1\t2026-09-24\tnone\t\t\t\t\tseed\n"
                    f"K1\t2026-09-24\tnone\t\t\t\t\tagain\n", encoding="utf-8")
    with pytest.raises(goldens.ChangeControlError, match="K1 appears twice"):
        goldens.read_changes(path)

    path.write_text(f"{header}\nK1\t2026-09-24\tmaybe\t\t\t\t\tseed\n", encoding="utf-8")
    with pytest.raises(goldens.ChangeControlError, match="kind 'maybe'"):
        goldens.read_changes(path)
