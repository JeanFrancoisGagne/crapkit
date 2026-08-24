"""Discovery helpers the start-editing packet consumes.

Three questions about one file: who calls its functions, which tests sit beside
it, and which guide files govern it. Every answer is sorted, so two runs of a
packet agree byte for byte.
"""
from pathlib import Path

import pytest

from crapkit import discover
from crapkit.discover import callers, nearest_guides, related_tests


def write(path: Path, text: str = "x\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


# --- nearest_guides -----------------------------------------------------------

def test_guides_are_collected_up_the_tree_nearest_first(tmp_path: Path):
    write(tmp_path / "AGENTS.md")
    write(tmp_path / "src" / "AGENTS.md")
    write(tmp_path / "src" / "api" / "send.ts")

    assert nearest_guides(tmp_path, "src/api/send.ts") == ["src/AGENTS.md", "AGENTS.md"]


def test_all_three_guide_names_count_and_tie_break_by_name(tmp_path: Path):
    for name in ("CONTRIBUTING.md", "CLAUDE.md", "AGENTS.md"):
        write(tmp_path / "src" / name)
    write(tmp_path / "src" / "send.ts")

    assert nearest_guides(tmp_path, "src/send.ts") == [
        "src/AGENTS.md", "src/CLAUDE.md", "src/CONTRIBUTING.md"]


def test_a_guide_beside_a_root_file_is_named_without_a_directory(tmp_path: Path):
    write(tmp_path / "CLAUDE.md")
    write(tmp_path / "send.ts")

    assert nearest_guides(tmp_path, "send.ts") == ["CLAUDE.md"]


def test_a_directory_named_like_a_guide_is_not_a_guide(tmp_path: Path):
    (tmp_path / "src" / "AGENTS.md").mkdir(parents=True)
    write(tmp_path / "src" / "send.ts")

    assert nearest_guides(tmp_path, "src/send.ts") == []


def test_a_backslash_path_reads_the_same_as_a_posix_one(tmp_path: Path):
    write(tmp_path / "src" / "api" / "AGENTS.md")
    write(tmp_path / "src" / "api" / "send.ts")

    assert nearest_guides(tmp_path, "src\\api\\send.ts") == ["src/api/AGENTS.md"]


# --- related_tests ------------------------------------------------------------

def test_the_sibling_test_files_of_a_ts_source_are_found(tmp_path: Path):
    for name in ("send.ts", "send.test.ts", "send.spec.ts", "sendMany.test.ts",
                 "receive.test.ts", "send.md"):
        write(tmp_path / "src" / name)
    write(tmp_path / "src" / "__tests__" / "send.integration.ts")

    found = related_tests(tmp_path, "src/send.ts")

    assert found["tests"] == ["src/__tests__/send.integration.ts", "src/send.spec.ts",
                              "src/send.test.ts", "src/sendMany.test.ts"]


def test_both_python_test_namings_of_a_py_source_are_found(tmp_path: Path):
    for name in ("parse.py", "test_parse.py", "test_parse_edges.py", "parse_test.py",
                 "test_other.py"):
        write(tmp_path / "app" / name)

    assert related_tests(tmp_path, "app/parse.py")["tests"] == [
        "app/parse_test.py", "app/test_parse.py", "app/test_parse_edges.py"]


def test_support_is_the_conftest_the_support_files_and_the_two_data_dirs(tmp_path: Path):
    for name in ("parse.py", "conftest.py", "render.test-support.ts", "notes.md"):
        write(tmp_path / "app" / name)
    write(tmp_path / "app" / "fixtures" / "rows.json")
    write(tmp_path / "app" / "factories" / "user.py")

    assert related_tests(tmp_path, "app/parse.py")["support"] == [
        "app/conftest.py", "app/factories/user.py", "app/fixtures/rows.json",
        "app/render.test-support.ts"]


def test_a_source_with_nothing_beside_it_gets_two_empty_lists(tmp_path: Path):
    write(tmp_path / "app" / "parse.py")

    assert related_tests(tmp_path, "app/parse.py") == {"tests": [], "support": []}


def test_the_source_itself_is_never_one_of_its_own_tests(tmp_path: Path):
    """`send.test.ts` asked about would otherwise glob itself: its stem is
    `send.test`, and `send.test*.test.ts` is not a match, but `test_x.py` asked
    about is — `test_x*.py` matches `test_x.py`."""
    write(tmp_path / "app" / "test_x.py")

    assert related_tests(tmp_path, "app/test_x.py")["tests"] == []


def test_one_directory_listing_serves_every_pattern(tmp_path: Path, monkeypatch):
    """Five glob patterns used to mean five walks of one directory. The listing
    is the batch: each directory is read once and every pattern is matched
    against that one list."""
    for name in ("send.ts", "send.test.ts", "send.spec.ts", "conftest.py"):
        write(tmp_path / "src" / name)
    write(tmp_path / "src" / "__tests__" / "send.e2e.ts")
    write(tmp_path / "src" / "fixtures" / "rows.json")
    write(tmp_path / "src" / "factories" / "user.py")

    listed: list[str] = []
    real = discover._entries
    monkeypatch.setattr(discover, "_entries",
                        lambda d: (listed.append(str(d)), real(d))[1])

    found = related_tests(tmp_path, "src/send.ts")

    assert len(found["tests"]) == 3 and len(found["support"]) == 3
    assert len(listed) == len(set(listed)) == 4, \
        "own dir, __tests__, fixtures, factories — each read exactly once"


# --- callers ------------------------------------------------------------------

SEND_PY = """def send(x):
    return send(x - 1) if x else 0


def relay(x):
    return send(x)
"""

HITS = "\n".join([
    "src/a.py:1:def send(x):",
    "src/a.py:2:    return send(x - 1) if x else 0",
    "src/a.py:6:    return send(x)",
    "src/b.py:3:    send(1)",
    "src/c.py:9:    sendMail(1)",
    "tests/test_a.py:4:    send(2)",
    "src/__tests__/a.ts:2:  send();",
    "web/send.test.ts:7:  send();",
])


@pytest.fixture()
def grep(monkeypatch):
    """`git grep`, recorded instead of spawned. The stub answers every call with
    one canned block, so a test can count spawns and read the argv."""
    def stub(root, args):
        stub.calls.append(args)
        return stub.output

    stub.calls, stub.output = [], ""
    monkeypatch.setattr(discover, "_grep_output", stub)
    return stub


def test_the_definition_and_its_own_body_are_not_callers(tmp_path: Path, grep):
    """Line 2 is send calling itself from inside its own span. Line 6 is relay,
    in the same file, and that one is a real caller — the span is excluded, not
    the file."""
    write(tmp_path / "src" / "a.py", SEND_PY)
    grep.output = HITS

    found = callers(tmp_path, ["src"], "src/a.py", "send")

    assert found["callers"] == [{"path": "src/a.py", "line": 6},
                                {"path": "src/b.py", "line": 3}]
    assert found["count"] == 2


def test_a_longer_name_that_merely_starts_with_the_identifier_is_not_a_caller(
        tmp_path: Path, grep):
    write(tmp_path / "src" / "a.py", SEND_PY)
    grep.output = HITS

    found = callers(tmp_path, ["src"], "src/a.py", "send")

    assert "src/c.py" not in [c["path"] for c in found["callers"]], "sendMail is not send"


def test_test_files_never_count_as_callers(tmp_path: Path, grep):
    """Three test namings appear in the canned block: a test_ prefix, a
    __tests__ segment and a .test. infix. None of them is a caller a change has
    to answer to."""
    write(tmp_path / "src" / "a.py", SEND_PY)
    grep.output = HITS

    found = callers(tmp_path, ["src"], "src/a.py", "send")

    assert [c["path"] for c in found["callers"]] == ["src/a.py", "src/b.py"]


def test_the_local_test_predicate_agrees_with_the_cli_one(tmp_path: Path):
    """discover imports nothing from the CLI, so the rule is written twice. The
    two have to route every path the same way or a packet and a verdict
    disagree about what a test is."""
    from crapkit.cli import _is_test_path

    paths = ["src/send.ts", "src/send.test.ts", "src/send.spec.ts", "src/__tests__/a.ts",
             "tests/unit/test_a.py", "test/a.py", "app/test_a.py", "app/TESTS/a.py",
             "src/latest/a.py", "src/contest.py", "app/a_test.py"]

    assert [discover._is_test_path(p) for p in paths] == [_is_test_path(p) for p in paths]


def test_the_caller_list_stops_at_twenty_and_the_count_does_not(tmp_path: Path, grep):
    write(tmp_path / "src" / "a.py", SEND_PY)
    grep.output = "\n".join(f"src/z.py:{n}:    send({n})" for n in range(1, 26))

    found = callers(tmp_path, ["src"], "src/a.py", "send")

    assert found["count"] == 25
    assert len(found["callers"]) == 20
    assert found["callers"][-1] == {"path": "src/z.py", "line": 20}


def test_a_name_in_dunder_all_reads_as_exported(tmp_path: Path, grep):
    write(tmp_path / "src" / "a.py", '__all__ = [\n    "send",\n]\n\n' + SEND_PY)

    assert callers(tmp_path, ["src"], "src/a.py", "send")["exported"] is True


def test_a_name_on_an_export_statement_reads_as_exported(tmp_path: Path, grep):
    write(tmp_path / "src" / "a.ts", "export function send(x) {\n  return x;\n}\n")

    assert callers(tmp_path, ["src"], "src/a.ts", "send")["exported"] is True


def test_a_name_the_file_never_exports_reads_as_private(tmp_path: Path, grep):
    write(tmp_path / "src" / "a.py", SEND_PY)

    assert callers(tmp_path, ["src"], "src/a.py", "send")["exported"] is False


def test_a_file_that_is_gone_still_answers_with_every_hit(tmp_path: Path, grep):
    """No file means no defining span to exclude and nothing exported. The
    callers git found are still callers."""
    grep.output = HITS

    found = callers(tmp_path, ["src"], "src/gone.py", "send")

    assert found["count"] == 4 and found["exported"] is False


def test_scope_paths_become_the_greps_pathspec(tmp_path: Path, grep):
    write(tmp_path / "src" / "a.py", SEND_PY)

    callers(tmp_path, ["src", "lib"], "src/a.py", "send")

    (args,) = grep.calls
    assert args[args.index("--") + 1:] == ["lib", "src"]


def test_a_config_scope_map_is_the_same_pathspec_as_a_flat_list(tmp_path: Path, grep):
    """crapkit's own config holds scope paths as {scope: (path, ...)}. A caller
    holding that should not have to flatten it first."""
    write(tmp_path / "src" / "a.py", SEND_PY)

    callers(tmp_path, {"app": ("src",), "shared": ("lib",)}, "src/a.py", "send")

    (args,) = grep.calls
    assert args[args.index("--") + 1:] == ["lib", "src"]


def test_no_scope_leaves_the_grep_unbounded_instead_of_ending_in_a_bare_dashdash(
        tmp_path: Path, grep):
    write(tmp_path / "src" / "a.py", SEND_PY)

    callers(tmp_path, [], "src/a.py", "send")

    (args,) = grep.calls
    assert "--" not in args


THREE = "def alpha():\n    pass\n\n\ndef beta():\n    pass\n\n\ndef gamma():\n    pass\n"
THREE_HITS = "\n".join(["src/b.py:2:    alpha()", "src/b.py:3:    beta()",
                        "src/b.py:4:    beta()", "src/c.py:1:    gamma()"])


def test_a_list_of_identifiers_costs_exactly_one_git_grep(tmp_path: Path, grep):
    write(tmp_path / "src" / "a.py", THREE)
    grep.output = THREE_HITS

    found = callers(tmp_path, ["src"], "src/a.py", ["alpha", "beta", "gamma"])

    (args,) = grep.calls
    assert args.count("-e") == 3, "one fixed-string pattern per name, one command"
    assert set(found) == {"alpha", "beta", "gamma"}
    assert found["beta"]["count"] == 2


def test_the_list_form_answers_what_the_single_calls_answer_for_a_third_of_the_spawns(
        tmp_path: Path, grep):
    """The list IS the cache: every name reads the same grep output and the same
    one read of the defining file."""
    write(tmp_path / "src" / "a.py", THREE)
    grep.output = THREE_HITS
    names = ["alpha", "beta", "gamma"]

    one_by_one = {n: callers(tmp_path, ["src"], "src/a.py", n) for n in names}
    assert len(grep.calls) == 3
    grep.calls.clear()

    batched = callers(tmp_path, ["src"], "src/a.py", names)

    assert len(grep.calls) == 1
    assert batched == one_by_one


def test_a_repeated_name_is_asked_about_once(tmp_path: Path, grep):
    write(tmp_path / "src" / "a.py", THREE)
    grep.output = THREE_HITS

    found = callers(tmp_path, ["src"], "src/a.py", ["beta", "alpha", "beta"])

    (args,) = grep.calls
    assert args.count("-e") == 2
    assert list(found) == ["beta", "alpha"], "asked-about order, duplicates dropped"


def test_a_body_under_an_export_line_is_not_part_of_the_export_surface(tmp_path: Path,
                                                                       grep):
    """`export function send(x) {` opens a BODY, not a list. Continuing the
    export surface through that brace made every local name the function
    touches read as exported."""
    write(tmp_path / "src" / "a.ts",
          "export function send(x) {\n  return wrap(x);\n}\n\nfunction wrap(x) {\n"
          "  return x;\n}\n")

    found = callers(tmp_path, ["src"], "src/a.ts", ["send", "wrap"])

    assert found["send"]["exported"] is True
    assert found["wrap"]["exported"] is False


def test_a_braced_export_list_spanning_lines_is_part_of_the_export_surface(
        tmp_path: Path, grep):
    """`export {` DOES open a list, and the names in it are two lines down."""
    write(tmp_path / "src" / "a.ts",
          "function send(x) {\n  return x;\n}\n\nexport {\n  send,\n} from \"./a\";\n")

    assert callers(tmp_path, ["src"], "src/a.ts", "send")["exported"] is True


def test_an_empty_identifier_list_is_an_empty_answer_and_no_grep_at_all(tmp_path: Path,
                                                                        grep):
    """`git grep` with no -e is a usage error, so a file with nothing to ask
    about must not reach the command."""
    write(tmp_path / "src" / "a.py", SEND_PY)

    assert callers(tmp_path, ["src"], "src/a.py", []) == {}
    assert grep.calls == []


def test_a_blank_name_never_reaches_the_grep(tmp_path: Path, grep):
    """`git grep -e ""` matches every line in the repo, which would come back as
    thousands of callers rather than as an error."""
    write(tmp_path / "src" / "a.py", SEND_PY)

    assert callers(tmp_path, ["src"], "src/a.py", "") == {
        "count": 0, "callers": [], "exported": False}
    assert grep.calls == []
