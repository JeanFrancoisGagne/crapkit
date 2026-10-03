"""crapkit reads the same history whatever the user's log settings say.

`git log` honors three settings that change what it prints, and crapkit's
readers took that output as the history:

- log.showSignature=true prints each signed commit's verification ahead of its
  record. `explain --history` named a commit 'Good "git" signature for ...' plus
  its name, churn counted the line as a changed path, and the ratchet file's
  patches carried it.
- log.follow=true follows a lone path across a rename, and git's --follow does
  not hold with --reverse: the ratchet file's history lost commits.
- log.showRoot=false prints no diff for the root commit, so the files it added
  got no churn from it and the marks it added never entered the ratchet report.

Each reading is checked against the same reading with the setting unset, and
against the history the test wrote.
"""
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from crapkit import churn_cache
from crapkit.cli.reports import _function_commits
from crapkit.marks_history import marks_history
from crapkit.ratchet_report import mark_events, report_from_events

DAY = 86400
BASE = int(time.time()) - 10 * DAY  # inside any churn window of a month or more
RATCHET = "crapkit-ratchet.tsv"


def git(repo: Path, *args: str, when: int = BASE) -> str:
    env = {"GIT_AUTHOR_DATE": f"@{when} +0000", "GIT_COMMITTER_DATE": f"@{when} +0000"}
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          env={**__import__("os").environ, **env}).stdout.decode("utf-8")


def new_repo(path: Path) -> Path:
    path.mkdir()
    git(path, "init", "-q")
    for key, value in (("user.name", "A"), ("user.email", "a@example.com"),
                       ("core.autocrlf", "false")):
        git(path, "config", key, value)
    return path


def sign_commits(repo: Path, keys: Path, verifiable: bool) -> None:
    """SSH-sign every commit from here on; a verifiable key has an allowed-signers file."""
    if shutil.which("ssh-keygen") is None:
        pytest.skip("ssh-keygen signs the commits")
    keys.mkdir()
    key = keys / "key"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "a@example.com",
                    "-f", str(key)], check=True, capture_output=True)
    for setting, value in (("gpg.format", "ssh"), ("user.signingkey", key.as_posix()),
                           ("commit.gpgsign", "true")):
        git(repo, "config", setting, value)
    if verifiable:
        allowed = keys / "allowed"
        allowed.write_text("a@example.com " + (keys / "key.pub").read_text(), encoding="utf-8")
        git(repo, "config", "gpg.ssh.allowedSignersFile", allowed.as_posix())


def commit(repo: Path, step: int, files: dict[str, str], body: str = "") -> None:
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8", newline="\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", f"step {step}" + (f"\n\n{body}" if body else ""),
        when=stamp(step))


def stamp(step: int) -> int:
    """Steps an hour apart, so two of them can fall on two dates."""
    return BASE + step * 3600


def day(step: int) -> str:
    """The date explain prints for a step: its UTC day, since every commit says +0000."""
    return time.strftime("%Y-%m-%d", time.gmtime(stamp(step)))


def marks(*names: str) -> str:
    """Ratchet rows, one mark per function name."""
    return "".join(f"src/e.py\t{name}( )\t{30 + len(name):.4f}\n" for name in names)


def under(repo: Path, setting: str, value: str, read):
    """read(repo) with the setting on, then with it unset: git's own default."""
    git(repo, "config", setting, value)
    try:
        return read(repo)
    finally:
        git(repo, "config", "--unset", setting)


def cold_churn(repo: Path) -> dict:
    shutil.rmtree(repo / ".crapkit", ignore_errors=True)
    return churn_cache.load_churn(repo, 12)


def ratchet_report(repo: Path) -> dict:
    return report_from_events(mark_events(marks_history(repo, RATCHET)))


@pytest.fixture(params=[True, False], ids=["verifiable", "unverifiable"])
def signed(tmp_path: Path, request) -> Path:
    """Two SSH-signed commits, each changing src/e.py and the ratchet file."""
    repo = new_repo(tmp_path / "repo")
    sign_commits(repo, tmp_path / "keys", request.param)
    commit(repo, 0, {"src/e.py": "def f():\n    return 0\n", RATCHET: marks("f")}, "body 0")
    commit(repo, 1, {"src/e.py": "def f():\n    return 1\n", RATCHET: marks("f", "g")},
           "body 1")
    return repo


def test_explain_history_names_each_signed_commit_by_its_own_name(signed):
    names = git(signed, "log", "--no-show-signature", "--format=%h").split()

    seen = under(signed, "log.showSignature", "true",
                 lambda repo: _function_commits(repo, "src/e.py", 1, 2))

    assert seen == [{"sha": names[0], "date": day(1), "subject": "step 1", "body": "body 1"},
                    {"sha": names[1], "date": day(0), "subject": "step 0", "body": "body 0"}]


def test_churn_counts_the_paths_a_signed_commit_changed_and_nothing_else(signed):
    seen = under(signed, "log.showSignature", "true", cold_churn)

    assert sorted(seen) == ["crapkit-ratchet.tsv", "src/e.py"]
    assert seen == cold_churn(signed)


def test_a_signed_commit_s_ratchet_patch_holds_its_patch_alone(signed):
    seen = under(signed, "log.showSignature", "true", lambda repo: marks_history(repo, RATCHET))

    assert seen == marks_history(signed, RATCHET)
    assert [set(r.marks) - set(r.before or {}) for r in seen] == [{("src/e.py", "f( )")},
                                                                 {("src/e.py", "g( )")}]


def test_the_ratchet_history_starts_at_its_rename_under_log_follow(tmp_path):
    """git's --follow walks the rename but drops the commit after it under --reverse."""
    repo = new_repo(tmp_path / "repo")
    commit(repo, 0, {"marks.tsv": marks("f")})
    git(repo, "mv", "marks.tsv", RATCHET)
    commit(repo, 1, {RATCHET: marks("f", "g")})
    commit(repo, 2, {RATCHET: marks("g")})

    seen = under(repo, "log.follow", "true", ratchet_report)

    assert seen == ratchet_report(repo)
    assert (seen["open"], seen["dropped_total"]) == (1, 1)


def test_a_root_commit_s_files_and_marks_count_under_show_root_false(tmp_path):
    repo = new_repo(tmp_path / "repo")
    commit(repo, 0, {"src/e.py": "a\n", "src/g.py": "a\n", RATCHET: marks("f", "g")})
    commit(repo, 1, {"src/e.py": "b\n", RATCHET: marks("g")})

    churn = under(repo, "log.showRoot", "false", cold_churn)
    report = under(repo, "log.showRoot", "false", ratchet_report)

    assert churn == cold_churn(repo)
    assert (churn["src/e.py"].commits, churn["src/g.py"].commits) == (2, 1)
    assert report == ratchet_report(repo)
    assert (report["open"], report["dropped_total"]) == (1, 1)
