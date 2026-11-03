"""End-to-end: the ratchet's metric stamp through the CLI seam.

A scoring change that leaves old marks in place is invisible — the marks still
parse, verify still runs, and every number it compares came from different rules.
The stamp turns that into an exit 3 instead of a green run.
"""
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest

from conftest import child_env, cli_runner, git_commit_all, git_init_repo
from unit.stamps import restamp

TOML = (
    '[crapkit]\ntarget = 6\n\n'
    '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n\n'
    '[[lane]]\nname = "unit"\ncommand = "python make_cov.py"\n'
    'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n'
)

MAKE_COV = (
    "import json, os\n"
    "app = os.path.join(os.getcwd(), 'src', 'app.ts')\n"
    "cov = {app: {'path': app,\n"
    "             'fnMap': {'0': {'name': 'tiny', 'decl': {'start': {'line': 1}},\n"
    "                             'loc': {'start': {'line': 1}, 'end': {'line': 1}}}},\n"
    "             'f': {'0': 1}, 'branchMap': {}, 'b': {}}}\n"
    "json.dump(cov, open('cov.json', 'w'))\n"
)

APP = "export function tiny(a: number) { return a; }\n"

TANGLED = (
    "export function tangled(a: number, b: number): number {\n"
    "  let r = 0;\n"
    "  if (a > 0) { if (b > 0) { r = 1; } else if (b < -5) { r = 2; } }\n"
    "  if (a > 10 && b > 10) { r += 3; }\n"
    "  if (a < -1) { r -= 1; } else if (b === 0) { r -= 2; }\n"
    "  return r;\n}\n"
)

RATCHET = "crapkit-ratchet.tsv"


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


# PYTHONPATH shims reach only a new interpreter, so this file keeps the child.
run_cli = cli_runner(timeout=180, spawn=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text(APP, encoding="utf-8")
    (tmp_path / "src" / "tangled.ts").write_text(TANGLED, encoding="utf-8")
    (tmp_path / "crapkit.toml").write_text(TOML, encoding="utf-8")
    (tmp_path / "make_cov.py").write_text(MAKE_COV, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".crapkit/\ncov.json\n", encoding="utf-8")
    git_init_repo(tmp_path)
    git_commit_all(tmp_path, "init")
    return tmp_path


def first_line(repo: Path) -> str:
    return (repo / RATCHET).read_text(encoding="utf-8").splitlines()[0]


def test_seed_stamps_the_marks_file_with_the_running_metric(repo: Path):
    assert run_cli(repo, "coverage", "--json").returncode == 0
    seeded = run_cli(repo, "ratchet", "seed")
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr

    stamp = first_line(repo)
    assert stamp.startswith("# crapkit-stamp=2 lizard=") and RATCHET in stamp, stamp
    text = (repo / RATCHET).read_text(encoding="utf-8")
    assert "\n#scope\tsrc\tanalysis typescript=" in text and "; coverage istanbul=" in text, text
    assert "tangled" in text


def test_a_marks_file_prune_creates_takes_the_running_per_scope_stamp_and_no_mark(repo: Path):
    assert run_cli(repo, "coverage", "--json").returncode == 0
    pruned = run_cli(repo, "ratchet", "prune")
    assert pruned.returncode == 0, pruned.stdout + pruned.stderr

    text = (repo / RATCHET).read_text(encoding="utf-8")
    assert first_line(repo).startswith("# crapkit-stamp=2 lizard=") and RATCHET in first_line(repo), text
    assert "\n#scope\tsrc\tanalysis typescript=" in text and "; coverage istanbul=" in text, text
    assert text.rstrip("\n").endswith("path\tlong_name\tcrap"), "prune adds no mark"


def test_a_stamped_ratchet_still_verifies_clean(repo: Path):
    assert run_cli(repo, "coverage", "--json").returncode == 0
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    res = run_cli(repo, "verify", "--json")
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)


