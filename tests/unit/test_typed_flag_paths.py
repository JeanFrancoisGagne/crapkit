r"""A path typed to a flag that writes or opens a file reads by the typed-path rules.

`--export`, `--sarif`, `--emit-baseline` and `report --out` placed their file
with `Path(out)`, and `inventory --db` and `verify --baseline-tsv` opened theirs
the same way. On Windows that reads Git Bash's `/c/Users/...` as
`C:\c\Users\...`: `inventory --export /c/.../x.tsv` exited 0 and wrote the file
into a new `C:\c` tree, and a baseline typed that way was "no baseline file".
Every file argument and `--repo` already read those spellings through
repopath's typed entry; these flags now read through it too.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli_inproc_repo import repo, seed_artifacts, template_repo  # noqa: F401
from crapkit.cli import main
from crapkit.cli._shared import _repo_out_path
from crapkit.errors import ConfigError

from path_spellings import admin_share, lower_drive, need


def _msys(path: Path, prefix: str = "/") -> str:
    return prefix + path.drive[0].lower() + path.as_posix()[2:]


def _through_share(path: Path) -> str:
    """`path` through this machine's admin share. Its folder exists, the file
    need not."""
    return admin_share(path.parent) + "\\" + path.name


# id -> (need, a spelling of an absolute destination outside the checkout)
DESTINATIONS = {
    "native": ("", str),
    "forward": ("", lambda path: path.as_posix()),
    "dot-dot": ("", lambda path: str(path.parent / "x" / ".." / path.name)),
    "msys": ("windows", _msys),
    "wsl": ("windows", lambda path: _msys(path, "/mnt/")),
    "lower-drive": ("windows", lower_drive),
    "extended-length": ("windows", lambda path: "\\\\?\\" + str(path)),
    "admin-share": ("windows", _through_share),
}


def _destination(tmp_path: Path, name: str) -> Path:
    """`out dir/<name>`, beside an `x` directory: POSIX resolves `x/..` on the
    disk, so the dot-dot spelling names the file only where `x` exists."""
    dest = tmp_path.resolve() / "out dir" / name
    (dest.parent / "x").mkdir(parents=True, exist_ok=True)
    return dest


@pytest.mark.parametrize("which", DESTINATIONS)
def test_an_absolute_destination_in_any_spelling_is_the_place_it_names(repo, tmp_path,  # noqa: F811
                                                                        which):
    spec, spell = DESTINATIONS[which]
    need(spec, tmp_path)
    dest = _destination(tmp_path, "x.tsv")

    _repo_out_path(repo, spell(dest)).write_text("x", encoding="utf-8")

    assert dest.read_text(encoding="utf-8") == "x"


# id -> (need, a repo-relative destination for out/x.tsv)
RELATIVE = {
    "forward": ("", "out/x.tsv"),
    "dot-slash": ("", "./out/x.tsv"),
    "dot-dot-inside": ("", "src/../out/x.tsv"),
    "backslash": ("windows", "out\\x.tsv"),
    "dot-backslash": ("windows", ".\\out\\x.tsv"),
}


@pytest.mark.parametrize("which", RELATIVE)
def test_a_relative_destination_lands_under_the_root(repo, which):  # noqa: F811
    spec, raw = RELATIVE[which]
    need(spec, repo)

    _repo_out_path(repo, raw).write_text("x", encoding="utf-8")

    assert (repo / "out" / "x.tsv").read_text(encoding="utf-8") == "x"


@pytest.mark.parametrize("raw, spec", [("../x.tsv", ""), ("out/../../x.tsv", ""),
                                       ("..\\x.tsv", "windows")])
def test_a_relative_destination_that_climbs_out_is_refused(repo, raw, spec):  # noqa: F811
    need(spec, repo)

    with pytest.raises(ConfigError, match="climbs out of"):
        _repo_out_path(repo, raw)


# --- each flag, through the CLI ---------------------------------------------------

@pytest.fixture()
def scored(repo, capsys):  # noqa: F811
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    return repo


# flag -> (the command up to the flag, the file it writes)
WRITERS = {
    "inventory-export": (["inventory", "--export"], "x.tsv"),
    "inventory-db": (["inventory", "--db"], "x.sqlite"),
    "coverage-export": (["coverage", "--reuse-artifacts", "--export"], "x.tsv"),
    "coverage-sarif": (["coverage", "--reuse-artifacts", "--sarif"], "x.sarif"),
    "report-out": (["report", "--out"], "x.html"),
    "verify-emit-baseline": (["verify", "--reuse-artifacts", "--emit-baseline"], "b.tsv"),
    "verify-sarif": (["verify", "--reuse-artifacts", "--sarif"], "v.sarif"),
}


@pytest.mark.parametrize("which", ["native", "msys", "wsl"])
@pytest.mark.parametrize("flag", WRITERS)
def test_each_writer_flag_puts_its_file_where_the_typed_path_names(scored, tmp_path, capsys,
                                                                   flag, which):
    spec, spell = DESTINATIONS[which]
    need(spec, tmp_path)
    argv, name = WRITERS[flag]
    dest = _destination(tmp_path, name)

    code = main([*argv, spell(dest), "--repo", str(scored)])

    assert code == 0, capsys.readouterr().err
    assert dest.is_file(), f"{flag} wrote nothing at {dest}"


@pytest.mark.parametrize("which", DESTINATIONS)
def test_a_baseline_file_typed_in_any_spelling_is_read(scored, tmp_path, capsys, which):
    spec, spell = DESTINATIONS[which]
    need(spec, tmp_path)
    dest = _destination(tmp_path, "b.tsv")
    assert main(["verify", "--reuse-artifacts", "--emit-baseline", str(dest),
                 "--repo", str(scored)]) == 0
    capsys.readouterr()

    code = main(["verify", "--reuse-artifacts", "--json", "--baseline-tsv", spell(dest),
                 "--repo", str(scored)])
    out = capsys.readouterr()

    assert code == 0, out.err
    assert json.loads(out.out)["baseline_run"] is None, "read from the file, not the store"


@pytest.mark.parametrize("raw, spec", [("./.crapkit/b.tsv", ""), (".crapkit\\b.tsv", "windows")])
def test_a_relative_baseline_file_is_read_under_the_root(scored, capsys, raw, spec):
    need(spec, scored)
    assert main(["verify", "--reuse-artifacts", "--emit-baseline", ".crapkit/b.tsv",
                 "--repo", str(scored)]) == 0
    capsys.readouterr()

    code = main(["verify", "--reuse-artifacts", "--baseline-tsv", raw, "--repo", str(scored)])

    assert code == 0, capsys.readouterr().err


@pytest.mark.parametrize("which", ["native", "forward", "msys", "wsl"])
def test_the_resource_directory_typed_in_any_spelling_is_that_directory(tmp_path, monkeypatch,
                                                                        which):
    r"""CRAPKIT_RESOURCE_DIR is set by hand, and processes must name one
    directory to share their slots: `/c/...` read as `C:\c\...` was another."""
    from crapkit.resources import _budget_directory

    spec, spell = DESTINATIONS[which]
    need(spec, tmp_path)
    slots = tmp_path.resolve() / "slots"
    monkeypatch.setenv("CRAPKIT_RESOURCE_DIR", spell(slots))

    assert _budget_directory() == slots
