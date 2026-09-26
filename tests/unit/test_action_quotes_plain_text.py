"""The pull-request comment quotes crapkit's text as plain text, and the builder's
own usage error stays plain on Python 3.14.

tools/action/comment.py quotes the first line of a lane failure, of an error
object and of the base run's reason. crapkit strips escape codes where it reads
a lane's output, so its own payloads arrive plain; the builder filters again
because it also renders payloads saved from an older crapkit (the README's
saved-payload route), where a lane that FORCE_COLOR or PY_COLORS coloured left
`\\x1b[31mERROR\\x1b[0m` and junit's `#x1B[31m` in the text. The Action posts
the comment verbatim through `gh api`.

The Action runs the builder on the `python-version` input, and on 3.14 argparse
colours a usage error in a pipe once the job sets FORCE_COLOR or PYTHON_COLORS;
`top: five` reaches that error.
"""
import json
import os
import sys
from functools import lru_cache
from pathlib import Path

import hang_guard
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
BUILDER = ROOT / "tools" / "action" / "comment.py"
ESC = "\x1b"

# What a 0.8.0 `coverage --json` carried for a lane pytest coloured, and for an
# xdist lane whose junit report held a coloured collection error.
_COLOURED_TAIL = ("lane 'bad' produced no artifact at .crapkit/cov/bad.json (command exit 2); "
                  "lane log: /repo/.crapkit/lane-bad.log; last output: \x1b[31mERROR\x1b[0m "
                  "tests2/test_broken_1.py\n\x1b[31mERROR\x1b[0m tests2/test_broken_2.py")
_COLOURED_JUNIT = ("junit reports a run that did not finish, so its coverage measures a partial "
                   "suite: collection error: collection failure #x1B[31mImportError while importing "
                   "test module '/repo/tests/test_n3.py'.")