def test_verify_refuses_marks_recorded_under_another_metric(repo: Path):
    assert run_cli(repo, "coverage", "--json").returncode == 0
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    text = (repo / RATCHET).read_text(encoding="utf-8")
    text = re.sub(r"lizard=[^:]+:", "lizard=0.0.1:", text, count=1)
    (repo / RATCHET).write_text(re.sub(r"analysis typescript=[0-9]+", "analysis typescript=1", text),
                                encoding="utf-8", newline="\n")

    res = run_cli(repo, "verify", "--json")
    assert res.returncode == 3, (res.returncode, res.stdout, res.stderr)
    assert "src (typescript analysis 1 to " in res.stderr, "the stale number must be named per scope"
    assert "; lizard 0.0.1 to " in res.stderr, res.stderr
    # One lizard version signs every scope and `seed --scope` keeps the file's: the plain seed fixes it.
    assert "ratchet seed`" in res.stderr and "--scope" not in res.stderr, res.stderr


def tangled_at(text: str, mark: str) -> str:
    return re.sub(r"(src/tangled\.ts\t[^\t]+\t)[0-9.]+", rf"\g<1>{mark}", text)


def test_a_scoped_seed_after_an_analysis_move_keeps_a_mark_the_run_scores_higher(repo: Path):
    """An analysis move lets no mark rise. The scoped seed restamps src and
    leaves tangled's mark below its score, so verify fails tangled with exit 7."""
    assert run_cli(repo, "coverage", "--json").returncode == 0
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    text = (repo / RATCHET).read_text(encoding="utf-8")
    moved = re.sub(r"(analysis typescript=)([0-9]+)", lambda m: f"{m[1]}{int(m[2]) - 1}", text)
    (repo / RATCHET).write_text(tangled_at(moved, "50.0000"), encoding="utf-8", newline="\n")
    assert run_cli(repo, "verify", "--json").returncode == 3

    seeded = run_cli(repo, "ratchet", "seed", "--scope", "src")

    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    assert seeded.stdout.startswith(f"{RATCHET}: scope src: added 0, tightened 0 - "), seeded.stdout
    assert (repo / RATCHET).read_text(encoding="utf-8") == tangled_at(text, "50.0000")
    res = run_cli(repo, "verify", "--json")
    assert res.returncode == 7, (res.returncode, res.stdout, res.stderr)
    assert "tangled" in res.stdout, res.stdout


ALERT = 'alert_command = "python -c \\"import sys; sys.stdin.read()\\""\n'


def test_the_rise_an_analysis_move_left_is_recorded_by_hand_as_verify_names(repo: Path):
    """The override path for a rise the scoped seed held back: `verify
    --override` grants no ratchet regression and names the escape, raising
    the mark by hand to the value verify prints; after that verify passes and
    the marks file records the rise."""
    (repo / "crapkit.toml").write_text(TOML.replace("[crapkit]\n", "[crapkit]\n" + ALERT), encoding="utf-8")
    assert run_cli(repo, "coverage", "--json").returncode == 0
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    text = (repo / RATCHET).read_text(encoding="utf-8")
    moved = re.sub(r"(analysis typescript=)([0-9]+)", lambda m: f"{m[1]}{int(m[2]) - 1}", text)
    (repo / RATCHET).write_text(tangled_at(moved, "50.0000"), encoding="utf-8", newline="\n")
    assert run_cli(repo, "ratchet", "seed", "--scope", "src").returncode == 0

    refused = run_cli(repo, "verify", "--override", "the analysis move scores tangled higher")

    assert refused.returncode == 7, (refused.returncode, refused.stdout, refused.stderr)
    found = re.search(r"override refused: 1 ratchet regression \(src/tangled\.ts tangled \( a , b \) "
                      r"50\.0 -> ([0-9.]+)\) never qualifies for an override; raise the mark by hand and commit it",
                      refused.stderr)
    assert found, refused.stderr
    assert (repo / RATCHET).read_text(encoding="utf-8") == tangled_at(text, "50.0000"), "a refused override writes"
    raised = f"{float(found[1]):.4f}"
    (repo / RATCHET).write_text(tangled_at(text, raised), encoding="utf-8", newline="\n")
    res = run_cli(repo, "verify", "--json")
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    assert f"src/tangled.ts\ttangled ( a , b )\t{raised}\n" in (repo / RATCHET).read_text(encoding="utf-8")


def test_verify_warns_once_and_proceeds_on_a_ratchet_written_before_stamping(repo: Path):
    assert run_cli(repo, "coverage", "--json").returncode == 0
    (repo / RATCHET).write_text("path\tlong_name\tcrap\nsrc/gone.ts\tf( )\t9999.0000\n",
                                encoding="utf-8")

    res = run_cli(repo, "verify", "--json")
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    assert res.stderr.count("no metric stamp") == 1, res.stderr


