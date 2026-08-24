"""The discovery helpers against real git, in a real repo.

Every unit test of `callers` stubs the grep, so the argv itself is unproven
there: whether -F plus one -e per name really is one command, whether
--full-name really yields repo-relative paths, and whether git's exit 1 for "no
match" really reads as an empty answer rather than a failure.
"""
import subprocess
from pathlib import Path

import pytest

from crapkit.discover import callers, nearest_guides, related_tests

SEND_TS = """export function send(x) {
  return wrap(x) + 1;
}

function wrap(x) {
  return x;
}
"""

RELAY_TS = """import { send } from "./send";

export function relay(x) {
  return send(x + 1);
}
"""

SCOPES = ["src", "lib"]


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    write(root / "src" / "send.ts", SEND_TS)
    write(root / "src" / "relay.ts", RELAY_TS)
    write(root / "src" / "send.test.ts", "send(3);\n")
    write(root / "src" / "__tests__" / "send.int.ts", "send(4);\n")
    write(root / "lib" / "other.ts", "send(5);\n")
    write(root / "docs" / "notes.md", "send(6) is the entry point\n")
    write(root / "AGENTS.md", "root rules\n")
    write(root / "src" / "CLAUDE.md", "src rules\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True,
                   capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "seed"], cwd=root, check=True,
                   capture_output=True)
    return root


def test_a_real_grep_finds_the_callers_and_drops_the_definition(repo: Path):
    found = callers(repo, SCOPES, "src/send.ts", "send")

    assert [c["path"] for c in found["callers"]] == [
        "lib/other.ts", "src/relay.ts", "src/relay.ts"]
    assert found["count"] == 3
    assert found["exported"] is True


def test_the_pathspec_keeps_the_grep_inside_the_declared_scopes(repo: Path):
    """docs/notes.md names send and is not under src or lib, so it is not an
    answer to who calls it."""
    found = callers(repo, SCOPES, "src/send.ts", "send")

    assert "docs/notes.md" not in [c["path"] for c in found["callers"]]


def test_a_real_grep_drops_the_test_files_it_matched(repo: Path):
    found = callers(repo, SCOPES, "src/send.ts", "send")

    assert "src/send.test.ts" not in [c["path"] for c in found["callers"]]
    assert "src/__tests__/send.int.ts" not in [c["path"] for c in found["callers"]]


def test_a_name_nothing_mentions_is_an_empty_answer_not_a_git_failure(repo: Path):
    """`git grep` exits 1 when it matched nothing. Treating that as a failure
    would turn every unused helper into a crash."""
    found = callers(repo, SCOPES, "src/send.ts", "nowhere")

    assert found == {"count": 0, "callers": [], "exported": False}


def test_two_names_of_one_file_come_back_from_one_real_grep(repo: Path):
    """Each name is excluded from its OWN span, not from the file: send calls
    wrap on line 2, which is inside send's span and outside wrap's, so it is a
    caller of wrap and not a caller of send."""
    found = callers(repo, SCOPES, "src/send.ts", ["send", "wrap"])

    assert set(found) == {"send", "wrap"}
    assert found["send"]["count"] == 3
    assert found["wrap"]["callers"] == [{"path": "src/send.ts", "line": 2}]
    assert found["send"]["exported"] is True and found["wrap"]["exported"] is False


def test_the_tests_beside_a_source_are_found_in_a_real_tree(repo: Path):
    assert related_tests(repo, "src/send.ts") == {
        "tests": ["src/__tests__/send.int.ts", "src/send.test.ts"], "support": []}


def test_the_guides_over_a_source_are_found_in_a_real_tree(repo: Path):
    assert nearest_guides(repo, "src/send.ts") == ["src/CLAUDE.md", "AGENTS.md"]
