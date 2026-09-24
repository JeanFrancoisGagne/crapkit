r"""Letter-case spellings a case-folding disk opens, whatever its separator.

macOS APFS opens `SRC/app.ts` for a tracked `src/app.ts`, as NTFS does, but
`/` is its only separator. Most letter-case rows skip by the disk alone, so
they already run there. These are the spellings the other files mark as
Windows rows or leave out: a `./` argument in another case, and the checkout's
own directory named in another case. Each one goes through the CLI, the
advisory hook or the istanbul reader. They run on every disk that folds case:
Windows runs them beside its own rows, and the macos-latest CI job runs them
under POSIX path rules.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli_inproc_repo import add_knotty, repo, seed_artifacts, template_repo  # noqa: F401
from crapkit import coverage_istanbul
from crapkit.cli import main
from crapkit.config import Lane

from path_spellings import need_case_insensitive
from test_claude_hook_path_spellings import _edit, _hook, breached  # noqa: F401
from test_cli_path_spellings import run, scored  # noqa: F401
from test_coverage_istanbul import ARTIFACT


def _other_case(root: Path) -> Path:
    """The checkout's own directory, named in another letter case."""
    return root.parent / root.name.swapcase()


# --- CLI file arguments (PL1, PL5) ------------------------------------------------

ARGUMENTS = {
    "dot-slash-dir-case": lambda root: "./SRC/app.ts",
    "checkout-case": lambda root: str(_other_case(root.resolve()) / "src" / "app.ts"),
    "checkout-and-dir-case": lambda root: str(_other_case(root.resolve()) / "SRC" / "app.ts"),
}


@pytest.mark.parametrize("which", ARGUMENTS)
def test_the_gate_fails_the_breach_in_a_folded_spelling(scored, capsys, which):
    need_case_insensitive(scored)
    add_knotty(scored)

    code, _, err = run(["rescore", ARGUMENTS[which](scored), "--gate"], scored, capsys)

    assert code == 6, err
    assert "knotty" in err and "src/app.ts" in err, err


@pytest.mark.parametrize("which", ARGUMENTS)
def test_test_scoped_routes_a_folded_spelling_to_its_scope(scored, capsys, which):
    need_case_insensitive(scored)

    code, _, err = run(["test-scoped", ARGUMENTS[which](scored)], scored, capsys)

    assert code == 0, err


def _gate_from(root_spelling: str | None, capsys) -> tuple[int, str]:
    argv = ["rescore", "src/app.ts", "--gate"]
    code = main(argv if root_spelling is None else [*argv, "--repo", root_spelling])
    return code, capsys.readouterr().err


def test_a_repo_flag_naming_the_checkout_in_another_case_gates_the_breach(scored, capsys):
    """Windows' resolve() hands back the listed case; POSIX's keeps the typed one."""
    need_case_insensitive(scored)
    add_knotty(scored)

    code, err = _gate_from(str(_other_case(scored.resolve())), capsys)

    assert code == 6, err
    assert "knotty" in err and "src/app.ts" in err, err


def test_a_working_directory_in_another_case_gates_the_breach(scored, monkeypatch, capsys):
    need_case_insensitive(scored)
    add_knotty(scored)
    monkeypatch.chdir(_other_case(scored.resolve()))

    code, err = _gate_from(None, capsys)

    assert code == 6, err
    assert "knotty" in err and "src/app.ts" in err, err


# --- the advisory hook's payload (PL6) ----------------------------------------------

HOOK_PATHS = {
    "checkout-case": lambda root: str(_other_case(root) / "calc" / "mod.py"),
    "checkout-and-file-case": lambda root: str(_other_case(root) / "calc" / "MOD.PY"),
    "relative-dir-case": lambda root: "CALC/mod.py",
}


@pytest.mark.parametrize("which", HOOK_PATHS)
def test_the_hook_advises_a_breach_in_a_folded_spelling(breached, monkeypatch, capsys, which):
    need_case_insensitive(breached)

    code, err = _hook(monkeypatch, capsys, _edit(HOOK_PATHS[which](breached), breached))

    assert code == 2, err
    assert "calc/mod.py" in err and "sprawl" in err, err


def test_the_hook_reads_a_session_cwd_in_another_case(breached, monkeypatch, capsys):
    need_case_insensitive(breached)

    code, err = _hook(monkeypatch, capsys,
                      _edit(str(breached / "calc" / "mod.py"), _other_case(breached)))

    assert code == 2, err
    assert "calc/mod.py" in err, err


# --- istanbul keys (PL11) ---------------------------------------------------------

def _istanbul_keys(root: Path, key: str) -> list[str]:
    body = dict(next(iter(ARTIFACT.values())), path=key)
    artifact = root / "coverage-final.json"
    artifact.write_text(json.dumps({key: body}), encoding="utf-8")
    lane = Lane(name="unit", command="x", artifact="coverage-final.json", parser="istanbul",
                scopes=("src",))
    return list(coverage_istanbul.read(lane, root, artifact)[0])


@pytest.fixture()
def measured(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.ts").write_text("export const a = 1;\n", encoding="utf-8")
    return root.resolve()


KEYS = {
    "checkout-case": lambda root: str(_other_case(root) / "src" / "app.ts"),
    "checkout-and-below-case": lambda root: str(_other_case(root) / "SRC" / "APP.TS"),
}


@pytest.mark.parametrize("which", KEYS)
def test_an_istanbul_key_naming_the_checkout_in_another_case_keys_gits_file(measured, which):
    need_case_insensitive(measured)

    assert _istanbul_keys(measured, KEYS[which](measured)) == ["src/app.ts"]
