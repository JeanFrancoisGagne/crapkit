"""What the release pages say about freshness, held to the code that decides it.

0.8.1 changes how crapkit judges whether a file, a lane or a run still describes
the tree: the lane stamp records content, a failed lane's leftover stays refused
until new bytes replace it, agents read `scored_changes` beside `stale`, a failed
git question is named, claude-hook remembers what it judged, and watch compares
content. CHANGELOG.md, docs/upgrading.md, README.md, AGENTS.md, the handbook and
the onboard skill each describe some of that. Each test here reads one claim off
a page and checks it against the code that makes it true, and a line a page
quotes is built by the code that prints it.
"""
from __future__ import annotations

import re
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

import pytest

from crapkit import lanes
from crapkit.cli import verifying

ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=None)
def _page(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _prose(text: str) -> str:
    """Each run of whitespace read as one space, so a phrase the page wraps
    across two lines still reads as one."""
    return " ".join(text.split())


def _readme_row(command: str) -> str:
    """The README subcommand table's row for COMMAND."""
    return next(line for line in _page("README.md").splitlines()
                if line.startswith(f"| `{command} "))


def _release(version: str = "0.8.1") -> str:
    """The CHANGELOG section of one release, heading to the next release."""
    text = _page("CHANGELOG.md")
    start = text.index(f"\n## {version} ")
    end = text.find("\n## ", start + 1)
    return text[start:end if end != -1 else None]


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=root,
                   check=True, capture_output=True)


def _one_commit_repo(root: Path) -> None:
    _git(root, "init", "-q")
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "a.py")
    _git(root, "commit", "-q", "-m", "one")


# -- the 0.8.0 staleness reader stays, and warns ---------------------------------

def test_the_changelog_no_longer_tells_library_callers_to_rename():
    """0.8.1 keeps `lanes.lane_sources_unchanged` through 0.8.x as a wrapper that
    warns. A line telling callers the name was replaced would send them to edit
    code that still works."""
    section = _prose(_release())

    assert "is now `lanes.lane_sources_moved`" not in section
    assert "`lanes.lane_sources_unchanged`" in section
    assert "`DeprecationWarning`" in section and "0.9.0 removes it" in section


def test_the_shim_the_changelog_names_answers_a_bool_and_warns(tmp_path):
    from crapkit.config import Lane

    lane = Lane(name="py", command="true", artifact=".crapkit/cov/py.json",
                parser="coveragepy", scopes=("calc",))

    with pytest.warns(DeprecationWarning):
        answer = lanes.lane_sources_unchanged(tmp_path, lane, {"calc": ("calc",)})

    assert answer is False, "no stamp vouches for the artifact, so its lines are not fresh"


# -- the same-size edit under a restored modification time -----------------------

def test_no_release_line_claims_a_same_size_edit_under_a_restored_mtime_is_caught():
    """git's index answers "unchanged" from its stat data, and 0.8.1 trusts it
    rather than read every scope file on every run. A same-size edit whose old
    modification time was put back passes that check, so a line saying it is
    caught, or that it reruns the lane, promises what the code does not do."""
    section = _prose(_release())

    assert "modification time was put back is caught too" not in section
    assert "so a same-size edit whose old modification time was put back" not in section


def test_the_changelog_names_the_same_size_edit_as_a_limit():
    section = _prose(_release())

    assert "A same-size edit whose old modification time was put back" in section
    assert "is still not seen" in section
    assert "measured for 0.9.0" in section


# -- verify tells a commit the clone lacks from a rewrite -------------------------

_BASELINE = "a74260f321f" + "4e0b9d2c61a8f3e57d0c1b2a9e8f7d6c5"


def _not_behind(shallow: bool, held: bool) -> str:
    """verify's line for a named baseline, in a clone where no branch holds
    the commit, so a commit the clone holds reads as a rewrite."""
    git = SimpleNamespace(is_shallow=lambda: shallow, branches_containing=lambda commit: [])
    return verifying._not_behind(git, _BASELINE, f"baseline commit {_BASELINE[:11]}",
                                 lambda commit: held)


def test_the_readme_quotes_the_refusal_for_a_baseline_the_clone_does_not_hold(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["crapkit"])

    missing = _not_behind(shallow=False, held=False)
    rewritten = _not_behind(shallow=False, held=True)

    assert f"crapkit: {missing}" in _page("README.md")
    assert "not in this clone" in missing and f"git fetch origin {_BASELINE}" in missing
    assert "rewrote history" in rewritten, "a clone that holds the commit still blames a rewrite"


def test_the_readme_no_longer_says_a_full_clone_always_blames_a_rewrite():
    text = _prose(_page("README.md"))

    assert "On a full clone the same exit blames what it used to" not in text
    assert "does not hold it at all" in text


def test_the_changelog_quotes_the_not_in_this_clone_refusal(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["crapkit"])
    missing = _not_behind(shallow=False, held=False)
    head = missing.split(";")[0]

    assert head in _prose(_release())
    assert re.search(r"blamed a rebase or an amend", _prose(_release()))


# -- the history caches key on the clone's depth ---------------------------------

def _has_history_depth() -> bool:
    from crapkit import churn_log

    return hasattr(churn_log, "history_depth")


# Each part of the coupling cache's key, as the handbook names it.
_COUPLING_KEY_WORDS = {"head": "HEAD", "months": "the churn window", "date": "the UTC date",
                       "paths": "the path format", "depth": "history depth",
                       "tracked": "a digest of the tracked set"}


def _unnamed(words: dict, text: str) -> list[str]:
    """The words TEXT does not name."""
    return [word for word in words.values() if word not in text]


