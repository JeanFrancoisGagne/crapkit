"""`crapkit score`: the flags-to-Config builder, the format sniff, and the
command itself driven in process over small committed repos.

The two-function fixture's numbers are counted by hand: pick (ccn 3) runs 4 of
its 6 lines, CRAP 9 * (1/3)^3 + 3 = 3.3333; five (ccn 5) runs none of its 10,
CRAP 25 + 5 = 30. wide (ccn 31) runs every line, CRAP 31.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import tomllib
from pathlib import Path

import pytest

from coverage_fields import minimal_artifact
from crapkit import coverage_format, one_shot
from crapkit.cli import main
from crapkit.coverage_lcov import WRONG_TREE_FIX
from crapkit.errors import ConfigError, GitError
from crapkit.score import parse_scored_tsv

CALC = """def pick(a, b):
    if a:
        return 1
    if b:
        return 2
    return 0


def five(a, b, c, d):
    if a:
        return 1
    if b:
        return 2
    if c:
        return 3
    if d:
        return 4
    return 0
"""
PICK_HITS = {1: 1, 2: 1, 3: 0, 4: 1, 5: 0, 6: 1}
FIVE_HITS = {line: 0 for line in range(9, 19)}
WIDE = "def wide(x):\n" + "".join(f"    if x == {k}:\n        return {k}\n" for k in range(30)) \
    + "    return -1\n"
WIDE_HITS = {line: 1 for line in range(1, 63)}
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "coverage" / "lcov"
COBERTURA = FIXTURE.parent / "cobertura"
RECORDED = Path(__file__).resolve().parents[1] / "accuracy" / "coverage_oracles" / "recorded"
# Read off the registry, so a reader that lands later joins the list without a test edit.
ACCEPTED = f"--format takes one of: {', '.join(sorted(coverage_format._FORMATS))}"


def _lcov(*records: tuple[str, dict[int, int]]) -> str:
    body = "".join(f"SF:{path}\n" + "".join(f"DA:{line},{hits}\n" for line, hits in sorted(lines.items()))
                   + "end_of_record\n" for path, lines in records)
    return "TN:\n" + body


def _git(root: Path, *args: str) -> str:
    done = subprocess.run(["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
                           "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false", *args],
                          cwd=root, check=True, capture_output=True, text=True, encoding="utf-8")
    return done.stdout


def _repo(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q", "-b", "main")
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "fixture")
    return root


def _calc_repo(root: Path, *, wide: bool = False) -> Path:
    files = {"src/calc.py": CALC, "lcov.info": _lcov(("src/calc.py", {**PICK_HITS, **FIVE_HITS}))}
    if wide:
        files["src/wide.py"] = WIDE
        files["lcov.info"] += _lcov(("src/wide.py", WIDE_HITS)).removeprefix("TN:\n")
    return _repo(root, files)


def _score(capsys, repo: Path, *args: str) -> tuple[int, list[str], str]:
    code = main(["score", "src", "--coverage", "lcov.info", "--repo", str(repo), *args])
    out, err = capsys.readouterr()
    return code, out.splitlines(), err


# --- the sniff ---------------------------------------------------------------------


@pytest.mark.parametrize("fmt", ["lcov", "coveragepy", "istanbul", "cobertura"])
def test_sniff_names_each_format_from_its_minimal_artifact(fmt):
    head = minimal_artifact(fmt)
    assert one_shot.sniff_format(head) == fmt
    assert one_shot.sniff_format(b"\xef\xbb\xbf" + head) == fmt


@pytest.mark.parametrize("report", [
    COBERTURA / "gcovr-7.2-stanza.xml", COBERTURA / "nyc-18.0.0-call.xml",
    RECORDED / "coveragepy-reports-7.16.1" / "call.xml"], ids=["gcovr", "istanbul", "coverage-xml"])
def test_sniff_names_cobertura_from_each_producers_report(report):
    assert one_shot.sniff_format(report.read_bytes()[:one_shot.HEAD_BYTES]) == "cobertura"


def test_sniff_names_cobertura_from_a_coverage_root_with_packages_and_no_sources():
    head = b'<?xml version="1.0" ?>\n<coverage version="7"><packages><package name="a"/></packages>'
    assert one_shot.sniff_format(head) == "cobertura"


@pytest.mark.parametrize("head", [
    b'<?xml version="1.0"?><report name="jacoco"><sessioninfo id="x"/></report>',
    b'<?xml version="1.0"?><coverage version="7"/>',
    b"mode: set\nexample.com/a/a.go:3.14,5.2 1 1\n",
    b"", b"[1, 2]", b'{"files": {}}', b"plain text\n",
])
def test_sniff_names_no_format_crapkit_has_no_reader_for(head):
    assert one_shot.sniff_format(head) is None


def test_format_flag_overrides_the_sniff(tmp_path):
    artifact = tmp_path / "cov.out"
    artifact.write_bytes(minimal_artifact("lcov"))
    assert one_shot.format_of(artifact, None) == "lcov"
    assert one_shot.format_of(artifact, "coveragepy") == "coveragepy"


def test_an_unknown_format_name_exits_3_naming_the_flag_and_the_names(tmp_path):
    artifact = tmp_path / "cov.out"
    artifact.write_bytes(minimal_artifact("lcov"))
    with pytest.raises(ConfigError) as caught:
        one_shot.format_of(artifact, "no-such-format")
    assert caught.value.exit_code == 3
    assert "--format 'no-such-format'" in str(caught.value)
    assert ACCEPTED in str(caught.value)


def test_a_file_of_no_format_exits_3_naming_the_flag(tmp_path):
    artifact = tmp_path / "cov.xml"
    artifact.write_bytes(b"<report/>")
    with pytest.raises(ConfigError, match="name it with --format. --format takes one of"):
        one_shot.format_of(artifact, None)


def test_a_missing_coverage_file_is_refused_naming_the_flag(tmp_path):
    with pytest.raises(ConfigError, match=r"^--coverage .*nope\.info: "):
        one_shot.format_of(tmp_path / "nope.info", "lcov")


# --- the Config the flags build ----------------------------------------------------


def test_one_scope_per_language_found_and_one_lane_with_no_command(tmp_path):
    repo = _repo(tmp_path / "r", {"src/a.py": "x = 1\n", "src/web/b.ts": "export const b = 1;\n",
                                  "docs/c.rs": "fn c() {}\n"})
    cfg = one_shot.config_from_flags(repo, ["./src/"], "out/lcov.info", "lcov")
    assert [(s.name, s.paths, s.languages) for s in cfg.scopes] == [
        ("typescript", ("src",), ("typescript",)), ("python", ("src",), ("python",))]
    (lane,) = cfg.lanes
    assert (lane.name, lane.command, lane.artifact, lane.parser, lane.scopes) == (
        "score", "", "out/lcov.info", "lcov", ("typescript", "python"))
    assert lane.results_artifact == ""
    assert cfg.target == 6


def test_a_path_with_a_space_and_non_ascii_letters_reads_as_written(tmp_path):
    repo = _repo(tmp_path / "r", {"my src/été.py": "def f():\n    return 1\n"})
    cfg = one_shot.config_from_flags(repo, ["my src"], "cov dir/é.info", "lcov")
    assert cfg.scopes[0].paths == ("my src",)
    assert cfg.lanes[0].artifact == "cov dir/é.info"


@pytest.mark.parametrize("text", ['a "quoted" name', "back\\slash", "tab\there", "line\nbreak",
                                  "del\x7f", "éè \U0001f600"])
def test_a_flag_value_reaches_crapkit_toml_unchanged(text):
    assert tomllib.loads("v = " + one_shot._quoted(text))["v"] == text


def test_a_path_crapkit_toml_would_refuse_is_refused_naming_path(tmp_path):
    repo = _repo(tmp_path / "r", {"src/a.py": "x = 1\n"})
    with pytest.raises(ConfigError, match=r"^scope 'PATH': path '\.\./elsewhere'"):
        one_shot.config_from_flags(repo, ["../elsewhere"], "lcov.info", "lcov")


def test_a_coverage_value_crapkit_toml_would_refuse_is_led_by_the_flag(tmp_path):
    repo = _repo(tmp_path / "r", {"src/a.py": "x = 1\n"})
    with pytest.raises(ConfigError, match=r"^--coverage: lane 'score': artifact names the directory"):
        one_shot.config_from_flags(repo, ["src"], "", "lcov")


def test_a_path_with_only_untracked_source_names_it(tmp_path):
    repo = _repo(tmp_path / "r", {"README.md": "x\n"})
    (repo / "src").mkdir()
    (repo / "src" / "new.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        one_shot.config_from_flags(repo, ["src"], "lcov.info", "lcov")
    assert str(caught.value) == (
        "PATH src: no tracked source file under it - crapkit scores git-tracked files only; run "
        "`git add` first (1 untracked source file(s) found: src/new.py)")


def test_outside_a_git_repository_git_refuses(tmp_path):
    (tmp_path / "src").mkdir()
    with pytest.raises(GitError):
        one_shot.config_from_flags(tmp_path, ["src"], "lcov.info", "lcov")


# --- the command -------------------------------------------------------------------


def test_help_names_every_flag_the_sniffed_formats_and_the_exit_codes():
    from crapkit.cli.parser import _help_topics, build_parser

    text = " ".join(_help_topics(build_parser())["score"].format_help().split())
    words = ("PATH", "--repo", "--coverage FILE", "--format NAME", "--fail-above N", "--top K",
             "an LCOV tracefile", "a coverage.py JSON report", "an Istanbul JSON report",
             "Exit 0:", "Exit 3:", "Exit 4:", "Exit 5:", "Exit 6:")
    assert [word for word in words if word not in text] == []
    assert text.isascii()


def test_prints_each_function_over_the_ceiling_then_the_total(tmp_path, capsys):
    repo = _calc_repo(tmp_path / "r")
    code, lines, _ = _score(capsys, repo)
    assert code == 0
    assert lines == ["crap    30.00  ccn   5  cov   0%  src/calc.py:9  five( a , b , c , d )",
                     "2 functions, 1 over 6"]


def test_hand_counts_on_the_two_function_fixture(tmp_path, capsys):
    repo = _calc_repo(tmp_path / "r")
    code, lines, _ = _score(capsys, repo, "--fail-above", "3")
    assert code == 6
    assert lines == ["crap    30.00  ccn   5  cov   0%  src/calc.py:9  five( a , b , c , d )",
                     "crap     3.33  ccn   3  cov  67%  src/calc.py:1  pick( a , b )",
                     "2 functions, 2 over 3"]


def test_fail_above_exits_0_at_n_and_6_above_it(tmp_path, capsys):
    at_thirty = _calc_repo(tmp_path / "thirty")
    assert _score(capsys, at_thirty, "--fail-above", "30")[:2] == (0, ["2 functions, 0 over 30"])
    code, lines, _ = _score(capsys, _calc_repo(tmp_path / "wide", wide=True), "--fail-above", "30")
    assert code == 6
    assert lines == ["crap    31.00  ccn  31  cov 100%  src/wide.py:1  wide( x )",
                     "3 functions, 1 over 30"]


def test_top_prints_only_the_worst_k(tmp_path, capsys):
    repo = _calc_repo(tmp_path / "r")
    code, lines, _ = _score(capsys, repo, "--fail-above", "3", "--top", "1")
    assert code == 6
    assert lines == ["crap    30.00  ccn   5  cov   0%  src/calc.py:9  five( a , b , c , d )",
                     "2 functions, 2 over 3"]


def test_the_default_root_is_the_working_directory(tmp_path, capsys, monkeypatch):
    repo = _calc_repo(tmp_path / "r")
    monkeypatch.chdir(repo)
    assert main(["score", "src", "--coverage", str(repo / "lcov.info")]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "2 functions, 1 over 6"


def _aged(root: Path) -> Path:
    """Every work-tree file 10 s older. Analysis keeps a file's stat only once
    the file has held still 2 s, so a fixture written just now hides a write."""
    past = time.time() - 10
    for path in root.rglob("*"):
        if ".git" not in path.relative_to(root).parts and path.is_file():
            os.utime(path, (past, past))
    return root


@pytest.mark.parametrize(("cwd", "argv", "exit_code"), [
    (".", ["score", "src", "--coverage", "lcov.info", "--fail-above", "1"], 6),
    (".", ["score", "src", "--coverage", "{repo}/lcov.info", "--repo", "{repo}", "--top", "0"], 0),
    ("src", ["score", ".", "--coverage", "../lcov.info"], 5),
])
def test_the_work_tree_is_left_byte_identical(tmp_path, capsys, monkeypatch, cwd, argv, exit_code):
    repo = _aged(_calc_repo(tmp_path / "r"))
    before = _git(repo, "status", "--porcelain", "--ignored")
    monkeypatch.chdir(repo / cwd)
    assert main([arg.format(repo=repo) for arg in argv]) == exit_code, capsys.readouterr().err
    assert _git(repo, "status", "--porcelain", "--ignored") == before == ""
    assert list(repo.rglob(".crapkit")) == []


def test_an_unrelated_tracked_file_outside_path_changes_no_line(tmp_path, capsys):
    repo = _calc_repo(tmp_path / "r")
    first = _score(capsys, repo, "--fail-above", "1")
    (repo / "tools").mkdir()
    (repo / "tools" / "other.py").write_text(CALC, encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "an unrelated file")
    assert _score(capsys, repo, "--fail-above", "1") == first


def test_an_untracked_source_file_is_skipped_and_named(tmp_path, capsys):
    repo = _calc_repo(tmp_path / "r")
    (repo / "src" / "draft.py").write_text("def draft(a):\n    return a\n", encoding="utf-8")
    code, lines, err = _score(capsys, repo)
    assert (code, lines[-1]) == (0, "2 functions, 1 over 6")
    assert ("crapkit: skipped 1 untracked source file(s) under PATH - crapkit scores git-tracked "
            "files only; run `git add` to score them: src/draft.py") in err


def test_a_coverage_file_from_another_tree_exits_5_with_the_readers_fix(tmp_path, capsys):
    repo = _repo(tmp_path / "r", {"src/calc.py": CALC,
                                  "lcov.info": _lcov(("lib/calc.py", PICK_HITS))})
    code, _, err = _score(capsys, repo)
    assert code == 5
    assert ("--coverage lcov.info measured 1 file(s), none of them a tracked source file under "
            "PATH src; it reports paths like lib/calc.py.") in err
    assert WRONG_TREE_FIX in err


def test_not_a_git_repository_exits_4(tmp_path, capsys):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text(CALC, encoding="utf-8")
    (tmp_path / "lcov.info").write_text(_lcov(("src/calc.py", PICK_HITS)), encoding="utf-8")
    assert _score(capsys, tmp_path)[0] == 4


def test_an_unknown_format_exits_3_through_the_command(tmp_path, capsys):
    repo = _calc_repo(tmp_path / "r")
    code, _, err = _score(capsys, repo, "--format", "no-such-format")
    assert code == 3
    assert ACCEPTED in err


def test_a_gcovr_report_is_scored_with_no_format_flag(tmp_path, capsys):
    # grade (ccn 7) runs none of its lines: CRAP 49 * 1^3 + 7 = 56. clamp's
    # branch arms run 3 of 4: CRAP 9 * (1/4)^3 + 3 = 3.14, under the ceiling.
    files = {rel: (COBERTURA / "cmake-repo" / rel).read_text(encoding="utf-8")
             for rel in ("src/shapes.cpp", "src/shapes.h")}
    files["cpp.xml"] = (COBERTURA / "gcovr-7.2-stanza.xml").read_text(encoding="utf-8")
    repo = _repo(tmp_path / "r", files)
    code = main(["score", "src", "--coverage", "cpp.xml", "--repo", str(repo)])
    out, err = capsys.readouterr()
    assert code == 0, err
    worst, total = out.splitlines()
    assert worst.startswith("crap    56.00  ccn   7  cov   0%  src/shapes.cpp:13  grade(")
    assert total == "2 functions, 1 over 6"


# --- the configured path gives the same scores -------------------------------------

_LANE_TOML = """[crapkit]
target = 6

