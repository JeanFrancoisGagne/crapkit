"""The 0.8.1 release review read the docs against the code and found sentences the
code does not do. Each test here runs the code path a sentence describes, or reads
it, and pins the sentence that now says what it does.
"""
import re
from functools import lru_cache
from pathlib import Path

from cli_inproc_repo import commit_all, repo, seed_artifacts, template_repo  # noqa: F401

from crapkit.cli import main
from crapkit.store import SnapshotStore

ROOT = Path(__file__).resolve().parents[2]
GUIDE = "docs/upgrading.md"
_HEADING = re.compile(r"^(#{1,6}) ", re.M)


@lru_cache(maxsize=None)
def _doc(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _section(rel: str, heading: str) -> str:
    """The body under `heading`, its subsections included, joined as prose."""
    text = _doc(rel)
    level = len(heading.split(" ", 1)[0])
    start = text.index(f"\n{heading}\n") + len(heading) + 2
    ends = [m.start() for m in _HEADING.finditer(text[start:]) if len(m[1]) <= level]
    return " ".join(text[start:start + ends[0] if ends else None].split())


def _changelog_steps() -> str:
    return _section("CHANGELOG.md", "### Upgrading from 0.8.0")


# --- verify --override with no alert_command (src[2]) -------------------------------

LANE_RAN = "lane_ran.py"


def test_an_override_with_no_alert_command_exits_3_before_any_lane_and_the_notes_list_it(
        repo, capsys):  # noqa: F811
    """0.8.0 refused the override only once the verdict wanted it, after every lane
    ran: a passing tree exited 0 and a ratchet regression 7. 0.8.1 refuses it at
    load. The guide's table of the rest of the exit moves had no row for it."""
    toml = repo / "crapkit.toml"
    toml.write_text(toml.read_text(encoding="utf-8")
                    .replace('alert_command = "python append_alert.py"\n', "")
                    .replace('command = "python -c pass"', f'command = "python {LANE_RAN}"'),
                    encoding="utf-8", newline="\n")
    (repo / LANE_RAN).write_text("open('ran.txt', 'a').close()\n", encoding="utf-8")
    commit_all(repo, "no alert command")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    capsys.readouterr()
    runs = len(SnapshotStore(repo / ".crapkit" / "crap.sqlite").list_runs())

    code = main(["verify", "--override", "hotfix", "--repo", str(repo)])
    out = capsys.readouterr()

    assert (code, out.out) == (3, "")
    assert out.err.startswith("crapkit: no alert_command configured"), out.err
    assert not (repo / "ran.txt").exists(), "a lane ran before the refusal"
    assert len(SnapshotStore(repo / ".crapkit" / "crap.sqlite").list_runs()) == runs

    row = next(line for line in _doc(GUIDE).splitlines()
               if line.startswith("| `--override REASON` where `crapkit.toml` sets no `alert_command` |"))
    assert ("| `verify` | the verdict's: 0 on a passing tree, 7 on a ratchet regression, and 3 after "
            "every lane ran on a gate breach the override would grant | 3 before any lane runs, "
            "`no alert_command configured` |") in row
    assert row in _doc(GUIDE).split("## Other exit codes that move in 0.8.1", 1)[1].split("\n## ", 1)[0]
    moved = ("`verify --override` in a repo whose `crapkit.toml` sets no `alert_command` exits 3 "
             "before any lane runs")
    assert moved in _changelog_steps()
    assert moved in _section("docs/releases/0.8.1.md", "## Upgrading from 0.8.0")
