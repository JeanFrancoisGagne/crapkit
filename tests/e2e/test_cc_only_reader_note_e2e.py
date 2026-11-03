"""A Rust repo from `crapkit init` to a measured scope, through the commented lane.

The repo holds the lcov fixture's crate under `rust/` and a `Cargo.toml` at its
root, the marker that shows init cargo llvm-cov. init writes the scope cc-only
and the rust lane commented; doctor notes that a reader can measure the scope.
The test then does what the note says: it uncomments the lane, deletes
`coverage_optional`, and swaps the lane's command for a copy of the committed
cargo-llvm-cov recording, since CI has no cargo. verify refuses the flip until
`crapkit ratchet seed --scope rust` re-marks the scope, and passes after it.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from crapkit.cli import main

ROOT = Path(__file__).resolve().parents[2]
LCOV = ROOT / "tests" / "fixtures" / "coverage" / "lcov"
MARKS = "crapkit-ratchet.tsv"
UPGRADE = "docs/upgrading.md#turning-a-cc-only-scope-into-a-measured-one"
STANZA_COMMAND = "cargo llvm-cov --lcov --output-path .crapkit/cov/rust.lcov"
# The lane's stand-in command: the recording copied to the stanza's artifact.
COPY_COV = ("import pathlib, shutil, sys\n"
            "pathlib.Path('.crapkit/cov').mkdir(parents=True, exist_ok=True)\n"
            "shutil.copy(sys.argv[1], '.crapkit/cov/rust.lcov')\n")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo,
                   check=True, capture_output=True, timeout=120)


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.delenv("CRAPKIT_OVERRIDE_REASON", raising=False)
    root = tmp_path / "crate"
    (root / "rust").mkdir(parents=True)
    for name in ("drive.rs", "shapes.rs"):
        (root / "rust" / name).write_bytes((LCOV / "rust" / name).read_bytes())
    # The root Cargo.toml is the workspace: cargo refuses a member that declares its own.
    crate = (LCOV / "rust" / "Cargo.toml").read_text(encoding="utf-8").replace("[workspace]\n", "")
    (root / "rust" / "Cargo.toml").write_text(crate, encoding="utf-8", newline="\n")
    (root / "Cargo.toml").write_text('[workspace]\nmembers = ["rust"]\n', encoding="utf-8",
                                     newline="\n")
    (root / ".gitignore").write_text(".crapkit/\ncopy_cov.py\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "init")
    (root / "copy_cov.py").write_text(COPY_COV, encoding="utf-8", newline="\n")
    return root


def cli(repo: Path, capsys, *args: str) -> tuple[int, str, str]:
    code = main([*args, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def marks(repo: Path) -> dict[str, str]:
    rows = (line.split("\t") for line in (repo / MARKS).read_text(encoding="utf-8").splitlines()
            if line.startswith("rust/"))
    return {name: mark for _, name, mark in rows}


def _measured_config(text: str) -> str:
    """The config with the rust stanza uncommented, coverage_optional deleted,
    and the stanza's command swapped for the copy of the recording."""
    command = f'command = "{STANZA_COMMAND}"'
    lines = []
    for line in text.split("\n"):
        if line == "coverage_optional = true":
            continue
        lines.append(line[2:] if line.startswith("# ") and _in_stanza(line) else line)
    swapped = "\n".join(lines).replace(command, f'command = "python copy_cov.py '
                                                f'{(LCOV / "cargo-llvm-cov-0.9.1.lcov").as_posix()}"')
    assert swapped.count("[[lane]]") == 1, swapped
    return swapped


def _in_stanza(line: str) -> bool:
    return line in ("# [[lane]]", '# name = "rust"', f'# command = "{STANZA_COMMAND}"',
                    '# artifact = ".crapkit/cov/rust.lcov"', '# parser = "lcov"',
                    '# scopes = ["rust"]')


def test_init_writes_the_stanza_and_doctor_notes_the_reader(repo, capsys):
    code, out, err = cli(repo, capsys, "init")
    text = (repo / "crapkit.toml").read_text(encoding="utf-8")

    assert code == 0, (out, err)
    assert sum(_in_stanza(line) for line in text.split("\n")) == 6
    assert "coverage_optional = true" in text
    assert "every scope is cc-only (coverage_optional = true) and needs no lane" in out

    code, out, err = cli(repo, capsys, "doctor")

    assert code == 0, (out, err)
    note = next(line for line in out.splitlines() if "a reader can measure" in line)
    assert "'rust'" in note and 'parser = "lcov"' in note and "cargo llvm-cov" in note
    assert UPGRADE in note


def test_uncommenting_the_stanza_flips_the_scope_and_the_scoped_seed_raises_its_marks(repo, capsys):
    """The crate's functions sit at ccn 5 or less, so target 2 puts them over
    the ceiling and the cc-only seed marks them; a scope with no marks has no
    stamp for the flip to move."""
    assert cli(repo, capsys, "init")[0] == 0
    config = repo / "crapkit.toml"
    config.write_text(config.read_text(encoding="utf-8").replace("target = 6", "target = 2"),
                      encoding="utf-8", newline="\n")
    for step in (["coverage"], ["worklist"], ["ratchet", "seed"], ["verify"]):
        code, out, err = cli(repo, capsys, *step)
        assert code == 0, (step, out, err)
    _commit(repo, "seed")
    cc_only = marks(repo)
    assert cc_only, "the cc-only seed marked no function"
    config.write_text(_measured_config(config.read_text(encoding="utf-8")), encoding="utf-8",
                      newline="\n")

    code, out, err = cli(repo, capsys, "verify")
    assert code == 3, (out, err)
    assert "rust (coverage cc-only to lcov=1)" in err and "ratchet seed --scope rust`" in err, err
    code, out, err = cli(repo, capsys, "coverage")
    assert code == 0, (out, err)
    assert "9 functions scored" in out and "cc-only" not in out.split("\n")[0], out

    code, out, err = cli(repo, capsys, "ratchet", "seed", "--scope", "rust")

    assert code == 0, (out, err)
    measured = marks(repo)
    risen = sum(float(measured[name]) > float(mark) for name, mark in cc_only.items())
    printed = re.search(r"scope rust: added \d+, tightened 0, raised (\d+)", out)
    assert printed and int(printed.group(1)) == risen > 0, out
    assert set(cc_only) <= set(measured)
    assert cli(repo, capsys, "verify")[0] == 0


def test_a_go_repo_with_no_go_mod_gets_no_stanza_and_no_note(tmp_path, capsys):
    root = tmp_path / "go"
    (root / "cmd").mkdir(parents=True)
    (root / "cmd" / "main.go").write_text("package main\n\nfunc main() {}\n", encoding="utf-8")
    _git(root, "init", "-q", "-b", "main")
    _commit(root, "init")

    assert cli(root, capsys, "init")[0] == 0
    code, out, err = cli(root, capsys, "doctor")

    assert "[[lane]]" not in (root / "crapkit.toml").read_text(encoding="utf-8")
    assert code == 0 and "a reader can measure" not in out