@lru_cache(maxsize=None)
def _builder():
    """Loaded by path, the way the Action runs it."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("crapkit_action_comment_plain", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _coverage(**failures) -> dict:
    return {"functions": 12, "files": 3, "over_target": 2, "crap_load": 45.2, "grade": "B",
            "ceilings": {"default": 6}, "lane_failures": failures}


def _assert_plain(text: str) -> None:
    assert ESC not in text, text
    assert "#x1B" not in text, text


def test_the_scored_line_quotes_a_coloured_lane_failure_as_plain_text():
    line = _builder().scored_line(_coverage(bad=_COLOURED_TAIL))

    _assert_plain(line)
    assert "last output: ERROR tests2/test_broken_1.py." in line


def test_the_no_verdict_line_quotes_a_coloured_lane_failure_as_plain_text():
    line = _builder().no_verdict_line(_coverage(bad=_COLOURED_TAIL), 5)

    _assert_plain(line)
    assert "(lane 'bad' failed: lane 'bad' produced no artifact" in line


def test_a_coloured_junit_refusal_reaches_the_comment_as_plain_text():
    line = _builder().scored_line(_coverage(xdist=_COLOURED_JUNIT))

    _assert_plain(line)
    assert "collection failure ImportError while importing test module" in line


def test_an_error_object_with_colour_is_quoted_plain():
    coverage = {"error": {"exit": 5, "kind": "tool", "message": "\x1b[1;31mlizard is not importable\x1b[0m\n"},
                "schema": 1}

    assert _builder().scored_line(coverage) == "`crapkit coverage` exited 5: lizard is not importable."
    assert "(lizard is not importable)" in _builder().no_verdict_line(coverage, 5)


def test_the_base_reason_is_quoted_plain(tmp_path):
    """action.yml writes `head -n 1` of the base run's stderr into
    crapkit-base.reason, and the verdict line quotes it."""
    verify = tmp_path / "verify.json"
    verify.write_text(json.dumps({"ok": True, "run_id": 2, "baseline_run": 1, "changed_files": 0}),
                      encoding="utf-8")
    (tmp_path / "base.sha").write_text("", encoding="utf-8")
    (tmp_path / "base.reason").write_text(
        f"lane failed at the fork point abc123: crapkit: lane 'bad' FAILED: {_COLOURED_TAIL}", encoding="utf-8")

    _builder().main(["--verify", str(verify), "--base-sha", str(tmp_path / "base.sha"),
                     "--base-reason", str(tmp_path / "base.reason"), "--out", str(tmp_path / "c.md")])
    text = (tmp_path / "c.md").read_text(encoding="utf-8")

    _assert_plain(text)
    assert ("the base run was not made (lane failed at the fork point abc123: crapkit: lane 'bad' FAILED: "
            "lane 'bad' produced no artifact") in text
    assert "last output: ERROR tests2/test_broken_1.py)" in text, "the reason is the file's first line"


@pytest.mark.parametrize("raw, plain", [
    ("a\tb", "a\tb"),
    ("bell\x07 back\x08space nul\x00 del\x7f", "bell backspace nul del"),
    ("\x1b]8;;https://example.com\x07link\x1b]8;;\x07", "link"),
    ("\x1b[?25lhidden cursor", "hidden cursor"),
    ("end\x1b", "end"),
], ids=["tab-kept", "c0-dropped", "osc8", "private-csi", "lone-esc"])
def test_a_quoted_line_keeps_tabs_and_drops_every_other_control(raw, plain):
    assert _builder().scored_line(_coverage(bad=raw)).endswith(f"lane 'bad' failed: {plain}.")


def test_a_plain_payload_renders_as_before():
    line = _builder().scored_line(_coverage(js="lane 'js' wrote no artifact on its last attempt\n  full log: x"))

    assert line.endswith("grade B; lane 'js' failed: lane 'js' wrote no artifact on its last attempt.")


# --- the builder's own argparse ----------------------------------------------

@pytest.mark.parametrize("version, kwargs", [
    ((3, 11, 9), {}), ((3, 13, 5), {}), ((3, 14, 0), {"color": False}), ((3, 15, 0), {"color": False}),
])
def test_the_builder_turns_argparse_colour_off_from_314(version, kwargs):
    """Before 3.14 argparse has no `color` keyword and refuses one."""
    assert _builder()._plain_parser(version) == kwargs


_COLOUR_ENVS = {
    "none": {},
    "FORCE_COLOR=1": {"FORCE_COLOR": "1"},
    "PYTHON_COLORS=1": {"PYTHON_COLORS": "1"},
    "TERM=dumb FORCE_COLOR=1": {"TERM": "dumb", "FORCE_COLOR": "1"},
    "NO_COLOR=1 FORCE_COLOR=1": {"NO_COLOR": "1", "FORCE_COLOR": "1"},
}
_KNOBS = ("FORCE_COLOR", "NO_COLOR", "PYTHON_COLORS", "PY_COLORS", "TERM", "CLICOLOR_FORCE")


@pytest.mark.parametrize("argv", [["--top", "five", "--out", "c.md"], ["--no-such-flag", "--out", "c.md"],
                                  ["--help"]], ids=["top-five", "unknown-flag", "help"])
@pytest.mark.parametrize("colour", list(_COLOUR_ENVS.values()), ids=list(_COLOUR_ENVS))
def test_the_builders_usage_error_and_help_are_plain_in_a_pipe(tmp_path, argv, colour):
    """Runs on the interpreter the suite runs on; the 3.14 CI legs are the ones
    where argparse would colour it."""
    env = {k: v for k, v in os.environ.items() if k not in _KNOBS}
    env.update(colour)
    result = hang_guard.run([sys.executable, str(BUILDER), *argv], cwd=tmp_path, env=env)

    output = result.stdout + result.stderr
    assert ESC.encode() not in output, output
    assert b"usage: comment.py" in output
    assert result.returncode == (0 if argv == ["--help"] else 2)


# --- what the base step keeps of a crash --------------------------------------

@pytest.mark.parametrize("colour", list(_COLOUR_ENVS.values()), ids=list(_COLOUR_ENVS))
def test_the_first_stderr_line_of_a_crash_is_plain(tmp_path, colour):
    """action.yml keeps `head -n 1` of the base run's stderr. From 3.13 the
    interpreter colours an uncaught traceback under FORCE_COLOR or
    PYTHON_COLORS, and a store that is not a database is one way to get one;
    the header line it starts with carries no colour, so the reason stays
    plain whatever crapkit prints first."""
    (tmp_path / "crapkit.toml").write_text('[crapkit]\ntarget = 6\n', encoding="utf-8")
    (tmp_path / ".crapkit").mkdir()
    (tmp_path / ".crapkit" / "crap.sqlite").write_bytes(b"not a sqlite database " * 8)
    env = {k: v for k, v in os.environ.items() if k not in _KNOBS}
    env.update(colour)
    result = hang_guard.run([sys.executable, "-m", "crapkit", "runs"], cwd=tmp_path, env=env)

    first = (result.stderr.splitlines() or [b""])[0]
    assert result.returncode != 0, "a store that is not a database is refused"
    assert first.strip(), result.stderr
    assert ESC.encode() not in first, result.stderr