def test_a_scope_renamed_in_the_config_reads_as_unstamped_and_names_the_new_name(repo: Path):
    assert run_cli(repo, "coverage", "--json").returncode == 0
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    restamp(repo)
    (repo / "crapkit.toml").write_text(TOML.replace('name = "src"', 'name = "app"')
                                       .replace('scopes = ["src"]', 'scopes = ["app"]'), encoding="utf-8")
    assert run_cli(repo, "coverage", "--json").returncode == 0

    res = run_cli(repo, "verify")
    assert res.returncode == 3, (res.returncode, res.stdout, res.stderr)
    assert "1 scope(s): app (no stamp recorded for this scope)" in res.stderr, res.stderr
    assert "ratchet seed --scope app`" in res.stderr and "src" not in res.stderr.split(" - ", 1)[0]


def test_a_lane_added_to_a_cc_only_scope_reads_as_the_coverage_move(repo: Path):
    optional = TOML.split("[[lane]]")[0].replace('languages = ["typescript"]\n',
                                                 'languages = ["typescript"]\ncoverage_optional = true\n')
    (repo / "crapkit.toml").write_text(optional, encoding="utf-8")
    assert run_cli(repo, "coverage", "--json").returncode == 0
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    restamp(repo)
    (repo / "crapkit.toml").write_text(TOML, encoding="utf-8")
    assert run_cli(repo, "coverage", "--json").returncode == 0

    res = run_cli(repo, "verify")
    assert res.returncode == 3, (res.returncode, res.stdout, res.stderr)
    assert "src (coverage cc-only to istanbul=1)" in res.stderr, res.stderr


# A second scope the lane never measures, holding a copy of tangled.
LIB_SCOPE = '[[scope]]\nname = "lib"\npaths = ["lib"]\nlanguages = ["typescript"]\ncoverage_optional = true\n\n'
EXPORT = ".crapkit/export.tsv"


def export_rows(repo: Path) -> dict[tuple[str, str], dict[str, str]]:
    """`coverage --export`'s rows by (path, long_name)."""
    header, *rows = [line.split("\t") for line in (repo / EXPORT).read_text(encoding="utf-8").splitlines()]
    return {(row["path"], row["long_name"]): row for row in (dict(zip(header, line)) for line in rows)}


def marks_under(text: str, prefix: str) -> dict[tuple[str, str], str]:
    """The marks whose path opens with `prefix`, as written: (path, key name) -> mark."""
    return {(path, name): mark for path, name, mark in
            (line.split("\t") for line in text.splitlines() if line.startswith(prefix))}


def lines_of_scope(text: str, scope: str) -> list[str]:
    """The scope's `#scope` line and its marks, byte for byte."""
    return [line for line in text.splitlines(keepends=True)
            if line.startswith((f"#scope\t{scope}\t", f"{scope}/"))]


def test_a_flip_seed_raises_each_mark_to_the_exported_crap_and_leaves_the_other_scope(repo: Path):
    """Accuracy checks for a scoped seed on the cc-only to istanbul flip: each
    raised mark equals the export's CRAP at 4 places (1) and ccn^2 x (1 - cov)^3
    + ccn from the export's own ccn and cov columns (2); every line of the other
    scope keeps its bytes (3); and verify passes."""
    (repo / "lib").mkdir()
    (repo / "lib" / "knot.ts").write_text(TANGLED.replace("tangled", "knot"), encoding="utf-8")
    git_commit_all(repo, "lib: a copy of tangled in a cc-only scope")
    optional = TOML.split("[[lane]]")[0].replace('languages = ["typescript"]\n',
                                                 'languages = ["typescript"]\ncoverage_optional = true\n')
    (repo / "crapkit.toml").write_text(optional + LIB_SCOPE, encoding="utf-8")
    assert run_cli(repo, "coverage", "--json").returncode == 0
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    before = (repo / RATCHET).read_text(encoding="utf-8")
    (repo / "crapkit.toml").write_text(TOML + "\n" + LIB_SCOPE, encoding="utf-8")
    assert run_cli(repo, "coverage", "--export", EXPORT).returncode == 0

    seeded = run_cli(repo, "ratchet", "seed", "--scope", "src")

    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    after = (repo / RATCHET).read_text(encoding="utf-8")
    exported = export_rows(repo)
    raised = marks_under(after, "src/")
    assert raised != marks_under(before, "src/") and "raised 1 (first src/tangled.ts" in seeded.stdout, \
        seeded.stdout
    for key, mark in raised.items():
        row = exported[key]
        ccn, cov = int(row["ccn"]), float(row["cov"])
        assert mark == f"{float(row['crap']):.4f}", (key, mark, row)
        assert mark == f"{ccn ** 2 * (1 - cov) ** 3 + ccn:.4f}", (key, mark, row)
    assert lines_of_scope(after, "lib") == lines_of_scope(before, "lib") != []
    res = run_cli(repo, "verify", "--json")
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)