def _handbook_sentence(opening: str) -> str:
    """The handbook's one sentence that starts with OPENING."""
    return next(s for s in _prose(_page("docs/handbook.html")).split(". ") if s.startswith(opening))


def test_the_pages_name_every_part_of_the_coupling_cache_key(tmp_path):
    from crapkit import coupling_cache

    _one_commit_repo(tmp_path)
    key = coupling_cache._cache_key(tmp_path, 12, ["a.py"])
    sentence = _handbook_sentence("Its key is HEAD")
    row = _readme_row("coupling")

    assert set(key) == set(_COUPLING_KEY_WORDS)
    assert _unnamed(_COUPLING_KEY_WORDS, sentence) == []
    assert _unnamed({**_COUPLING_KEY_WORDS, "date": "today's UTC date"}, row) == []
    assert "git fetch --unshallow</code> rebuilds it the same day" in sentence
    assert "`git fetch --unshallow` rebuilds them the same day" in row


def test_the_changelog_says_a_deepened_clone_rebuilds_the_history_caches():
    section = _prose(_release())

    assert "`git fetch --unshallow` or `--deepen` at an unmoved HEAD" in section
    assert ("Each file 0.4.5 to 0.8.0 wrote holds a window cut at the wall clock" in section
            and "the first churn read walks the window once" in section)


# -- watch judges content ---------------------------------------------------------

def _watch_polls_content() -> bool:
    from crapkit import watch

    return hasattr(watch, "poll")


def _watch_row() -> str:
    return next(line for line in _page("README.md").splitlines() if line.startswith("| `watch "))


def test_the_watch_row_says_a_touch_rescores_nothing_and_new_bytes_do(tmp_path):
    import os

    from crapkit import watch

    _git(tmp_path, "init", "-q")
    source = tmp_path / "app.py"
    source.write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-q", "-m", "one")
    first = watch.snapshot(tmp_path, ["app.py"])
    later = os.stat(source).st_mtime + 5
    os.utime(source, (later, later))
    touched, moved_by_touch = watch.poll(tmp_path, ["app.py"], first)
    source.write_text("x = 22\n", encoding="utf-8")
    os.utime(source, (later + 5, later + 5))
    _, moved_by_edit = watch.poll(tmp_path, ["app.py"], touched)
    row = _prose(_watch_row())

    assert moved_by_touch == [] and moved_by_edit == ["app.py"]
    assert "a touch or an editor saving the same bytes rescores nothing" in row
    assert "Each poll lists the tracked and untracked files under the scope paths again" in row
    assert "mtime polling" not in row


def test_the_watch_row_names_the_edit_under_an_old_mtime_as_a_limit():
    row = _prose(_watch_row())

    assert "an edit written under the file's old mtime (`cp -p`, `touch -r`) is not seen" in row


def _help_line(command: str) -> str:
    """The one-line help `crapkit --help` prints for COMMAND."""
    from crapkit.cli.parser import build_parser

    parser = build_parser()
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    return next(c.help for c in sub._choices_actions if c.dest == command)


def test_the_changelog_and_the_help_agree_on_what_watch_rescores():
    help_line = _help_line("watch")
    section = _prose(_release())

    assert "from start" not in help_line
    assert "a touch does not" in help_line
    assert "`watch` rescores a file when its bytes change, not when its mtime moves" in section


# -- what claude-hook remembers and what it says it could not judge

def _hook():
    from crapkit.cli import claude_hook

    return claude_hook


def _hook_remembers() -> bool:
    return hasattr(_hook(), "_Memory")


HOOK_PAGES = ("README.md", "AGENTS.md", "plugin/skills/crapkit-onboard/SKILL.md",
              "docs/handbook.html", "CHANGELOG.md", "docs/upgrading.md")


@pytest.mark.parametrize("page", HOOK_PAGES)
def test_each_page_names_where_the_hook_keeps_its_session_memory(page):
    where = ".git/" + "/".join(_hook()._MEMORY_DIR) + "/"

    assert where in _page(page)


def test_the_pages_state_how_long_an_idle_session_is_kept():
    days = _hook()._MEMORY_DAYS

    assert f"idle for {days} days" in _prose(_page("README.md"))
    assert f"idle for {days} days" in _prose(_release())
    assert f"idle for {days} days" in _prose(_page("docs/upgrading.md"))
    assert days == 7 and "idle for a week" in _prose(_page("docs/handbook.html"))


def test_no_page_says_the_hook_writes_nothing():
    row = next(line for line in _page("README.md").splitlines()
               if line.startswith("| `claude-hook "))

    assert "writes nothing" not in row
    assert "the one thing it writes is that session's record of judged bytes" in row
    assert "writes nothing" not in _page("docs/handbook.html")
    assert "nothing in your tree was written" in _prose(_page("AGENTS.md"))


def _unjudged_head(path: str, unread: bool) -> str:
    """The head line of the block the hook prints for an edit it could not
    judge: a file no reader could read, or a change set git could not give."""
    hook = _hook()
    if unread:
        return hook._unread_advisory(path, "the reason")[0]
    return hook._unjudged_lines(f"git could not report what changed in {path}", "the reason",
                                hook._GIT_NEXT)[0]


@pytest.mark.parametrize("unread", [True, False], ids=["unread", "git-failed"])
def test_agents_quotes_the_head_line_of_an_edit_the_hook_could_not_judge(unread):
    path = "src/a.ts" if unread else "calc/grade.py"
    head = _unjudged_head(path, unread).split(" (the edit landed")[0]

    assert f"`{head}" in _prose(_page("AGENTS.md"))


