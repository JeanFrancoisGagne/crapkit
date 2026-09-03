"""`--json` prints one object on stdout even when the command dies (0.5.0).

A crapkit error used to leave stdout empty under `--json`: `coverage --json`
with pytest-cov missing exited 5 with 0 bytes on stdout, so the Action's
comment read "crapkit coverage wrote no run summary" while the one sentence
naming `pip install pytest-cov` stayed in the job log. The error now reaches
stdout as `{"error": {"exit", "kind", "message"}, "schema": 1}`; the stderr
line and the exit code are unchanged, and without `--json` stdout stays empty.
"""
import json

from cli_inproc_repo import repo, seed_artifacts, template_repo  # noqa: F401

from crapkit.cli import main


def run(argv: list[str], repo, capsys) -> tuple[int, str, str]:
    code = main([*argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_a_tool_error_under_json_is_one_object_on_stdout(repo, capsys):
    """Every lane failing is the platform engineer's case: nothing was scored,
    and the comment renderer still gets the sentence that names the fix."""
    code, out, err = run(["coverage", "--reuse-artifacts", "--json"], repo, capsys)

    assert code == 5
    assert "crapkit: every lane failed (2 of 2)" in err, err
    payload = json.loads(out)
    assert payload == {"error": {"exit": 5, "kind": "tool",
                                 "message": "every lane failed (2 of 2); the errors are above"},
                       "schema": 1}


def test_the_error_kind_names_the_class_behind_the_exit_code(repo, capsys):
    """exit 1 is a state the store lacks; exit 3 a configuration refusal."""
    code, out, _ = run(["rescore", "src/app.ts", "--json"], repo, capsys)
    state = json.loads(out)["error"]
    assert (code, state["exit"], state["kind"]) == (1, 1, "state")
    assert state["message"].startswith("no snapshot in ")

    code, out, _ = run(["coverage", "--lane", "nope", "--json"], repo, capsys)
    config = json.loads(out)["error"]
    assert (code, config["exit"], config["kind"]) == (3, 3, "config")
    assert config["message"] == "no lane named 'nope'"


def test_without_json_a_refusal_leaves_stdout_empty(repo, capsys):
    code, out, err = run(["coverage", "--reuse-artifacts"], repo, capsys)

    assert code == 5
    assert out == "", out
    assert "every lane failed" in err