def _write(path: Path, stamp: str, rows: str) -> str:
    path.write_text(f"{stamp}path\tlong_name\tcrap\n{rows}", encoding="utf-8")
    return str(path)


def test_merge_refuses_two_sides_that_each_moved_the_stamp_differently(tmp_path: Path):
    base = _write(tmp_path / "base", "# crapkit-analysis=1 lizard=1.17.10\n", "src/a.ts\tf( )\t50.0000\n")
    ours = _write(tmp_path / "ours", "# crapkit-analysis=2 lizard=1.17.10\n", "src/a.ts\tf( )\t30.0000\n")
    theirs = _write(tmp_path / "theirs", "# crapkit-analysis=3 lizard=1.24.0\n", "src/a.ts\tf( )\t20.0000\n")

    res = run_cli(tmp_path, "ratchet", "merge", base, ours, theirs)
    assert res.returncode == 3, (res.returncode, res.stdout)
    assert "crapkit-analysis=2 lizard=1.17.10" in res.stderr
    assert "crapkit-analysis=3 lizard=1.24.0" in res.stderr
    assert Path(ours).read_text(encoding="utf-8").endswith("src/a.ts\tf( )\t30.0000\n"), \
        "a refused merge must not rewrite ours"


def test_merge_takes_the_stamp_of_the_one_side_that_moved_it(tmp_path: Path):
    base = _write(tmp_path / "base", "# crapkit-analysis=2 lizard=1.17.10\n", "src/a.ts\tf( )\t50.0000\n")
    ours = _write(tmp_path / "ours", "# crapkit-analysis=2 lizard=1.17.10\n", "src/a.ts\tf( )\t30.0000\n")
    theirs = _write(tmp_path / "theirs", "# crapkit-analysis=3 lizard=1.24.0\n", "src/a.ts\tf( )\t50.0000\n")

    res = run_cli(tmp_path, "ratchet", "merge", base, ours, theirs)
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    assert Path(ours).read_text(encoding="utf-8") == (
        "# crapkit-analysis=3 lizard=1.24.0\npath\tlong_name\tcrap\nsrc/a.ts\tf( )\t30.0000\n")


def test_merge_of_matching_stamps_keeps_that_stamp_not_the_running_one(tmp_path: Path):
    stamp = "# crapkit-analysis=2 lizard=1.17.10\n"
    base = _write(tmp_path / "base", stamp, "src/a.ts\tf( )\t50.0000\n")
    ours = _write(tmp_path / "ours", stamp, "src/a.ts\tf( )\t30.0000\n")
    theirs = _write(tmp_path / "theirs", stamp, "src/a.ts\tf( )\t20.0000\n")

    res = run_cli(tmp_path, "ratchet", "merge", base, ours, theirs)
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    merged = Path(ours).read_text(encoding="utf-8")
    assert merged.splitlines()[0] == "# crapkit-analysis=2 lizard=1.17.10"
    assert merged.splitlines()[2] == "src/a.ts\tf( )\t20.0000", "both changed: the merge keeps the min"


def test_the_merge_driver_needs_no_analysis_stack_to_write_a_stamp(tmp_path: Path):
    # git runs the driver in its own temp dir. Stamping the result from the
    # RUNNING metric would import lizard to write three lines, and a driver that
    # can fail on an absent dependency turns every merge into a conflict.
    stamp = "# crapkit-analysis=2 lizard=1.17.10\n"
    base = _write(tmp_path / "base", stamp, "src/a.ts\tf( )\t50.0000\n")
    ours = _write(tmp_path / "ours", stamp, "src/a.ts\tf( )\t30.0000\n")
    theirs = _write(tmp_path / "theirs", stamp, "src/a.ts\tf( )\t20.0000\n")
    blind = os.pathsep.join([str(FIXTURES / "shims" / "lizard"), os.environ.get("PYTHONPATH", "")])

    res = run_cli(tmp_path, "ratchet", "merge", base, ours, theirs, env_extra={"PYTHONPATH": blind})
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    assert Path(ours).read_text(encoding="utf-8").splitlines()[0] == stamp.strip()


