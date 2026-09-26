r"""Every spelling of one file argument names the file git names.

`_repo_relative` read `./`, backslashes and absolute paths, but kept the letter
case the user typed and took `/c/...`, `/mnt/c/...` and `\\localhost\C$\...` as
paths somewhere else. On a case-insensitive disk the file still opened, so the
wrong-case key went on: `rescore --gate SRC\app.ts` judged 0 functions and
passed a file that fails spelled `src/app.ts`, `test-scoped` refused a file its
scope declares, `brief` and `explain` found nothing, `ratchet move` filed a mark
under a key no row carries, and `mutate --files` called an in-scope file outside
the corpus. Every command here reads its path through the same door, so each
spelling is fed through the commands that act on it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli_inproc_repo import (add_knotty, commit_all, git, repo,  # noqa: F401
                             seed_artifacts, template_repo)
from crapkit.cli import main
from crapkit.cli._shared import _command_root
from crapkit.cli.analyses import _mutation_targets

from path_spellings import (SPELLINGS, WINDOWS, admin_share, lower_drive, need,
                            need_case_sensitive, only_posix, short_name)
from path_spellings import spelled as _spelled

_need = need


def run(argv: list[str], root: Path, capsys) -> tuple[int, str, str]:
    code = main([*argv, "--repo", str(root)])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture()
def scored(repo, capsys):  # noqa: F811
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


@pytest.mark.parametrize("which", SPELLINGS)
def test_the_gate_fails_the_breach_whatever_the_spelling(scored, capsys, which):
    add_knotty(scored)

    code, _, err = run(["rescore", _spelled(which, scored), "--gate"], scored, capsys)

    assert code == 6, err
    assert "knotty" in err and "src/app.ts" in err, err


@pytest.mark.parametrize("which", SPELLINGS)
def test_test_scoped_routes_every_spelling_to_its_scope(scored, capsys, which):
    code, _, err = run(["test-scoped", _spelled(which, scored)], scored, capsys)

    assert code == 0, err


@pytest.mark.parametrize("which", SPELLINGS)
def test_brief_opens_the_packet_whatever_the_spelling(scored, capsys, which):
    code, out, err = run(["brief", _spelled(which, scored), "dispatch", "--json"], scored, capsys)

    assert code == 0, err
    assert json.loads(out)["path"] == "src/app.ts"


@pytest.mark.parametrize("which", SPELLINGS)
def test_explain_finds_the_function_whatever_the_spelling(scored, capsys, which):
    code, out, err = run(["explain", _spelled(which, scored), "dispatch", "--json"], scored,
                         capsys)

    assert code == 0, err
    assert "src/app.ts" in out


@pytest.mark.parametrize("which", SPELLINGS)
def test_mutate_files_names_the_corpus_path_whatever_the_spelling(scored, which):
    assert list(_mutation_targets(scored, [_spelled(which, scored)])) == ["src/app.ts"]


def _marks(root: Path) -> list[str]:
    return (root / "crapkit-ratchet.tsv").read_text(encoding="utf-8").splitlines()


MARKS = ("# crapkit-analysis=11 lizard=1.24.0\n# crapkit-keys=1\npath\tlong_name\tcrap\n"
         "src/app.ts\tdispatch( kind : string )\t40.0000\n")


@pytest.mark.parametrize("new", ["src/moved.ts", "./src/moved.ts", "src\\moved.ts", "SRC/moved.ts",
                                 "src/Moved.ts", "SRC\\moved.ts", "src\\MOVED.ts"])
def test_ratchet_move_files_the_mark_under_the_moved_file_git_names(repo, capsys, new):  # noqa: F811
    """After `git mv src/app.ts src/moved.ts`, NEW in another case opened the
    moved file and wrote `SRC/moved.ts` into the committed marks: no row
    carries that key, so the function ran unmarked from then on."""
    _need(("windows" if "\\" in new else "") + (" case" if new.lower() != new else ""), repo)
    (repo / "crapkit-ratchet.tsv").write_text(MARKS, encoding="utf-8")
    (repo / "src" / "app.ts").rename(repo / "src" / "moved.ts")

    code, _, err = run(["ratchet", "move", "src/app.ts", new], repo, capsys)

    assert code == 0, err
    assert _marks(repo)[3:] == ["src/moved.ts\tdispatch( kind : string )\t40.0000"]


@pytest.mark.parametrize("new", ["lib/", "./lib/", "lib\\", "LIB/", "Lib\\", "LIB\\"])
def test_ratchet_move_files_a_directory_mark_under_the_directory_git_names(repo, capsys,  # noqa: F811
                                                                           new):
    """`ratchet move src/ LIB/` into an existing lib/ wrote `LIB/app.ts` into
    the committed marks, a key no scored row carries."""
    _need(("windows" if "\\" in new else "") + (" case" if new.lower() != new else ""), repo)
    (repo / "crapkit-ratchet.tsv").write_text(MARKS, encoding="utf-8")
    (repo / "lib").mkdir()

    code, _, err = run(["ratchet", "move", "src/", new], repo, capsys)

    assert code == 0, err
    assert _marks(repo)[3:] == ["lib/app.ts\tdispatch( kind : string )\t40.0000"]


@pytest.mark.parametrize("which", ["msys", "wsl", "absolute-lower-drive", "admin-share"])
def test_a_repo_flag_in_any_windows_spelling_serves_the_checkout(scored, capsys, which):
    """`worklist --repo /c/...` was read as C:\\c\\..., which holds no crapkit.toml."""
    repo_spelling = _spelled(which, scored).rsplit("/src/", 1)[0].rsplit("\\src\\", 1)[0]

    assert main(["worklist", "--repo", repo_spelling]) == 0, capsys.readouterr().err


ROOTS = {
    "extended-length": lambda root: "\\\\?\\" + str(root.resolve()),
    "extended-share": lambda root: "\\\\?\\UNC\\" + admin_share(root)[2:],
    "admin-share": admin_share,
}


@pytest.mark.skipif(not WINDOWS, reason="needs Windows path rules")
@pytest.mark.parametrize("which", ROOTS)
def test_a_root_named_through_a_share_or_extended_path_is_its_drive(repo, which):  # noqa: F811
    r"""cmd.exe cannot start a command in a UNC directory: it says so and runs
    the lane in C:\Windows. `--repo \\?\C:\...`, `--repo \\?\UNC\localhost\C$\...`
    and `--repo \\localhost\C$\...` rooted the run on such a path."""
    assert _command_root(ROOTS[which](repo)) == repo.resolve()


@pytest.mark.skipif(not WINDOWS, reason="needs Windows path rules")
def test_a_checkout_entered_through_its_admin_share_is_rooted_on_its_drive(repo,  # noqa: F811
                                                                           monkeypatch):
    r"""With no --repo, a session standing in `\\localhost\C$\...` found its
    crapkit.toml there, and every lane ran in C:\Windows."""
    monkeypatch.chdir(admin_share(repo))

    assert _command_root(None) == repo.resolve()


# --- init reads its root the way every other command does -------------------------

def _uninitialized(tmp_path: Path) -> Path:
    """A tracked Go file and no crapkit.toml: init probes no interpreter for it."""
    root = tmp_path / "initrepo"
    (root / "cmd").mkdir(parents=True)
    (root / "cmd" / "route.go").write_text("package main\n\nfunc route() int { return 1 }\n",
                                           encoding="utf-8")
    git(root, "init", "-q")
    commit_all(root, "init")
    return root.resolve()


def _msys(root: Path, prefix: str) -> str:
    return prefix + root.drive[0].lower() + root.as_posix()[2:]


# id -> (what the host needs, a spelling of the checkout init is pointed at)
INIT_ROOTS = {
    "native": ("", str),
    "forward": ("", lambda root: root.as_posix()),
    "msys": ("windows", lambda root: _msys(root, "/")),
    "wsl": ("windows", lambda root: _msys(root, "/mnt/")),
    "lower-drive": ("windows", lower_drive),
    "extended-length": ("windows", lambda root: "\\\\?\\" + str(root)),
    "admin-share": ("windows", admin_share),
}


@pytest.mark.parametrize("which", INIT_ROOTS)
def test_init_writes_into_the_checkout_its_repo_flag_names_in_any_spelling(tmp_path, capsys,
                                                                           which):
    r"""`init --repo /c/...` read the path as C:\c\... and ended in a traceback
    (NotADirectoryError, exit 1), where `worklist --repo` in the same spelling
    served the checkout."""
    spec, spell = INIT_ROOTS[which]
    root = _uninitialized(tmp_path)
    need(spec, root)

    code = main(["init", "--repo", spell(root)])

    assert code == 0, capsys.readouterr().err
    assert (root / "crapkit.toml").is_file()


@pytest.mark.skipif(not WINDOWS, reason="needs Windows path rules")
def test_init_standing_in_the_admin_share_writes_on_the_drive(tmp_path, monkeypatch, capsys):
    root = _uninitialized(tmp_path)
    monkeypatch.chdir(admin_share(root))

    assert main(["init"]) == 0, capsys.readouterr().err
    assert (root / "crapkit.toml").is_file()


# --- a report written from another spelling of the checkout ----------------------

def _rekey(artifact: Path, spell) -> None:
    """Rewrite each absolute istanbul key (and its `path`) in `spell`'s spelling,
    the way a runner started from that spelling of the checkout writes it."""
    report = json.loads(artifact.read_text(encoding="utf-8"))
    rekeyed = {spell(key): {**entry, "path": spell(entry["path"])} for key, entry in report.items()}
    artifact.write_text(json.dumps(rekeyed), encoding="utf-8")


# id -> (need, the key spelling of an absolute path the checkout resolves to)
REPORT_KEYS = {
    "resolved": ("", lambda key: key),
    "upper-cased": ("windows case", lambda key: key.upper()),
    "lower-drive": ("windows", lambda key: key[0].lower() + key[1:]),
    "short-name": ("windows", short_name),
}


@pytest.mark.parametrize("stand", ["repo-flag", "lower-drive-cwd"])
@pytest.mark.parametrize("which", REPORT_KEYS)
def test_a_reused_report_scores_whatever_spelling_the_runner_and_crapkit_started_from(
        repo, monkeypatch, capsys, which, stand):  # noqa: F811
    r"""vitest run by hand from `cd /d C:\...` or `cd /d c:\...` writes every key
    in that spelling, and crapkit started from `c:\...` roots itself there too:
    cmd.exe keeps a lower-case drive letter it was handed. Every pairing scores
    the lane's three functions measured."""
    spec, spell = REPORT_KEYS[which]
    need(("windows " if stand == "lower-drive-cwd" else "") + spec, repo)
    seed_artifacts(repo)
    for artifact in ("coverage/unit.json", "coverage/ui.json"):
        _rekey(repo / artifact, spell)
    monkeypatch.chdir(lower_drive(repo) if stand == "lower-drive-cwd" else repo.parent)
    argv = ["coverage", "--reuse-artifacts"] + (["--repo", str(repo)] if stand == "repo-flag" else [])

    code = main(argv)

    out = capsys.readouterr()
    assert code == 0, out.err
    assert "3 functions scored: 3 measured" in out.out, out.out


