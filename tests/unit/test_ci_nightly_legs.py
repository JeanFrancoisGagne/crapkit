"""Windows runs Python 3.12 and 3.13 nightly; every push runs 3.11 and 3.14.

The gate audit of 2026-10-01 found no Windows failure in 3 weeks that differed
between Python versions, while the 3.12 and 3.13 Windows legs cost 91.7
machine-min per push. 3.11 and 3.14, the ends of the classified range, stay on
every push and pull request; the middle two run once a night, so a
version-specific break still shows within a day.

The nightly also runs macOS on Python 3.11. The push leg runs macOS on 3.13,
which has os.waitid, so the path a Python without it takes, the one that
crashed every lane on macOS in 0.8.0, never ran in CI.
"""
from test_ci_parallel_jobs import matrix_rows, workflow
from test_ci_verdict import ROOT

WINDOWS = "windows-latest"
MACOS = "macos-latest"
SKIPS_SCHEDULE = "github.event_name != 'schedule'"


def _legs(event):
    matrix = workflow()["jobs"]["test"]["strategy"]["matrix"]
    return {(row["os"], row["python"], row["suite"]) for row in matrix_rows(matrix, events=(event,))}


def test_a_push_or_pull_request_runs_windows_on_the_ends_of_the_classified_range():
    for event in ("push", "pull_request"):
        windows = {python for runner, python, _ in _legs(event) if runner == WINDOWS}
        assert windows == {"3.11", "3.14"}, event


def test_the_nightly_runs_windows_on_3_12_and_3_13_macos_on_3_11_and_nothing_a_push_already_ran():
    nightly = _legs("schedule")

    windows = {(WINDOWS, python, suite) for python in ("3.12", "3.13") for suite in ("unit", "e2e")}
    assert nightly == windows | {(MACOS, "3.11", "unit e2e")}
    assert not nightly & _legs("push")


def test_the_schedule_runs_the_test_legs_and_no_job_that_judges_a_push():
    """verdict-measure and dogfood read the event's base commit, which a
    schedule does not have, and the other jobs judge a change a push made.
    deploy-action and its log job ran every night until they skipped too:
    about 15 machine-min a night with no change to judge. Each skips at job
    level, so no runner boots to skip its steps: the log job once kept a bare
    always() and booted a runner every night to skip its three steps."""
    found = workflow()
    jobs = found["jobs"]
    skipped = {name for name, job in jobs.items()
               if SKIPS_SCHEDULE in [part.strip() for part in job.get("if", "").split("&&")]}
    skipped |= {name for name, job in jobs.items() if "github.event_name == 'push'" in job.get("if", "")}
    for name, job in jobs.items():
        needs = job.get("needs", [])
        needs = [needs] if isinstance(needs, str) else needs
        if "always()" not in job.get("if", "") and set(needs) & skipped:
            skipped.add(name)

    assert set(jobs) - skipped == {"test"}
    assert [entry["cron"] for entry in found[True]["schedule"]] == ["41 4 * * *"]


def test_a_leg_keeps_its_check_name_whatever_its_cadence():
    """Required checks match a job by name; a cadence in the name renames every leg."""
    assert workflow()["jobs"]["test"]["name"] == "test (${{ matrix.os }}, ${{ matrix.python }}, ${{ matrix.suite }})"


def _nightly_time():
    minute, hour = workflow()[True]["schedule"][0]["cron"].split()[:2]
    return f"{int(hour):02d}:{int(minute):02d} UTC"


def _section(text, start, end):
    """From `start` to the next `end`, or to the end of the text."""
    begin = text.index(start)
    stop = text.find(end, begin + len(start))
    return text[begin:stop if stop >= 0 else None]


def test_the_docs_say_which_legs_run_only_nightly_and_what_the_schedule_skips():
    """CONTRIBUTING.md and docs/upgrading.md said Windows ran 3.11 to 3.14 on
    every push after the middle two had moved to the nightly."""
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    upgrading = (ROOT / "docs/upgrading.md").read_text(encoding="utf-8")
    row = _section(contributing, "| `test` |", "\n")
    evidence = _section(upgrading, "## Release evidence", "\n#")
    jobs = _section(contributing, "**In CI**", "\n\n")

    for text in (row, evidence):
        assert _nightly_time() in text and "3.12 and 3.13" in text and "macOS" in text
        assert "3.11, 3.12, 3.13 and 3.14 on Ubuntu and Windows" not in text
    assert "skips every job except `test`" in jobs


def test_the_push_median_samples_only_push_runs_while_ci_runs_nightly():
    """accuracy.yml's nightly takes the accuracy-push median over ci.yml's last 7
    successful main runs. The 04:41 schedule adds a successful main run every
    night with accuracy-push skipped, and a skipped matrix job lists under its
    bare name, with no 'ubuntu' to sample. At about 3 pushes a week the 7 newest
    were mostly nightlies, and a window of only nightlies passed the 6-minute
    check without a measurement."""
    accuracy = (ROOT / ".github/workflows/accuracy.yml").read_text(encoding="utf-8")
    query = next(line for line in accuracy.splitlines() if "workflows/ci.yml/runs?" in line)

    assert "event=push" in query.split("?", 1)[1].split('"', 1)[0].split("&")