def test_merge_of_two_unstamped_sides_stays_unstamped(tmp_path: Path):
    base = _write(tmp_path / "base", "", "src/a.ts\tf( )\t50.0000\n")
    ours = _write(tmp_path / "ours", "", "src/a.ts\tf( )\t30.0000\n")
    theirs = _write(tmp_path / "theirs", "", "src/a.ts\tf( )\t50.0000\n")

    res = run_cli(tmp_path, "ratchet", "merge", base, ours, theirs)
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)
    assert Path(ours).read_text(encoding="utf-8").splitlines()[0] == "path\tlong_name\tcrap", \
        "stamping legacy marks with the running metric would assert a measurement nobody made"


# --- a real `git merge` through the driver, installed as docs/ratchet.md says --------
# Branch A (main, OURS) holds the file `ratchet seed --scope web` writes after a
# typescript raise; branch B (feature, THEIRS) lowers marks as a verify tighten
# does. Both files are written by hand, so each case names exact bytes; the
# walkthrough after them writes every file with a real command.

DOC = Path(__file__).resolve().parents[2] / "docs" / "ratchet.md"
API = "analysis python=13; coverage coveragepy=1"
WEB13 = "analysis typescript=13; coverage istanbul=1"
WEB14 = "analysis typescript=14; coverage istanbul=1"
WEB15 = "analysis typescript=15; coverage istanbul=1"
ROWS = {"api/a.py\tf( )": "50.0000", "api/b.py\tg( )": "40.0000", "web/c.ts\th( )": "30.0000"}


def _driver_lines() -> tuple[str, list[list[str]]]:
    """The `.gitattributes` line and the `git config` commands of the page's
    first two fences, with the driver run by this interpreter."""
    section = DOC.read_text(encoding="utf-8").split("## The git merge driver\n", 1)[1]
    attributes, config = re.findall(r"^```\n(.*?)^```", section, re.M | re.S)[:2]
    here = f"{shlex.quote(Path(sys.executable).as_posix())} -m crapkit"
    commands = [shlex.split(line.replace('"crapkit ratchet merge', f'"{here} ratchet merge'))
                for line in config.splitlines()]
    return attributes, commands


def _marks_text(web: str, rows: dict) -> str:
    head = ("# crapkit-stamp=2 lizard=1.24.0: crapkit-ratchet.tsv holds one stamp per scope, which "
            "crapkit 0.9.0 or later reads")
    lines = [head, "# crapkit-keys=1", f"#scope\tapi\t{API}", f"#scope\tweb\t{web}", "path\tlong_name\tcrap"]
    return "\n".join([*lines, *(f"{key}\t{crap}" for key, crap in sorted(rows.items()))]) + "\n"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo,
                          env=child_env(), capture_output=True, text=True, encoding="utf-8")


def _branches(tmp_path: Path, a: tuple[str, dict], b: tuple[str, dict]) -> Path:
    """main at the base file, then A committed on main and B on feature."""
    repo = git_init_repo(tmp_path)
    attributes, commands = _driver_lines()
    (repo / ".gitattributes").write_text(attributes, encoding="utf-8")
    (repo / RATCHET).write_text(_marks_text(WEB13, ROWS), encoding="utf-8", newline="\n")
    git_commit_all(repo, "base marks")
    for command in commands:
        assert subprocess.run(command, cwd=repo, capture_output=True).returncode == 0, command
    for checkout, (web, rows) in ((["-b", "feature"], b), (["main"], a)):
        _git(repo, "checkout", "-q", *checkout)
        (repo / RATCHET).write_text(_marks_text(web, {**ROWS, **rows}), encoding="utf-8", newline="\n")
        git_commit_all(repo, f"marks on {checkout[-1]}")
    return repo


