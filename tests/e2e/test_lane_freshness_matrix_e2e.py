"""Every lane-freshness reader, through the CLI, over every event the stale-touch hunts tried.

A measured repo (stale_tree's one-lane TypeScript fixture, measured by
`crapkit coverage`), then one thing done to src/app.ts or to git's view of it,
then each command that judges the lane: `brief --json` (the dark-line note an
agent and the MCP tool read), `report` (the banner a published page carries),
`coverage --reuse-artifacts` (the warning) and `coverage --reuse-unchanged`
(the reuse proof, for a lane without and with `inputs`). The truth is the
content: a reader names exactly the files whose git blob id moved, and stays
silent when none did. A same-size edit under a restored modification time is
the named limit every reader shares (docs/lanes.md), pinned here as silence.

The loops these rows port: c01 to c07, c28, shape-1 to shape-3, history-1,
history-8 and history-9 (lane freshness); c10, c11, c23 and history-3 (a
failed attempt's leftover) live in test_lane_reuse_refusal_e2e.py and here.
c25's fold and c29's index race run at their seams in tests/unit.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1] / "unit"))

import stale_tree  # noqa: E402
from stale_tree import EVENTS, REL  # noqa: E402

from conftest import run_cli  # noqa: E402

# git-missing stops every command before a lane is judged: crapkit exits 4 on
# `git executable not found`. Its readers are driven in tests/unit.
CLI_EVENTS = sorted(name for name in EVENTS if name != "git-missing")


def _prepared(tmp_path: Path, name: str, inputs: tuple = ()) -> Path:
    """The fixture measured by the coverage command itself, then the event."""
    if name == "symlink-add" and not stale_tree.symlinks_work(tmp_path):
        pytest.skip("needs os.symlink: developer mode or elevation on Windows (runs on ubuntu CI)")
    event = EVENTS[name]
    root = stale_tree.build(tmp_path / "repo", gitcfg=event.gitcfg, attrs=event.attrs,
                            app=event.app, inputs=inputs)
    measured = run_cli(root, "coverage")
    assert measured.returncode == 0, measured.stderr
    return event.act(root) or root


def _truth(name: str, root: Path) -> tuple[str, ...]:
    """A case-only rename moves nothing where the filesystem folds case and
    renames the file where it does not."""
    if name.startswith("case-only-") and not (root / REL).exists():
        return ("src/App.ts", REL)
    return EVENTS[name].moved


def _brief(root: Path) -> dict:
    res = run_cli(root, "brief", REL, "dispatch", "--json")
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


def _banner(root: Path) -> str:
    res = run_cli(root, "report")
    assert res.returncode == 0, res.stderr
    page = (root / ".crapkit" / "report.html").read_text(encoding="utf-8")
    return page.split("lanes are stale", 1)[1].split("</ul>", 1)[0] if "lanes are stale" in page else ""


def _lane_lines(res) -> list[str]:
    return [line for line in res.stderr.splitlines() if "lane 'unit'" in line]


def _names_all(text: str, truth) -> bool:
    return all(path in text for path in truth)


@pytest.mark.parametrize("name", CLI_EVENTS)
def test_each_reader_names_exactly_the_files_whose_bytes_moved(name, tmp_path):
    root = _prepared(tmp_path, name)
    truth = _truth(name, root)

    packet, banner = _brief(root), _banner(root)
    warning = _lane_lines(run_cli(root, "coverage", "--reuse-artifacts"))

    if not truth:
        assert packet["uncovered_lines"] == [9], packet["uncovered_lines_note"]
        assert (banner, warning) == ("", []), (banner, warning)
        return
    (line,) = warning
    assert _names_all(banner, truth) and _names_all(line, truth), (banner, line)
    assert f"{len(truth)} file(s) in its scopes changed since it measured them" in line
    if REL in truth:
        assert packet["uncovered_lines"] is None
        assert f"{REL} changed since coverage/coverage-final.json measured it" in \
            packet["uncovered_lines_note"]
    else:
        assert packet["uncovered_lines"] == [9], "another file arriving keeps this one's lines"


# --reuse-unchanged: "" reuses, anything else is a word the rerun line holds
WHOLE_TREE = {name: "" for name in CLI_EVENTS}
WHOLE_TREE.update({
    "autocrlf-false-crlf-bytes": REL, "content-change": REL, "delete": REL, "rename": REL,
    "add-in-scope": "src/added.ts", "mode-change-staged": REL, "symlink-add": "src/link.ts",
    "renormalize-crlf-blob": REL, "case-only-git-mv": "src/App.ts",
    "shallow-clone-scope-changed": "HEAD is", "shallow-clone-scope-unchanged": "HEAD is",
    "amend-message-only": "HEAD is", "sibling-commit-same-scope-bytes": "HEAD is",
})
WITH_INPUTS = {**WHOLE_TREE, "amend-message-only": "", "sibling-commit-same-scope-bytes": "",
               "shallow-clone-scope-changed": "which this clone does not hold",
               "shallow-clone-scope-unchanged": "which this clone does not hold"}
INPUTS = ("src", "make_cov.py")


def _reuse_unchanged(root: Path) -> str:
    (line,) = _lane_lines(run_cli(root, "coverage", "--reuse-unchanged"))
    return line


def _expected(name: str, root: Path, table: dict) -> str:
    if name == "case-only-rename" and not (root / REL).exists():
        return "src/App.ts"
    return table[name]


@pytest.mark.parametrize("inputs", [(), INPUTS], ids=["whole-tree", "inputs"])
@pytest.mark.parametrize("name", CLI_EVENTS)
def test_reuse_unchanged_reruns_exactly_when_something_the_lane_reads_moved(name, inputs,
                                                                            tmp_path):
    root = _prepared(tmp_path, name, inputs)
    expected = _expected(name, root, WITH_INPUTS if inputs else WHOLE_TREE)

    line = _reuse_unchanged(root)

    if not expected:
        assert "measurement inputs unchanged; reusing without rerun" in line, line
        assert "its proof leaves out" in line, line
        return
    assert "rerunning:" in line and expected in line, line


# --- c07: the hint a partial run prints ------------------------------------------

SECOND_LANE = """
[[lane]]
name = "other"
command = 'python -c "print(1)"'
artifact = "coverage/other.json"
parser = "istanbul"
scopes = ["src"]
"""
DIRTY = "(the working tree has uncommitted changes, so every lane that lists no `inputs` reruns)"
PARTIAL = {"norefresh-control": False, "touch": False, "touch-norefresh": False,
           "crlf-touch-norefresh": False, "same-size-one-tick": False,
           "content-change": True, "add-in-scope": True, "mode-change-staged": True}


@pytest.mark.parametrize("name", sorted(PARTIAL))
def test_a_partial_run_hints_a_dirty_tree_only_when_git_holds_a_change(name, tmp_path):
    """same-size-one-tick is the named limit: git's stat cache calls the file
    clean, and so does the hint."""
    event = EVENTS[name]
    root = stale_tree.build(tmp_path / "repo", gitcfg=event.gitcfg, attrs=event.attrs,
                            app=event.app)
    with (root / "crapkit.toml").open("a", encoding="utf-8") as config:
        config.write(SECOND_LANE)
    stale_tree.git(root, "commit", "-qam", "second lane")
    assert run_cli(root, "coverage", "--lane", "unit").returncode == 0
    event.act(root)

    res = run_cli(root, "coverage", "--lane", "unit")
    (hint,) = [line for line in res.stdout.splitlines() if line.startswith("-> ")]

    assert (DIRTY in hint) == PARTIAL[name], hint


# --- c11 and shape-8: a lane command that only touches its old report -------------

TOUCHING = "import os, time; t = time.time(); os.utime('coverage/coverage-final.json', (t, t))"


@pytest.mark.parametrize("mode", ["touch", "nothing", "same-bytes"])
def test_only_a_lane_that_writes_its_report_is_scored(mode, tmp_path):
    root = stale_tree.measure(stale_tree.build(tmp_path / "repo"))
    saved = (root / "coverage" / "coverage-final.json").read_bytes()
    script = {"touch": TOUCHING, "nothing": "pass",
              "same-bytes": f"open('coverage/coverage-final.json', 'wb').write({saved!r})"}[mode]
    (root / "make_cov.py").write_text(script + "\n", encoding="utf-8")
    stale_tree.git(root, "commit", "-qam", "the lane stops measuring")

    res = run_cli(root, "coverage", "--json")

    stamp = stale_tree.stamp(root)["coverage/coverage-final.json"]
    head = stale_tree.git(root, "rev-parse", "HEAD").strip()
    if mode == "same-bytes":
        assert res.returncode == 0, res.stderr
        assert stamp["commit"] == head
        return
    assert res.returncode == 5, res.stderr
    assert "wrote no artifact this run" in res.stderr
    assert stamp["commit"] != head, "the previous run's file is never stamped as this commit's"
