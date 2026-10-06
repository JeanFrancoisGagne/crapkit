"""Transcripts the pages print, held to what the CLI prints for the same step.

A reader following the quickstarts diffs each printed block against their
terminal. `doctor` gained a first line naming its resource policy, `verify OK`
gained the ratchet tail those exact steps write, `rescore --gate` a closing
verdict line, and none of the pages followed. Each check here takes the line
from the code that prints it, then looks for it on the page.
"""
import html
import json
import re
from pathlib import Path

import pytest

from cli_inproc_repo import repo, template_repo  # noqa: F401

from crapkit.cli import main
from crapkit.cli.verifying import _ratchet_suffix
from crapkit.config import Lane
from crapkit.errors import ToolError

ROOT = Path(__file__).resolve().parents[2]
MARKS = "crapkit-ratchet.tsv"
HANDBOOK = "docs/handbook.html"


def _page(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _page_text(rel: str) -> str:
    """The page as a reader sees it. The handbook is HTML: each <pre> block
    becomes a fence, tags go, and `&gt;` reads as `>` again."""
    text = _page(rel)
    if rel.endswith(".html"):
        text = re.sub(r"</?pre\b[^>]*>", "\n```\n", text)
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return text


def _output_under(lines: list[str]) -> list[str]:
    """Lines up to the next prompt, blank line or fence."""
    out = []
    for line in lines:
        if not line.strip() or line.startswith(("$ ", "```")):
            break
        out.append(line)
    return out


def _blocks(text: str, command: str) -> list[list[str]]:
    """The output lines under each `$ <command>` line inside a fenced block."""
    lines = text.splitlines()
    return [_output_under(lines[i + 1:]) for i, line in enumerate(lines)
            if line.strip() == f"$ {command}"]


def _doctor_first_line(root: Path, capsys) -> str:
    main(["doctor", "--repo", str(root)])
    return capsys.readouterr().out.splitlines()[0]


FINDINGS = ("ok ", "WARN", "FAIL", "note")
DOCTOR_PAGES = ("README.md", "docs/lanes.md", "docs/configuration.md")


@pytest.mark.parametrize("page", DOCTOR_PAGES)
def test_each_doctor_report_opens_with_the_resources_line(page, repo, capsys):
    """Printed first since 0.8.0's resource policy; a report shown without it
    starts at a line the reader's terminal shows second."""
    shape = re.escape(_doctor_first_line(repo, capsys))
    shape = re.sub(r"\d+", r"\\d+", shape)
    reports = [b for b in _blocks(_page(page), "crapkit doctor")
               if any(line.startswith(FINDINGS) for line in b)]

    assert reports, f"{page} prints no doctor report"
    for report in reports:
        assert re.fullmatch(shape, report[0]), (page, report[:2])


def _verdict_of(report: list[str]) -> str:
    from crapkit.cli.admin import _doctor_verdict
    from crapkit.doctor import Finding

    return _doctor_verdict([Finding(line[:4].strip(), line[5:]) for line in report
                            if line.startswith(FINDINGS)])


@pytest.mark.parametrize("page", DOCTOR_PAGES)
def test_each_doctor_report_closes_on_the_verdict_its_findings_print(page):
    """The closing line counts the WARNs above it since a container's lane
    WARN closed on a bare "no problems found"; a report still showing the bare
    line under a WARN is one the reader's terminal no longer prints."""
    reports = [b for b in _blocks(_page(page), "crapkit doctor") if b and b[-1].startswith("doctor:")]

    assert reports, f"{page} prints no doctor verdict"
    for report in reports:
        assert report[-1] == _verdict_of(report), (page, report[-2:])


# --- the runner line doctor prints for each lane ---------------------------------
#
# doctor names each lane's runner on a line of its own. The two checks above
# read a report's first line and its verdict only, so a page could drop every
# runner line and stay green.

RUNNER_LINE = re.compile(r"(ok  |note) lane '[^']*': (runs |runner unknown)")
_TOML_BLOCK = re.compile(r"^```toml\n(.*?)^```", re.M | re.S)


def _printed(findings, capsys) -> list[str]:
    """The lines doctor prints for these findings, less the closing verdict."""
    from crapkit.cli.admin import _print_findings

    _print_findings(findings)
    return capsys.readouterr().out.splitlines()[:-1]


def _lane_reports(lines: list[str]) -> list[tuple[int, list[str]]]:
    """(line index, output) for each `$ crapkit doctor` report that names a lane."""
    found = [(i, _output_under(lines[i + 1:])) for i, line in enumerate(lines)
             if line.strip() == "$ crapkit doctor"]
    return [(i, report) for i, report in found
            if any(line.startswith(FINDINGS) and (" lane '" in line or "lane(s) declared" in line)
                   for line in report)]


def _config_above(lines: list[str], at: int):
    """The last toml block above line `at` that declares a lane, the config the
    report under it reads, loaded with a scope for each name its lanes claim
    and it does not declare: the lanes page opens on a lone [[lane]]."""
    import tomllib

    from crapkit.config import load_config_text

    *_, body = [block for block in _TOML_BLOCK.findall("\n".join(lines[:at]) + "\n")
                if re.search(r"^\[\[lane\]\]$", block, re.M)]
    raw = tomllib.loads(body)
    declared = {scope["name"] for scope in raw.get("scope", ())}
    missing = sorted({name for lane in raw["lane"] for name in lane["scopes"]} - declared)
    scopes = "".join(f'[[scope]]\nname = "{name}"\npaths = ["{name}"]\nlanguages = ["python"]\n'
                     for name in missing)
    return load_config_text(scopes + body)


def test_every_doctor_report_that_lists_lanes_prints_each_lane_s_runner_line(capsys):
    """README's quickstart and the lanes page show doctor over the lane the
    page declares above the report. Each report carries the runner lines doctor
    prints for that config, no more and no fewer."""
    from crapkit.cli.admin import NO_PACKAGES, _doctor_runners

    checked = []
    for page in DOCTOR_PAGES:
        lines = _page(page).splitlines()
        for at, report in _lane_reports(lines):
            expected = _printed(_doctor_runners(_config_above(lines, at), NO_PACKAGES), capsys)
            checked.append((f"{page}:{at + 1}", [line for line in report if RUNNER_LINE.match(line)],
                            expected))

    assert len(checked) >= 3, "the reports stopped being found, so nothing is checked"
    assert [row for row in checked if row[1] != row[2]] == []


def test_the_runner_section_shows_the_line_doctor_prints_for_each_place_it_reads(capsys):
    """One lane per place a runner is read from: the command, the package.json
    script it runs, devDependencies, and nowhere; then a lane naming two."""
    from crapkit.cli.admin import PackageMap, _doctor_runners
    from crapkit.config import Config
    from crapkit.scaffold import npm_package

    shapes = [("py", "python -m pytest --cov", None),
              ("js", "npm run test -- --coverage", {"scripts": {"test": "vitest run"}}),
              ("js", "make cov", {"devDependencies": {"vitest": "^2.0.0"}}),
              ("js", "npm run cov", None),
              ("both", "python -m pytest --cov && npx vitest run --coverage", None)]
    text = _page("docs/lanes.md")
    section = text[text.index("## How crapkit reads a lane's runner"):]
    block = section[section.index("```\n") + 4:]
    printed = [line for name, command, package in shapes
               for line in _printed(_doctor_runners(
                   Config(lanes=(Lane(name, command, "cov.json", "istanbul", ("src",)),)),
                   PackageMap({"": npm_package(package)} if package else {}, {})), capsys)]

    assert block[:block.index("```")].splitlines() == printed


# --- the refusals a lane's runner word turns on ----------------------------------
#
# The narrowing, file-filter and container refusals fire on the runner a
# command spells. The checks above skip a refusal block, which has no finding
# line and no verdict, so a reworded refusal left these transcripts stale.

def _one_lane(name: str, command: str, parser: str) -> str:
    return ('[[scope]]\nname = "calc"\npaths = ["calc"]\nlanguages = ["python"]\n'
            f'[[lane]]\nname = "{name}"\ncommand = {json.dumps(command)}\n'
            f'artifact = ".crapkit/cov/{name}.json"\nparser = "{parser}"\nscopes = ["calc"]\n')


def _refusal_transcripts(page: str) -> list[tuple[str, str, str, list[str]]]:
    """(page:line, lane, command, output) for each `$ crapkit doctor` whose
    output is a lane refusal, the command read off the `command = "..."` line
    above it and the lane off the refusal."""
    import tomllib

    lines = _page_text(page).splitlines()
    found = []
    for i, line in enumerate(lines):
        output = _output_under(lines[i + 1:]) if line.strip() == "$ crapkit doctor" else []
        named = re.match(r"crapkit: lane '([^']+)'", output[0]) if output else None
        if not named:
            continue
        written = next(above for above in reversed(lines[:i]) if re.match(r"(# )?command = ", above))
        command = tomllib.loads(written.removeprefix("# "))["command"]
        found.append((f"{page}:{i + 1}", named.group(1), command, output))
    return found


def test_every_narrowing_transcript_prints_what_doctor_prints_under_cmd_exe(monkeypatch, repo,
                                                                            capsys):
    """README, the lanes page and the handbook show doctor refusing a lane that
    narrows a full-suite run. The single quotes and the `;` are cmd.exe cases,
    so each is read as cmd.exe reads it; the `&&` ones read the same under
    either shell."""
    from crapkit import config

    monkeypatch.setattr(config, "SHELL_IS_CMD", True)
    checked = []
    for page in ("README.md", "docs/lanes.md", HANDBOOK):
        for where, name, command, output in _refusal_transcripts(page):
            (repo / "crapkit.toml").write_text(_one_lane(name, command, "coveragepy"), encoding="utf-8")
            code = main(["doctor", "--repo", str(repo)])
            printed = capsys.readouterr().err.splitlines()
            exit_line = [f"EXIT={code}"] if output[len(printed):] else []
            checked.append((where, output, printed + exit_line))

    assert len(checked) >= 6, "the transcripts stopped being found, so nothing is checked"
    assert [row for row in checked if row[1] != row[2]] == []


def test_the_lanes_page_quotes_the_file_filter_refusal_a_vitest_lane_draws():
    from crapkit.config import load_config_text
    from crapkit.errors import ConfigError

    with pytest.raises(ConfigError) as raised:
        load_config_text(_one_lane("js", "npx vitest run --coverage src/grade.ts", "istanbul"))

    assert f"crapkit: {raised.value}" in _page("docs/lanes.md").splitlines()


def test_the_lanes_page_quotes_the_container_refusal_a_pytest_lane_draws(monkeypatch):
    from crapkit import lanes

    monkeypatch.setenv("CRAPKIT_INSIDE_CONTAINER", "1")
    lane = Lane("py", "python -m pytest --cov", ".crapkit/cov/py.json", "coveragepy", ("calc",))
    with pytest.raises(ToolError) as raised:
        lanes._refuse_container_python(lane)

    assert f"crapkit: lane 'py' FAILED: {raised.value}" in _page("docs/lanes.md").splitlines()


def test_the_lanes_page_quotes_the_container_warn_doctor_prints_before_that_refusal(capsys):
    """The WARN called every lane it reached a coverage.py suite after the rule
    moved to the command, and nothing held the page to the line doctor prints."""
    from crapkit.doctor import container_lane_findings

    lane = Lane("py", "python -m pytest --cov", ".crapkit/cov/py.json", "coveragepy", ("calc",))
    printed = _printed(container_lane_findings([lane], "/.dockerenv exists"), capsys)

    assert printed and printed[0] in _page("docs/lanes.md").splitlines(), printed


def test_the_quickstarts_verify_ok_lines_carry_the_ratchet_tail_their_steps_write():
    """Step 6 (Python) and step 7 (TypeScript) repay the one seeded mark, so
    the tighten drops it and the OK line asks for the `git add`."""
    tail = _ratchet_suffix({"dropped": 1, "tightened": 0}, [], MARKS)
    ok_lines = [line for block in _blocks(_page("README.md"), "crapkit verify")
                for line in block if line.startswith("verify OK")]

    assert len(ok_lines) == 2, ok_lines
    assert all(line.endswith(f"changed files){tail}") for line in ok_lines), ok_lines


def test_the_restamp_transcript_carries_the_tail_the_restamp_prints():
    tail = _ratchet_suffix({"dropped": 0, "tightened": 0}, [], MARKS)
    text = _page("docs/ratchet.md")
    block = text[text.index("warning: crapkit-ratchet.tsv carries no metric stamp"):]
    ok = next(line for line in block.splitlines() if line.startswith("verify OK"))

    assert ok.endswith(tail), ok


def test_the_ratchet_page_quotes_the_stand_in_warning_a_missing_marks_file_draws(monkeypatch,
                                                                                  capsys):
    """The page quotes the line verify prints when it judges against the marks
    the baseline's commit held, spelled as the console script the page runs."""
    from types import SimpleNamespace

    from crapkit.cli.verifying import _warn_marks_stand_in

    monkeypatch.setattr("sys.argv", ["/usr/local/bin/crapkit", "verify"])
    committed = SimpleNamespace(entries=[object()])
    _warn_marks_stand_in(SimpleNamespace(text=None), committed, "8c780bb18da", MARKS)
    [line] = capsys.readouterr().err.splitlines()

    assert line in _page("docs/ratchet.md").splitlines()


def test_the_typescript_rescore_gate_block_ends_with_the_gate_line():
    """A passing `rescore --gate` prints one stdout line after the table, so
    the exit code is not the only signal; the page stopped at the table."""
    [block] = _blocks(_page("README.md"), "crapkit rescore src/grade.ts --gate")

    assert block[-1] == "gate: 4 changed function(s) judged, 0 over ceiling 6", block


def test_the_typescript_quickstart_says_why_the_first_doctor_warns():
    """init writes the vitest scoped_tests line commented, so the first doctor
    WARNs; the quickstart named no doctor step and no reason for the WARN."""
    text = " ".join(_page("README.md").split())
    quickstart = text[text.index("## Quickstart: TypeScript"):
                      text.index("### 2. Install a coverage provider")]

    assert "has a lane but no [crapkit.scoped_tests] template" in quickstart


def test_the_crapkit_directory_listing_names_the_measurement_lock():
    """lanes.py holds `.crapkit/measurement.lock` while lanes run and leaves it
    behind; the listing showed a tree without it."""
    text = _page("docs/lanes.md")
    names = _listing_names(text)
    table = text[text.index("| Path | What it holds | Key |"):]
    rows = [name.replace("lane-py.log", "lane-<name>.log") for name in names
            if name not in ("junit-py.xml", "py.json", "cov")]

    assert "measurement.lock" in names, names
    assert [row for row in rows if f"`{row}`" not in table] == [], "listed but never explained"


def test_the_crapkit_directory_listing_names_the_cache_files_this_version_writes():
    """The churn and coupling caches carry a version in their file names. A
    rename that left the listing behind would teach a reader to look for files
    crapkit no longer writes."""
    from crapkit import churn_cache, churn_commits, churn_log, coupling_cache

    written = {churn_cache.CACHE_NAME, churn_commits.COMMITS_NAME, churn_log.LOG_NAME,
               churn_log._key_path(Path(churn_log.LOG_NAME)).name, coupling_cache.CACHE_NAME}
    listed = {name for name in _listing_names(_page("docs/lanes.md"))
              if name.startswith(("churn-", "coupling-"))}

    assert listed == written


def _listing_names(text: str) -> list[str]:
    listing = text[text.index("$ ls .crapkit .crapkit/cov"):]
    listing = listing[:listing.index("```")]
    return [line for line in listing.splitlines()[2:] if line and not line.endswith(":")]


def test_the_lanes_page_quotes_the_refusal_an_fnmap_entry_without_decl_draws(tmp_path):
    from crapkit import coverage_istanbul

    source = str(tmp_path / "src" / "a.ts")
    artifact = tmp_path / "coverage-final.json"
    artifact.write_text(json.dumps({source: {
        "path": source, "fnMap": {"0": {"name": "f", "loc": {"start": {"line": 1},
                                                             "end": {"line": 2}}}},
        "f": {"0": 1}, "branchMap": {}, "b": {}, "statementMap": {}, "s": {}}}),
        encoding="utf-8")
    lane = Lane("js", "x", "coverage-final.json", "istanbul", ("src",))
    with pytest.raises(ToolError) as raised:
        coverage_istanbul.read(lane, tmp_path, artifact)
    printed = str(raised.value).replace(str(artifact),
                                        "/repo/.crapkit/cov/js/coverage-final.json")

    assert f"crapkit: lane 'js' FAILED: {printed}\n" in _page("docs/lanes.md"), printed


def test_the_lanes_page_quotes_the_held_line_tune_prints_for_its_testpath_lanes():
    """The page shows doctor --tune holding the lane slots at 1 for its two
    testpath lanes once one drops its `env` line. A lane left on `.coverage`
    deletes and combines the other's `.coverage.py-impl`, and the line quoted
    there still spoke of one shared file and named one lane's fix."""
    from crapkit.doctor import shared_coverage_data, suggest_knobs, tune_lines

    lanes = [Lane("py-conform", "python -m pytest conform --cov", "a.json", "coveragepy",
                  ("impl",)),
             Lane("py-impl", "python -m pytest impl --cov", "b.json", "coveragepy", ("impl",),
                  env=(("COVERAGE_FILE", ".coverage.py-impl"),))]
    knobs = suggest_knobs(cpus=16, lanes=2, shared=shared_coverage_data(lanes))
    held = tune_lines(cpus=16, knobs=knobs, durations=())[3]

    assert held.startswith("# held at 1: "), held
    assert f"\n{held}\n" in _page("docs/lanes.md"), held


# --- the next step worklist and seed end with -----------------------------------

TRANSCRIPT_PAGES = ("README.md", HANDBOOK,
                    *sorted(str(p.relative_to(ROOT)).replace("\\", "/")
                            for p in (ROOT / "docs").glob("*.md")))


def _as_typed(lines: list[str]) -> list[str]:
    """The lines as the pages spell crapkit: the console script a reader types."""
    from crapkit.invocation import _self

    return [line.replace(_self(), "crapkit") for line in lines]


def _outputs(page: str, prefix: str) -> list[tuple[str, list[str]]]:
    lines = _page_text(page).splitlines()
    return [(page, _output_under(lines[i + 1:])) for i, line in enumerate(lines)
            if line.startswith(f"$ {prefix}")]


def _printed_blocks(prefix: str, opening: str) -> list[tuple[str, list[str]]]:
    """(page, output) for every `$ <prefix>...` whose output opens with `opening`."""
    found = [block for page in TRANSCRIPT_PAGES for block in _outputs(page, prefix)]
    return [(page, block) for page, block in found if _opens_with(block, opening)]


def _opens_with(block: list[str], opening: str) -> bool:
    return bool(block) and block[0].startswith(opening)


def _worklist_endings(root: Path, header: str) -> list[list[str]]:
    """What worklist prints last for the run its header names: over an
    inventory run, and over a trusted run before and after the seed."""
    from types import SimpleNamespace

    from crapkit.cli.queue import _worklist_next

    run_id = int(re.search(r"\(run (\d+),", header).group(1))
    cfg, marks = SimpleNamespace(ratchet_file=MARKS), root / MARKS
    marks.unlink(missing_ok=True)
    endings = [_worklist_next(root, cfg, {"id": run_id, "kind": kind})
               for kind in ("inventory", "coverage")]
    marks.write_text("", encoding="utf-8")
    endings.append(_worklist_next(root, cfg, {"id": run_id, "kind": "coverage"}))
    return [_as_typed(end) for end in endings]


def _ends_as_printed(root: Path, block: list[str]) -> bool:
    return any(block[-len(end):] == end for end in _worklist_endings(root, block[0]))


def test_every_worklist_transcript_ends_with_the_step_worklist_prints(tmp_path):
    """worklist printed no next step, and the pages showed it that way; a page
    that stops at the rows now shows a run the CLI no longer prints. The
    handbook is one of those pages: its day-one story runs worklist after
    `inventory`, where the step is `coverage`."""
    blocks = _printed_blocks("crapkit worklist", "worklist @")

    assert len(blocks) >= 6, blocks
    assert HANDBOOK in {page for page, _ in blocks}, "the handbook's worklist was not read"
    assert [(page, block[-2:]) for page, block in blocks
            if not _ends_as_printed(tmp_path, block)] == []


def test_every_seed_transcript_under_the_running_metric_ends_with_commit_then_verify(capsys):
    from crapkit.cli.ratchet_cmds import _print_seed_next

    _print_seed_next("seed", MARKS, "")
    [after] = _as_typed(capsys.readouterr().out.splitlines())
    seeds = [(page, block) for page, block in _printed_blocks("crapkit ratchet seed", f"{MARKS}: added")
             if "was measured under" not in block[0]]

    assert len(seeds) >= 6, seeds
    assert [(page, block[-1]) for page, block in seeds if block[-1] != after] == []