# --- next-item --exclude ---------------------------------------------------------

@pytest.fixture()
def queued(repo, capsys):  # noqa: F811
    add_knotty(repo)
    commit_all(repo, "knotty")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


def _handed_out(root: Path, capsys, pattern: str) -> list[dict]:
    code, out, err = run(["next-item", "--top", "5", "--exclude", pattern], root, capsys)
    assert code == 0, err
    return json.loads(out).get("items", [])


@pytest.mark.parametrize("pattern, need", [("src/app", ""), ("./src/app", ""),
                                           ("src\\app", "windows"), ("src\\", "windows"),
                                           ("SRC/App", "case")])
def test_next_item_skips_what_the_exclude_names_in_any_spelling(queued, capsys, pattern, need):
    """`--exclude pkg\\legacy` and `--exclude PKG/Legacy` were matched as text
    against git's `pkg/legacy/...`, and the excluded directory was handed out
    as the next item anyway."""
    _need(need, queued)

    items = _handed_out(queued, capsys, pattern)

    assert [i["path"] for i in items if i["path"] == "src/app.ts"] == [], items


@only_posix
@pytest.mark.parametrize("pattern", ["src\\app", "src\\app\\", "src\\"])
def test_posix_reads_a_backslash_in_an_exclude_as_part_of_a_name(queued, capsys, pattern):
    """A backslash is a filename character on POSIX, so `src\\app` names no
    directory there and git's src/app.ts is still handed out."""
    items = _handed_out(queued, capsys, pattern)

    assert [i["path"] for i in items if i["path"] == "src/app.ts"], items


def test_an_exclude_in_another_case_names_another_directory_on_a_case_sensitive_disk(queued,
                                                                                     capsys):
    need_case_sensitive(queued)

    items = _handed_out(queued, capsys, "SRC/App")

    assert [i["path"] for i in items if i["path"] == "src/app.ts"], items


def test_next_item_still_skips_a_function_name_fragment(queued, capsys):
    items = _handed_out(queued, capsys, "knotty")

    assert [i["function"] for i in items if "knotty" in i["function"]] == [], items


def test_next_item_reads_a_function_name_in_its_own_case(queued, capsys):
    """Only the path side folds case: `Knotty` is another function name."""
    items = _handed_out(queued, capsys, "Knotty")

    assert [i["function"] for i in items if "knotty" in i["function"]], items