@pytest.mark.parametrize("page", ["CHANGELOG.md", "plugin/skills/crapkit-onboard/SKILL.md"])
def test_the_pages_name_both_unjudged_advisories_as_the_hook_words_them(page):
    text = _prose(_page(page))
    for what, unread in (("PATH could not be read", True),
                         ("git could not report what changed in PATH", False)):
        head = _unjudged_head("PATH", unread).removeprefix("crapkit advisory: ")
        assert what in text, (page, what)
        assert head.split(" (")[0].startswith(what)


def test_the_unread_next_step_agents_gives_is_the_one_the_hook_prints():
    """AGENTS.md tells an agent to fix what the reason names or exclude the
    file; the hook's own closing line says the same two moves."""
    closing = _hook()._unread_advisory("src/a.ts", "why")[-1]

    assert "[exclude] globs" in closing and "`[exclude] globs`" in _page("AGENTS.md")


# -- ratchet prune refuses when the commit its renames start from is gone --------

def _ratchet_cmds():
    from crapkit.cli import ratchet_cmds

    return ratchet_cmds


_FIRST_RUN = {"id": 1, "commit": "35f524b3f89" + "a" * 29}


def test_the_changelog_quotes_the_refusal_prune_raises_for_a_missing_anchor(tmp_path):
    _one_commit_repo(tmp_path)
    refusal = _ratchet_cmds()._unseen_refusal(tmp_path, _FIRST_RUN, ["src/old.py"])
    head = refusal.removeprefix("ratchet prune: ").split(", and prune would")[0]

    assert head in _prose(_release())
    assert "nothing was written" in refusal and "exits 4" in _prose(_release())


def test_the_changelog_prints_the_renames_the_prune_line_names():
    from typing import NamedTuple

    class Mark(NamedTuple):
        path: str
        name: str

    names = _ratchet_cmds()._followed_names([Mark("calc/grade.py", "curve")], [],
                                            {"calc/grade.py": "calc/grading.py"})

    assert f"`followed 1 rename(s){names}`" in _prose(_release())


def test_the_exit_code_table_lists_the_git_refusals_0_8_1_adds():
    row = next(line for line in _page("README.md").splitlines() if line.startswith("| 4 |"))

    assert "missing from this clone" in row and "`ratchet prune`" in row


# -- doctor names the refusal reuse applies ---------------------------------------

def _admin():
    from crapkit.cli import admin

    return admin


def test_the_doctor_row_documents_the_refusal_each_json_lane_carries(tmp_path):
    from crapkit.config import Lane

    lane = Lane(name="py", command="true", artifact=".crapkit/cov/py.json",
                parser="coveragepy", scopes=("calc",))
    report = _admin()._lane_report(tmp_path, lane, _module("lane_stamps").read(tmp_path))
    row = next(line for line in _page("README.md").splitlines() if line.startswith("| `doctor "))

    assert report["refusal"] is None, "no artifact on disk, so nothing to refuse"
    assert "`--json` gives each lane a `refusal`" in row and "or `null`" in row
    assert "`doctor --json` gives each lane a `refusal`" in _prose(_release())


# -- scored_changes beside stale ----------------------------------------------------

def _queue():
    from crapkit.cli import queue

    return queue


def _has_run_freshness() -> bool:
    return hasattr(_queue(), "run_freshness")


def _samples(page: str) -> list[dict]:
    """Every next-item payload a page prints: one JSON object per line."""
    import json

    return [json.loads(line.strip()) for line in _page(page).splitlines()
            if line.strip().startswith('{"comm')]


@pytest.mark.parametrize("page, count", [("README.md", 1), ("AGENTS.md", 2)])
def test_each_next_item_sample_carries_the_envelope_the_command_builds(page, count):
    queue = _queue()
    head = queue._next_head({"id": 1, "commit": "f" * 40}, 0, 0,
                            queue.RunFreshness(False, []), False)
    samples = _samples(page)

    assert len(samples) == count
    for sample in samples:
        assert set(head) <= set(sample), (page, set(head) - set(sample))
        assert sample["scored_changes"] == 0 and sample["commands"] == head["commands"]


def test_agents_and_agent_json_stop_on_the_same_scored_changes_clause():
    rule = _page("AGENTS.md").split("\n## The termination rule\n", 1)[1].split("\n## ", 1)[0]
    stop = _page("docs/agent-json.md")

    assert re.search(r"^    scored_changes +0$", rule, re.M), "the rule's fourth line"
    assert "`scored_changes == 0`" in stop
    assert "null` included" in _prose(rule) and "null` included" in _prose(stop)


def test_the_changelog_quotes_the_worklist_warning_that_names_changed_files(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["crapkit"])
    queue = _queue()
    fresh = queue.RunFreshness(False, ["calc/grade.py", "calc/report.py"])
    (line,) = queue._freshness_warnings(fresh, {"id": 4, "commit": "f" * 40})
    head = line.removeprefix("warning: ").split(" - rerun")[0]

    assert f"`{head}`" in _prose(_release())


def test_the_readme_says_what_scored_changes_and_stale_each_answer():
    section = _prose(_page("README.md").split("### 4. Take the top item", 1)[1].split("### 5.", 1)[0])

    assert "`scored_changes: 0` says no file the run scored holds other content now" in section
    assert "`stale: false` says HEAD is still the run's commit" in section