def _merged(repo: Path) -> str:
    return (repo / RATCHET).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_a_reseeded_scope_and_a_tighten_elsewhere_merge_through_git(tmp_path: Path):
    lowered = {"api/a.py\tf( )": "30.0000", "api/b.py\tg( )": "35.0000"}
    repo = _branches(tmp_path, (WEB14, {}), (WEB13, lowered))

    done = _git(repo, "merge", "feature", "-m", "merge feature")

    assert done.returncode == 0, done.stdout + done.stderr
    assert _merged(repo) == _marks_text(WEB14, {**ROWS, **lowered})
    assert ("ratchet merge: scope web's stamp came from OURS and THEIRS changed 2 mark(s); "
            "if verify names web, run ") in done.stderr, done.stderr


def test_a_web_mark_both_branches_moved_merges_at_the_lower_value(tmp_path: Path):
    repo = _branches(tmp_path, (WEB14, {"web/c.ts\th( )": "28.0000"}),
                     (WEB13, {"api/a.py\tf( )": "30.0000", "web/c.ts\th( )": "25.0000"}))

    done = _git(repo, "merge", "feature", "-m", "merge feature")

    assert done.returncode == 0, done.stdout + done.stderr
    assert _merged(repo) == _marks_text(WEB14, {**ROWS, "api/a.py\tf( )": "30.0000", "web/c.ts\th( )": "25.0000"})
    assert "scope web's stamp came from OURS and THEIRS changed 2 mark(s)" in done.stderr, done.stderr


def test_two_branches_that_reseeded_web_differently_leave_a_conflict_naming_web(tmp_path: Path):
    repo = _branches(tmp_path, (WEB14, {}), (WEB15, {}))

    done = _git(repo, "merge", "feature", "-m", "merge feature")

    said = done.stdout + done.stderr
    assert done.returncode == 1, said
    assert "CONFLICT (content): Merge conflict in crapkit-ratchet.tsv" in said, said
    assert (f"ratchet merge refused: OURS and THEIRS moved a stamp differently - scope web: OURS records "
            f"[lizard=1.24.0 {WEB14}] and THEIRS records [lizard=1.24.0 {WEB15}]") in said, said


# --- the same merge, every marks file written by a real command -------------------
# api is cc-only python, web is typescript under an istanbul lane. Branch A (main,
# OURS) runs a crapkit whose typescript analysis number is one higher, then
# `coverage` and `ratchet seed --scope web`; branch B (feature, THEIRS) runs a
# verify that tightens marks the base file holds above their scores.

WALK_TOML = (
    '[crapkit]\ntarget = 6\n\n'
    '[[scope]]\nname = "api"\npaths = ["api"]\nlanguages = ["python"]\ncoverage_optional = true\n\n'
    '[[scope]]\nname = "web"\npaths = ["web"]\nlanguages = ["typescript"]\n\n'
    '[[lane]]\nname = "ui"\ncommand = "python make_cov.py"\n'
    'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["web"]\n'
)
WALK_COV = (
    "import json, os\n"
    "c = os.path.join(os.getcwd(), 'web', 'c.ts')\n"
    "json.dump({c: {'path': c, 'fnMap': {}, 'f': {}, 'branchMap': {}, 'b': {}}}, open('cov.json', 'w'))\n"
)
# Seven decision points each: ccn 7, over the target of 6, so seed marks both.
KNOT_PY = "".join(f"def {name}(n):\n" + "".join(f"    if n > {i}:\n        return {i}\n" for i in range(6))
                  + "    return 0\n\n\n" for name in ("f", "g"))
# The shim a newer crapkit stands for: typescript's analysis number one higher.
RAISE_TS = 'import crapkit.analyze as a\na.ANALYSIS_VERSIONS["typescript"] += 1\n'


def _hand_raised(repo: Path, prefixes: tuple[str, ...]) -> None:
    """Lift each mark under `prefixes` 10 above its score, as a mark a later
    tighten pulls back down."""
    lines = (repo / RATCHET).read_text(encoding="utf-8").splitlines(keepends=True)
    raised = [re.sub(r"\t([0-9.]+)\n$", lambda m: f"\t{float(m[1]) + 10:.4f}\n", line)
              if line.startswith(prefixes) else line for line in lines]
    (repo / RATCHET).write_text("".join(raised), encoding="utf-8", newline="\n")


