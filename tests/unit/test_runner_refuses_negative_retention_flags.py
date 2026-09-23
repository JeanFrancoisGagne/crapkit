"""A negative retention flag is a usage error of the runner's own command line,
not incomplete test evidence."""
import pytest

from test_runner_owns_test_evidence_retention import finished_run, runner


@pytest.mark.parametrize("flag", ["--retention-days", "--retention-count"])
def test_a_negative_retention_flag_exits_2_with_usage(flag, tmp_path, capsys):
    run = finished_run(tmp_path, "run-old", 30)

    with pytest.raises(SystemExit) as exit_:
        runner().main(["--repo", str(tmp_path), "--preview-retention", flag, "-1"])

    err = capsys.readouterr().err
    assert exit_.value.code == 2
    assert err.startswith("usage: ")
    assert f"argument {flag}: must be an integer >= 0, got '-1'" in err
    assert "test evidence is incomplete" not in err
    assert run.is_dir()


def test_zero_is_accepted_and_disables_the_limit(tmp_path, capsys):
    finished_run(tmp_path, "run-old", 30)

    assert runner().main(["--repo", str(tmp_path), "--preview-retention",
                          "--retention-days", "0", "--retention-count", "0"]) == 0
    assert '"planned": []' in capsys.readouterr().out


def test_a_word_is_still_refused_as_not_a_number(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_:
        runner().main(["--repo", str(tmp_path), "--preview-retention", "--retention-days", "week"])

    assert exit_.value.code == 2
    assert "argument --retention-days: must be an integer >= 0, got 'week'" in capsys.readouterr().err


@pytest.mark.parametrize("limits", [{"keep": -1}, {"days": -1}])
def test_a_direct_caller_passing_a_negative_limit_gets_a_value_error(limits, tmp_path):
    run = finished_run(tmp_path, "run-old", 30)

    with pytest.raises(ValueError, match="retention limits must be nonnegative"):
        runner().prune_test_runs(tmp_path, **limits)
    assert run.is_dir()
