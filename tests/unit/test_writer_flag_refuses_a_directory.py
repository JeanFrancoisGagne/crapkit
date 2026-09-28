"""A writer flag that names an existing directory is refused before the command
does any work.

`verify --sarif DIR --json` stored its run as verdict=ok, then opened DIR for
writing and ended in a PermissionError traceback at exit 1 with nothing on
stdout, so a wrapper read no error object and the store kept a verdict nobody
was told. `--emit-baseline`, `inventory --export`, `coverage --export` and
`--sarif`, and `report --out` crashed the same way. Each now exits 3 naming the
flag and the path, before a run is stored, and `--json` carries the error
object.
"""
import json
from contextlib import closing

import pytest
from cli_inproc_repo import repo, seed_artifacts, template_repo  # noqa: F401

from crapkit.cli import main
from crapkit.store import SnapshotStore

REFUSAL = "is a directory; name a file to write"


def run(root, capsys, *argv: str) -> tuple[int, str, str]:
    code = main([*argv, "--repo", str(root)])
    out = capsys.readouterr()
    return code, out.out, out.err


def stored_runs(root) -> list[str]:
    store = SnapshotStore(root / ".crapkit" / "crap.sqlite")
    with closing(store._conn):
        return [r["kind"] for r in store.list_runs()]


@pytest.fixture()
def scored(repo, capsys):  # noqa: F811 (the imported fixture)
    """One trusted coverage run, and a directory where a file would go."""
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    (repo / "outdir").mkdir()
    capsys.readouterr()
    return repo


WRITERS = [
    ("inventory", "--export"),
    ("coverage --reuse-artifacts", "--export"),
    ("coverage --reuse-artifacts", "--sarif"),
    ("verify --reuse-artifacts", "--sarif"),
    ("verify --reuse-artifacts", "--emit-baseline"),
]


@pytest.mark.parametrize("command, flag", WRITERS)
def test_a_writer_into_a_directory_is_refused_before_the_run_is_stored(scored, capsys,
                                                                       command, flag):
    before = stored_runs(scored)

    code, out, err = run(scored, capsys, *command.split(), flag, "outdir")

    assert code == 3, out + err
    assert f"{flag} 'outdir' {REFUSAL}" in err and "Traceback" not in err, err
    assert stored_runs(scored) == before


@pytest.mark.parametrize("command, flag", WRITERS)
def test_json_carries_the_error_object(scored, capsys, command, flag):
    target = str((scored / "outdir").resolve())

    code, out, _ = run(scored, capsys, *command.split(), flag, target, "--json")

    error = json.loads(out)["error"]
    assert (code, error["exit"], error["kind"]) == (3, 3, "config"), out
    assert error["message"].startswith(f"{flag} '{target}' {REFUSAL}"), out


def test_report_out_into_a_directory_is_refused(scored, capsys):
    code, out, err = run(scored, capsys, "report", "--out", "outdir")

    assert code == 3, out + err
    assert f"--out 'outdir' {REFUSAL}" in err and "Traceback" not in err, err


def test_a_file_path_still_writes(scored, capsys):
    """The control: a file path beside the directory writes as before."""
    code, _, err = run(scored, capsys, "verify", "--reuse-artifacts", "--sarif", "outdir/v.sarif")

    assert code == 0, err
    assert (scored / "outdir" / "v.sarif").is_file()