def _walkthrough(tmp_path: Path, raised: tuple[str, ...]) -> tuple[Path, dict, subprocess.CompletedProcess]:
    """Base seeded by this crapkit with the marks under `raised` lifted; B
    tightens them; A reseeds web under the raised typescript; then git merges
    B into A. Returns the repo, the newer crapkit's env and the merge."""
    repo = tmp_path / "repo"
    for folder in ("api", "web", "shim"):
        (tmp_path / ("shim" if folder == "shim" else f"repo/{folder}")).mkdir(parents=True)
    (repo / "api" / "a.py").write_text(KNOT_PY, encoding="utf-8")
    (repo / "web" / "c.ts").write_text(TANGLED, encoding="utf-8")
    (repo / "crapkit.toml").write_text(WALK_TOML, encoding="utf-8")
    (repo / "make_cov.py").write_text(WALK_COV, encoding="utf-8")
    (repo / ".gitignore").write_text(".crapkit/\ncov.json\n", encoding="utf-8")
    (tmp_path / "shim" / "sitecustomize.py").write_text(RAISE_TS, encoding="utf-8")
    newer = {"PYTHONPATH": os.pathsep.join(filter(None, (str(tmp_path / "shim"), os.environ.get("PYTHONPATH"))))}
    git_init_repo(repo)
    attributes, commands = _driver_lines()
    (repo / ".gitattributes").write_text(attributes, encoding="utf-8")
    for command in commands:
        assert subprocess.run(command, cwd=repo, capture_output=True).returncode == 0, command
    git_commit_all(repo, "init")
    assert run_cli(repo, "coverage", "--json").returncode == 0
    assert run_cli(repo, "ratchet", "seed").returncode == 0
    _hand_raised(repo, raised)
    git_commit_all(repo, "base marks")

    _git(repo, "checkout", "-q", "-b", "feature")
    tightened = run_cli(repo, "verify")
    assert tightened.returncode == 0, tightened.stdout + tightened.stderr
    assert f"ratchet: 0 dropped, {len(raised)} tightened" in tightened.stdout, tightened.stdout
    git_commit_all(repo, "B: verify tightened the marks")

    _git(repo, "checkout", "-q", "main")
    assert run_cli(repo, "coverage", "--json", env_extra=newer).returncode == 0
    seeded = run_cli(repo, "ratchet", "seed", "--scope", "web", env_extra=newer)
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    assert _web_number(repo) == _web_number(repo, "feature") + 1
    git_commit_all(repo, "A: seed --scope web after the typescript raise")
    return repo, newer, _git(repo, "merge", "feature", "-m", "merge feature")


def _web_number(repo: Path, rev: str | None = None) -> int:
    """The typescript number web's scope line records, in the tree or at `rev`."""
    text = (repo / RATCHET).read_text(encoding="utf-8") if rev is None else _git(repo, "show", f"{rev}:{RATCHET}").stdout
    return int(re.search(r"^#scope\tweb\tanalysis typescript=([0-9]+);", text, re.M)[1])


def test_a_real_scoped_seed_and_a_real_tighten_merge_and_verify_passes(tmp_path: Path):
    repo, newer, merged = _walkthrough(tmp_path, ("api/a.py\tf", "api/a.py\tg"))

    assert merged.returncode == 0, merged.stdout + merged.stderr
    assert "scope web's stamp came from OURS and THEIRS changed 2 mark(s)" in merged.stderr, merged.stderr
    text = _merged(repo)
    assert _web_number(repo) == _web_number(repo, "feature") + 1, text
    assert "api/a.py\tf( n )\t7.0000\napi/a.py\tg( n )\t7.0000\n" in text, "THEIRS's two tightened marks"
    res = run_cli(repo, "verify", env_extra=newer)
    assert res.returncode == 0, (res.returncode, res.stdout, res.stderr)


def test_a_real_merge_where_b_also_tightened_a_web_mark_names_web(tmp_path: Path):
    repo, newer, merged = _walkthrough(tmp_path, ("api/a.py\tf", "api/a.py\tg", "web/c.ts\t"))

    assert merged.returncode == 0, merged.stdout + merged.stderr
    assert "scope web's stamp came from OURS and THEIRS changed 3 mark(s)" in merged.stderr, merged.stderr
    res = run_cli(repo, "verify", env_extra=newer)
    assert res.returncode == 0 or (res.returncode == 7 and "web/" in res.stdout), \
        (res.returncode, res.stdout, res.stderr)
    seeded = run_cli(repo, "ratchet", "seed", "--scope", "web", env_extra=newer)
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