def test_agents_names_the_batch_envelope_the_command_builds():
    line = next(ln for ln in _page("AGENTS.md").splitlines() if ln.startswith('`{"schema": 1, "run_id"'))
    named = set(re.findall(r'"(\w+)":', line)) - {"refresh"}
    envelope = _queue().RunFreshness(False, []).envelope()

    assert named == {"schema", "run_id", "commit", "packets", *envelope}
    assert envelope["commands"] == {"refresh": "crapkit coverage --reuse-unchanged"}


def _handbook_rules() -> list[str]:
    """The two places the handbook states the stop rule: §11 and the §16 recipe."""
    text = _prose(_page("docs/handbook.html"))
    return [text.split("<h3>The termination rule</h3>", 1)[1].split("</p>", 1)[0],
            next(p for p in text.split("<p>") if p.startswith("Stop when <code>next-item</code>"))]


@pytest.mark.parametrize("which", [0, 1], ids=["queue", "recipe"])
def test_each_handbook_stop_rule_carries_the_scored_changes_clause(which):
    rule = _handbook_rules()[which]

    assert "<code>no_lane_over_target</code>" in rule
    assert re.search(r"<code>scored_changes(: 0</code>|</code> is <code>0</code>)", rule), rule
    assert "Three conditions" not in rule and "two riders" not in _page("docs/handbook.html")


def test_agents_brief_table_reads_scored_changes_before_stale():
    rows = [ln for ln in _page("AGENTS.md").splitlines() if ln.startswith(("| `scored_changes`", "| `stale`"))]

    assert len(rows) == 4, "one pair in the brief table, one in the next-item table"
    assert all(rows[i].startswith("| `scored_changes`") for i in (0, 2))
    assert all("predates HEAD" not in row for row in rows)


# -- counts that name their files --------------------------------------------------


_VERDICT = re.compile(r"^ *verify (OK|FAILED) @ .*\((\d+) changed files\)")


def _verdicts(page: str) -> list[tuple[int, str, str]]:
    """(count, the verdict line's indent, the line under it) for each verify
    verdict a page prints, in a fenced or an indented block."""
    lines = _page(page).splitlines()
    return [(int(m.group(2)), line[:len(line) - len(line.lstrip())], lines[i + 1])
            for i, line in enumerate(lines) if (m := _VERDICT.match(line))]


def _names_shown(count: int) -> re.Pattern:
    """The `changed files:` line verify prints under a verdict of COUNT files."""
    rest = f" and {count - 3} more" if count > 3 else ""
    return re.compile(rf"  changed files: [^,\s]+(, [^,\s]+){{{min(count, 3) - 1}}}{rest}")


@pytest.mark.parametrize("page", ["README.md", "AGENTS.md"])
def test_every_verdict_with_a_diff_names_its_files_on_the_next_line(page, capsys):
    verifying._print_changed_paths(["a.py", "b.py", "c.py", "d.py"])
    printed = capsys.readouterr().out.rstrip("\n")
    verdicts = [v for v in _verdicts(page) if v[0]]

    assert printed == "  changed files: a.py, b.py, c.py and 1 more"
    assert verdicts, f"{page} prints no verdict with a changed file"
    for count, indent, under in verdicts:
        assert _names_shown(count).fullmatch(under.removeprefix(indent)), (page, count, under)


def test_the_changelog_quotes_the_line_that_names_the_changed_files(capsys):
    verifying._print_changed_paths(["app/m.py", "app/n.py", "tests/test_m.py"])
    printed = capsys.readouterr().out.strip()

    assert f"`{printed}`" in _prose(_release())
    assert "`changed_paths` beside the `changed_files` count" in _prose(_release())
    assert ("A line under the verdict names the first three changed files, `--json` lists them "
            "all as `changed_paths`") in _prose(_readme_row("verify"))


def test_the_changelog_quotes_the_warning_for_untracked_source_verify_did_not_judge(capsys):
    verifying._warn_untracked_in_scope(["src/added.ts"])
    line = capsys.readouterr().err.strip()

    assert f"``{line}``" in _prose(_release())
    assert "`untracked_in_scope`" in _prose(_release())


def _unmarked(count: int) -> list:
    from typing import NamedTuple

    class Row(NamedTuple):
        path: str
        long_name: str

    return [Row("calc/a.py", f"f{n}( x )") for n in range(count)]


def test_the_readme_says_the_debt_warning_names_three_functions(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["crapkit"])
    verifying._warn_standing_debt(_unmarked(4))
    line = capsys.readouterr().err

    assert "(calc/a.py f0( x ), calc/a.py f1( x ), calc/a.py f2( x ) and 1 more)" in line
    assert "carry no ratchet mark` and names the first three" in _prose(_page("README.md"))
    assert "no ratchet mark names the first three" in _prose(_release())


def _untracked_repo(root: Path, names: list[str]) -> str:
    """What init says about a repo holding NAMES, none of them added."""
    _git(root, "init", "-q")
    for name in names:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("def g(x):\n    return x or 0\n", encoding="utf-8")
    return _admin()._no_scopes_reason(root)


@pytest.mark.parametrize("names, shown", [
    (["src/app.ts", "lib/util.py"], "lib/util.py, src/app.ts"),
    (["a/e.py", "a/b.py", "a/a.py", "a/d.py", "a/c.py"], "a/a.py, a/b.py, a/c.py and 2 more"),
], ids=["two", "five"])
def test_init_names_up_to_three_untracked_sources(tmp_path, names, shown):
    reason = _untracked_repo(tmp_path, names)

    assert reason.endswith(f"run `git add` first ({len(names)} untracked source file(s) found: {shown})")