[[scope]]
name = "code"
paths = ["{path}"]
languages = ["{language}"]

[[lane]]
name = "lcov"
command = "python -c pass"
artifact = "lcov.info"
parser = "lcov"
scopes = ["code"]
"""


def _rust_files() -> dict[str, str]:
    files = {f"rust/{p.name}": p.read_text(encoding="utf-8") for p in (FIXTURE / "rust").iterdir()}
    files["lcov.info"] = (FIXTURE / "cargo-llvm-cov-0.9.1.lcov").read_text(encoding="utf-8")
    return files


def _calc_files() -> dict[str, str]:
    return {"src/calc.py": CALC, "lcov.info": _lcov(("src/calc.py", {**PICK_HITS, **FIVE_HITS}))}


def _compared(rows) -> list[tuple]:
    return sorted((r.path, r.long_name, r.start, r.end, r.ccn, r.cov, r.flag, r.crap, r.remedy)
                  for r in rows)


@pytest.mark.parametrize(("files", "path", "language"), [
    (_rust_files, "rust", "rust"), (_calc_files, "src", "python")])
def test_scores_equal_crapkit_coverage_through_a_configured_lane(tmp_path, capsys, files, path,
                                                                language):
    one = _repo(tmp_path / "one", files())
    configured = tmp_path / "configured"
    shutil.copytree(one, configured)
    (configured / "crapkit.toml").write_text(_LANE_TOML.format(path=path, language=language),
                                             encoding="utf-8", newline="\n")
    assert main(["coverage", "--repo", str(configured), "--reuse-artifacts",
                 "--export", "scored.tsv"]) == 0
    expected = parse_scored_tsv((configured / "scored.tsv").read_text(encoding="utf-8"))
    cfg = one_shot.config_from_flags(one, [path], "lcov.info", "lcov")
    from crapkit.cli.score import _inventory
    tracked, rows = _inventory(one, cfg)
    scored = one_shot.scored_rows(one, cfg, tracked, rows, one / "lcov.info")
    capsys.readouterr()
    assert expected and _compared(scored) == _compared(expected)
