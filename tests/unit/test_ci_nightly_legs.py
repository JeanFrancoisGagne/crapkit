"""Windows runs Python 3.12 and 3.13 nightly; every push runs 3.11 and 3.14.

The gate audit of 2026-10-01 found no Windows failure in 3 weeks that differed
between Python versions, while the 3.12 and 3.13 Windows legs cost 91.7
machine-min per push. 3.11 and 3.14, the ends of the classified range, stay on
every push and pull request; the middle two run once a night, so a
version-specific break still shows within a day.
"""
from test_ci_parallel_jobs import matrix_rows, workflow

WINDOWS = "windows-latest"
SKIPS_SCHEDULE = "github.event_name != 'schedule'"


def _legs(event):
    matrix = workflow()["jobs"]["test"]["strategy"]["matrix"]
    return {(row["os"], row["python"], row["suite"]) for row in matrix_rows(matrix, events=(event,))}


def test_a_push_or_pull_request_runs_windows_on_the_ends_of_the_classified_range():
    for event in ("push", "pull_request"):
        windows = {python for runner, python, _ in _legs(event) if runner == WINDOWS}
        assert windows == {"3.11", "3.14"}, event


def test_the_nightly_runs_windows_on_3_12_and_3_13_and_nothing_a_push_already_ran():
    nightly = _legs("schedule")

    assert nightly == {(WINDOWS, python, suite) for python in ("3.12", "3.13") for suite in ("unit", "e2e")}
    assert not nightly & _legs("push")


def test_the_schedule_runs_the_test_legs_and_no_job_that_judges_a_push():
    """verdict-measure and dogfood read the event's base commit, which a
    schedule does not have, and the other jobs judge a change a push made.
    deploy-action and its log job ran every night until they skipped too:
    about 15 machine-min a night with no change to judge. The log job keeps
    its job-level always(), so its steps carry the skip."""
    found = workflow()
    jobs = found["jobs"]
    skipped = {name for name, job in jobs.items() if job.get("if") == SKIPS_SCHEDULE}
    skipped |= {name for name, job in jobs.items()
                if all(item.get("if") == SKIPS_SCHEDULE for item in job["steps"])}
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