def test_the_changelog_quotes_what_init_says_about_untracked_source(tmp_path):
    reason = _untracked_repo(tmp_path, ["src/app.ts", "lib/util.py"])

    assert f"``{reason[reason.index('run `git add` first'):]}``" in _prose(_release())


# -- the Action names a base diff git refused ---------------------------------------

def _comment_builder():
    """tools/action/comment.py, loaded by path the way the action runs it."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("crapkit_action_comment",
                                                  ROOT / "tools" / "action" / "comment.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ACTION_NAMES_ITS_DIFF = hasattr(_comment_builder(), "_scope_lines")


def test_the_readme_says_what_the_comment_prints_when_the_base_diff_fails():
    builder = _comment_builder()
    worklist = {"active": [], "dormant_top": []}
    text = builder.body(None, None, 0, worklist, [], 5,
                        changed_error="fatal: Invalid symmetric difference expression\n")
    readme = _prose(_page("README.md"))

    assert "`fatal: Invalid symmetric difference expression`" in text
    assert "`fetch-depth: 0`" in text
    assert "quoting git's first line and naming `fetch-depth: 0`" in readme


def test_the_changelog_quotes_the_action_log_line_for_a_push():
    step = _page("action.yml")
    said = "no base commit on this event: the comment ranks the whole repository"

    assert f'echo "{said}"' in step
    assert f"`{said}`" in _prose(_release())


def test_the_pages_quote_the_names_the_comment_gives_verify_s_count():
    verify = {"run_id": 3, "baseline_run": 1, "changed_files": 1,
              "changed_paths": ["app/calc.py"]}
    against = _comment_builder()._against(verify)
    named = against.split(", ", 1)[1]

    assert named == "1 changed file (`app/calc.py`)"
    assert f"``{named}``" in _prose(_release())
    assert f"``{named}``" in _prose(_page("README.md"))


# -- the stamp records git blob ids ------------------------------------------------

def _module(name: str):
    """crapkit.NAME."""
    import importlib

    return importlib.import_module(f"crapkit.{name}")


def _hash_object(root: Path, name: str) -> str:
    return subprocess.run(["git", "hash-object", "--path", name, name], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def test_the_record_the_pages_describe_is_the_id_git_add_would_store(tmp_path):
    _one_commit_repo(tmp_path)
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")

    recorded = _module("lane_sources").record(tmp_path, ["a.py", "b.py"])

    assert recorded == {name: _hash_object(tmp_path, name) for name in ("a.py", "b.py")}
    assert "(`blobs` in `.crapkit/artifacts.json`), the id `git add` would store" in _prose(_release())
    assert "`record` is the one rule" in _page("AGENTS.md")
    assert "stamp holds the git blob id of each file it measured" in _prose(_page("AGENTS.md"))


# -- a failed lane's leftover stays refused until new bytes replace it -------------

def test_a_leftover_stays_refused_through_a_touch_and_a_lost_stamp_file_until_new_bytes(tmp_path):
    import os

    from crapkit.config import Lane

    stamps = _module("lane_stamps")
    lane = Lane(name="py", command="true", artifact=".crapkit/cov/py.json",
                parser="coveragepy", scopes=("calc",))
    leftover = tmp_path / lane.artifact
    leftover.parent.mkdir(parents=True)
    leftover.write_text("{}", encoding="utf-8")
    stamps.write(tmp_path, stamps.refusal_entry(stamps.read(tmp_path), lane,
                                                stamps.file_sha256(leftover)))
    later = leftover.stat().st_mtime + 60
    os.utime(leftover, (later, later))
    after_touch = stamps.read(tmp_path).refusal(lane.artifact).kind
    (tmp_path / stamps.STAMPS_FILE).unlink()
    after_delete = stamps.read(tmp_path).refusal(lane.artifact).kind
    leftover.write_text('{"meta": 1}', encoding="utf-8")
    after_new_bytes = stamps.read(tmp_path).refusal(lane.artifact).kind

    assert (after_touch, after_delete, after_new_bytes) == ("leftover", "leftover", "")
    assert "a `touch` does not lift the refusal, new bytes do" in _prose(_readme_row("coverage"))
    assert ("a touch keeps the leftover refused, deleting `.crapkit/artifacts.json` does not "
            "lift it, and new bytes lift it") in _prose(_page("docs/upgrading.md"))
    assert "so deleting `.crapkit/artifacts.json` does not lift it. New bytes lift it" in _prose(_release())


def test_a_declared_output_is_gone_while_the_attempt_runs_and_a_leftover_comes_back(tmp_path):
    report = tmp_path / ".crapkit" / "cov" / "py.json"
    report.parent.mkdir(parents=True)
    report.write_text("{}", encoding="utf-8")

    with _module("lane_outputs").owned(tmp_path, "py", (".crapkit/cov/py.json",)):
        during = report.exists()

    upgrading = _prose(_page("docs/upgrading.md"))
    assert during is False and report.read_text(encoding="utf-8") == "{}"
    assert "sit under `.crapkit/aside/` while it runs" in upgrading
    assert "finds nothing at that path" in upgrading
    assert "move under `.crapkit/aside/` before its attempts start" in _prose(_release())


# -- docs/upgrading.md: what 0.8.1 changes about freshness ----------------------------

def _upgrading_section(text: str, heading: str) -> str:
    return text.split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]


def _upgrading_freshness() -> str:
    """What docs/upgrading.md tells a 0.8.0 user about freshness: its own
    section, and the library callers section that holds the shims."""
    text = _page("docs/upgrading.md")
    return _prose(_upgrading_section(text, "Freshness in 0.8.1") + "\n"
                  + _upgrading_section(text, "Library callers"))


def test_the_upgrade_notes_name_the_stop_rule_the_shim_and_prune_s_exit():
    notes = _upgrading_freshness()

    assert "The stop rule gains a fourth clause, `scored_changes == 0`" in notes
    assert "`null` included, means run `commands.refresh`" in notes
    assert "`lanes.lane_sources_unchanged` keeps its 0.8.0 arguments" in notes
    assert "`DeprecationWarning`" in notes and "0.9.0 removes it" in notes
    assert "it exits 4 before writing anything" in notes


def test_the_upgrade_notes_quote_the_not_in_this_clone_refusal(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["crapkit"])
    missing = _not_behind(shallow=False, held=False)

    assert re.match(r"baseline commit \w+ is not in this clone", missing)
    assert "`baseline commit ... is not in this clone`" in _upgrading_freshness()


def test_a_touch_after_restoring_an_old_mtime_lets_git_read_the_content(tmp_path):
    """The upgrade notes' way out of the same-size limit. git compares its
    index's stat data first; core.trustctime=false makes Linux and macOS answer
    as Windows does, where the change time is the creation time."""
    import os
    import time

    from crapkit.gitio import status_names

    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "core.trustctime", "false")
    source = tmp_path / "a.py"
    source.write_text("x = 1\n", encoding="utf-8")
    past = time.time_ns() - 100 * 10**9
    os.utime(source, ns=(past, past))
    _git(tmp_path, "add", "a.py")
    _git(tmp_path, "commit", "-q", "-m", "one")
    source.write_text("x = 2\n", encoding="utf-8")
    os.utime(source, ns=(past, past))
    restored = status_names(tmp_path)
    os.utime(source)
    touched = status_names(tmp_path)

    assert (restored, touched) == ([], ["a.py"])
    assert ("`touch` the files after restoring them that way, and every reader compares their "
            "content") in _upgrading_freshness()



_WHERE_THE_LIMIT_HOLDS = (
    "holds on Windows, where the change time is the creation time, and under "
    "`core.trustctime=false`",
    "git sees the edit once the change time moves a second past the one it recorded",
)


@pytest.mark.parametrize("page", ["CHANGELOG.md", "docs/upgrading.md", "docs/lanes.md",
                                  "docs/ratchet.md", "src/crapkit/lane_sources.py",
                                  "AGENTS.md", "CONTEXT.md"])
def test_each_page_that_names_the_same_size_limit_says_where_git_holds_it(page):
    """On Linux and macOS git's stat check also compares the change time, which
    no copy puts back, to the second; the same-size-new-ctime row in
    tests/unit/stale_tree.py measures every reader naming the edit there, and
    the same-size-one-tick row measures the limit under core.trustctime=false.
    A page that states the limit with no OS reads as true where git sees it."""
    text = _prose(_release() if page == "CHANGELOG.md" else _page(page))

    for phrase in _WHERE_THE_LIMIT_HOLDS:
        assert phrase in text, (page, phrase)

# -- explain reads the run's lines, not HEAD's ----------------------------------------

def _reports():
    from crapkit.cli import reports

    return reports


def _tests_withheld() -> bool:
    import inspect

    return "withheld" in inspect.signature(_reports()._tests_fields).parameters


def test_the_pages_say_explain_tests_withholds_its_ids_with_the_dark_line_note():
    fields = _reports()._tests_fields({3: {"tests/test_m.py::test_a"}}, (1, 5), "the note")

    assert fields == {"tests": None, "tests_note": "the note"}
    assert "`tests_note` repeats `uncovered_lines_note`" in _prose(_release())
    assert "withholds them with the same note whenever the file's dark lines are withheld" in \
        _prose(_page("README.md"))


def test_the_changelog_quotes_the_note_for_a_span_no_commit_holds(tmp_path):
    _git(tmp_path, "init", "-q")

    fields = _reports()._span_commits(tmp_path, "pkg/m.py", (9, 10))

    assert fields["commits"] is None
    assert f"`{fields['commits_note']}`" in _prose(_release())
    assert "carried through your uncommitted edits onto HEAD's lines" in _prose(_page("README.md"))


def _freshness_carries_git_errors() -> bool:
    return "unread" in getattr(getattr(_queue(), "RunFreshness", None), "_fields", ())


def test_the_changelog_quotes_the_worklist_warning_when_git_cannot_read_the_tree(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["crapkit"])
    queue = _queue()
    fresh = queue.RunFreshness(False, None, "fatal: index file corrupt")
    (line,) = queue._freshness_warnings(fresh, {"id": 4, "commit": "f" * 40})
    head = line.removeprefix("warning: ").split("git failed:")[0] + "git failed:"

    assert fresh.envelope()["scored_changes"] is None
    assert f"`{head}`" in _prose(_release())


def test_the_changelog_limit_names_every_reader_s_blind_spot_and_the_touch_that_clears_it():
    section = _prose(_release())

    assert "or a second same-size write inside one clock tick" in section
    assert "a symlink re-pointed to a same-size target with the same time" in section
    assert "`touch` the files after restoring them, and every reader compares their content" in section


# -- one verdict per lane, and a reuse line that names its limits

_FRESHNESS = _module("lane_freshness")
_ONE_VERDICT = _FRESHNESS is not None


def test_the_changelog_quotes_the_reuse_line_and_what_each_lane_kind_leaves_out(capsys):
    from crapkit.cli import scoring

    verdict = _FRESHNESS.ReuseVerdict("8c14f3daa8e" + "0" * 29, "")
    scoring._report_reuse("py", verdict, _FRESHNESS._UNPROVED[False])
    line = capsys.readouterr().err.strip()
    section = _prose(_release())

    assert f"`{line}`" in section
    assert f"For a lane with `inputs` it names {_FRESHNESS._UNPROVED[True]}" in section
    assert "says what that proof leaves out" in _prose(_readme_row("coverage"))
    assert "each reuse names what its proof leaves out" in _prose(_page("docs/handbook.html"))
    assert "the line that reuses a lane names what its proof leaves out" in _prose(_page("docs/upgrading.md"))


def test_the_changelog_quotes_why_a_stamp_holds_no_proof():
    proof = _FRESHNESS.Proof("", {}, "x", ("calc/grade.py", "calc/report.py"))
    why = _FRESHNESS.unproved(proof, proof)

    assert f"`its stamp holds no proof: {why}`" in _prose(_release())


def test_the_session_variables_the_changelog_names_are_the_ones_the_proof_leaves_out():
    session = _FRESHNESS._SESSION_VARIABLES
    section = _prose(_release())

    for name in ("SSH_AUTH_SOCK", "SSH_AGENT_PID", "TMUX", "VSCODE_GIT_IPC_HANDLE", "PSModulePath"):
        assert name.upper() in session and f"`{name}`" in section
    assert "PATHEXT" not in session and "names `PATHEXT` alone" in section


@pytest.mark.parametrize("inputs", [(), ("calc",)], ids=["tree", "inputs"])
def test_both_lane_kinds_prove_the_crapkit_version(tmp_path, inputs):
    from crapkit import __version__
    from crapkit.config import Lane

    _one_commit_repo(tmp_path)
    (tmp_path / "crapkit.toml").write_text("[crapkit]\n", encoding="utf-8")
    lane = Lane(name="py", command="true", artifact=".crapkit/cov/py.json",
                parser="coveragepy", scopes=("calc",), inputs=inputs)

    parts = _FRESHNESS.proof_parts(tmp_path, lane, "f" * 40)

    assert parts["crapkit"] == __version__
    assert "A lane's proof holds the crapkit version, with `inputs` or without" in _prose(_release())


@pytest.mark.parametrize("page", ["CHANGELOG.md", "docs/upgrading.md"])
def test_the_pages_keep_the_0_8_0_staleness_reads_as_a_shim_and_name_its_replacement(page):
    """A 0.8.0 library name stays through 0.8.x as a warning shim. A page
    saying it is gone sends a caller to rewrite code that still runs."""
    text = _prose(_release() if page == "CHANGELOG.md" else _upgrading_freshness())

    assert callable(lanes.staleness_reads) and hasattr(_FRESHNESS.Freshness, "__enter__")
    assert "`lanes.staleness_reads` keeps" in text or "`lanes.staleness_reads` stays" in text
    assert "lane_freshness.Freshness(root, lanes, scope_paths)" in text
    assert "staleness_reads` is gone" not in text and "exported, is gone" not in text


@pytest.mark.parametrize("page", ["CHANGELOG.md", "docs/upgrading.md"])
def test_the_limit_names_each_reader_that_trusts_git_s_stat_cache(page):
    text = _prose(_release() if page == "CHANGELOG.md" else _upgrading_freshness())

    for reader in ("lane reuse", "verify's changed files and its split of committed and dirty findings",
                   "`rescore --gate`", "the commit hook's note that a staged file differs from the working tree",
                   "the files `mutate` copies into its workers"):
        assert reader in text, (page, reader)


def _fold_keys_on_content() -> bool:
    import inspect

    return "digest" in inspect.signature(_module("uncovered")._artifact_key).parameters


def test_the_changelog_says_the_dead_line_fold_keys_on_the_artifact_s_sha256(tmp_path):
    import hashlib

    artifact = tmp_path / "py.json"
    artifact.write_text("{}", encoding="utf-8")

    key = _module("uncovered")._artifact_key(artifact)

    assert hashlib.sha256(b"{}").hexdigest() in key
    assert "cached by the artifact's sha256" in _prose(_release())


def test_the_onboard_skill_quotes_what_init_says_when_no_source_is_tracked(tmp_path):
    reason = _untracked_repo(tmp_path, ["src/app.ts", "lib/util.py"])
    skill = _prose(_page("plugin/skills/crapkit-onboard/SKILL.md"))

    assert f"``{reason[reason.index('run `git add` first'):]}``" in skill
    assert "`init` exits 3 and names up to three of the files it found" in skill


# -- the handbook, in the words the behaviour has now ---------------------------------

def _handbook() -> str:
    return _prose(_page("docs/handbook.html"))


def test_the_handbook_says_the_stamp_holds_blob_ids_and_staleness_is_per_file(tmp_path):
    _one_commit_repo(tmp_path)

    recorded = _module("lane_sources").record(tmp_path, ["a.py"])

    assert recorded == {"a.py": _hash_object(tmp_path, "a.py")}
    assert ("the git blob id of each file under the lane's scopes, so its line numbers go stale "
            "for the files whose bytes moved and for no others") in _handbook()


def test_the_handbook_says_a_touch_does_not_lift_a_leftover_s_refusal(tmp_path):
    report = tmp_path / "py.json"
    report.write_text("{}", encoding="utf-8")

    with _module("lane_outputs").owned(tmp_path, "py", ("py.json",)):
        during = report.exists()

    assert during is False
    assert "Its declared files sit under <code>.crapkit/aside/</code> while it runs" in _handbook()
    assert "a <code>touch</code> does not lift the refusal, new bytes do" in _handbook()


def test_the_handbook_lookup_row_says_watch_rescores_on_new_bytes(tmp_path):
    import os

    from crapkit import watch

    _one_commit_repo(tmp_path)
    first = watch.snapshot(tmp_path, ["a.py"])
    later = (tmp_path / "a.py").stat().st_mtime + 5
    os.utime(tmp_path / "a.py", (later, later))
    _, moved = watch.poll(tmp_path, ["a.py"], first)
    row = next(r for r in _handbook().split("<tr>") if '<td class="mono">watch</td>' in r)

    assert moved == []
    assert "when its bytes change, not when its mtime moves" in row
    assert "Rescores tracked files as they change" not in row


def test_an_unreadable_stamp_file_refuses_the_artifact_and_says_why(tmp_path):
    stamps = _module("lane_stamps")
    artifact = tmp_path / ".crapkit" / "cov" / "py.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("{}", encoding="utf-8")
    (tmp_path / stamps.STAMPS_FILE).write_text("{not json", encoding="utf-8")

    refusal = stamps.read(tmp_path).refusal(".crapkit/cov/py.json")

    assert (refusal.kind, refusal.why) == ("unknown", "it does not parse as JSON")
    assert ("`--reuse-artifacts` refuses a lane while `.crapkit/artifacts.json` cannot be read"
            in _prose(_release()))


# -- what the lane-freshness change hands library callers and git --------------------

def test_the_replacement_the_pages_name_for_the_shim_gives_the_shim_s_answer(tmp_path):
    from crapkit.config import Lane

    lane = Lane(name="py", command="true", artifact=".crapkit/cov/py.json",
                parser="coveragepy", scopes=("calc",))
    _one_commit_repo(tmp_path)

    with _FRESHNESS.Freshness(tmp_path, [lane], {"calc": ("calc",)}) as fresh:
        reason = fresh.lines(lane)
    with pytest.warns(DeprecationWarning):
        shim = lanes.lane_sources_unchanged(tmp_path, lane, {"calc": ("calc",)})

    call = "`lane_freshness.Freshness(root, lanes, scope_paths).lines(lane)`"
    assert isinstance(reason, str) and shim is (reason == "")
    assert call in _prose(_release()) and call in _upgrading_freshness()


def _git_environment() -> dict:
    return getattr(_module("gitio"), "_environment", dict)()


def test_the_changelog_says_every_git_process_takes_no_optional_lock():
    assert _git_environment()["GIT_OPTIONAL_LOCKS"] == "0"
    assert "Every git process crapkit starts sets `GIT_OPTIONAL_LOCKS=0`" in _prose(_release())


def test_the_upgrade_notes_say_a_0_8_0_stamp_is_judged_by_its_commit_until_the_lane_runs():
    notes = _upgrading_freshness()

    assert "A stamp 0.8.0 wrote records only its commit" in notes
    assert "`lanes.staleness_reads` stays through 0.8.x on the same terms" in notes


# -- scored_changes is null when crapkit cannot compare, in every payload ---------

def test_every_agent_json_table_types_scored_changes_int_or_null():
    """next-item, brief and worklist each carry `null` for a run 0.8.0 wrote or
    a git failure. The brief table said `int`, so an agent that read that table
    alone had no branch for the null the packet carries."""
    rows = [line for line in _page("docs/agent-json.md").splitlines()
            if line.startswith("| `scored_changes` |")]

    types = [line.split("|")[2].replace("*", "").strip() for line in rows]
    assert len(types) == 3 and set(types) == {"int or null"}, rows


# -- the git command the content record runs is the one the pages name ------------

_HASH_OBJECT = re.compile(r"`git hash-object([^`]*)`")
_CONTENT_PAGES = ("CHANGELOG.md", "AGENTS.md", "README.md", "CONTEXT.md", "docs/lanes.md",
                  "docs/upgrading.md", "docs/agent-json.md", "docs/configuration.md")


def _hash_object_options(root: Path, monkeypatch) -> set[str]:
    """The options crapkit passes `git hash-object` while it records an edited
    file's blob id, read off the processes it starts."""
    from crapkit import gitio

    started = []
    real = subprocess.Popen

    def spy(argv, *args, **kwargs):
        started.append(list(argv))
        return real(argv, *args, **kwargs)

    _one_commit_repo(root)
    (root / "a.py").write_text("x = 2\n", encoding="utf-8")
    monkeypatch.setattr(subprocess, "Popen", spy)
    gitio.worktree_blobs(root, ["a.py"])
    return set().union(*(_options(argv[argv.index("hash-object") + 1:])
                         for argv in started if "hash-object" in argv))


def _options(args: list[str]) -> set[str]:
    """The `--name` options, the `--` that ends them left out."""
    return {arg for arg in args if arg.startswith("--") and arg != "--"}


def test_each_hash_object_command_a_page_names_is_the_one_crapkit_runs(tmp_path, monkeypatch):
    """CHANGELOG and AGENTS.md said `git hash-object --path` hashes a file the
    index cannot vouch for; crapkit runs `git hash-object --stdin-paths`."""
    runs = _hash_object_options(tmp_path, monkeypatch)
    named = {(page, options.strip()) for page in _CONTENT_PAGES
             for options in _HASH_OBJECT.findall(_page(page))}

    assert runs == {"--stdin-paths"}, runs
    assert named, "no page names the command that hashes an edited file"
    assert [(page, options) for page, options in sorted(named)
            if not set(re.findall(r"--[\w-]+", options)) <= runs] == []
