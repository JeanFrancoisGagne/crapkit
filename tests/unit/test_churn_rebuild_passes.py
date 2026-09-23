"""A churn map rebuild through the laid-down log touches each line as few times as it can.

Three passes that bought nothing: the tee made one compress call and one file
write per line (635,835 of each on a large consumer repo's log), the map rebuild
stripped the commit date off every header only for the parser to split the
same header again, and the parser called the path unquoter on every path line
although only a quoted one needs it.

Every git seam is monkeypatched. The passes are counted, not timed.
"""
import zlib

import pytest

from crapkit import churn, churn_cache, churn_log
from crapkit.churn import FileChurn

HEAD = "aaaa1111aaaa1111aaaa1111aaaa1111aaaa1111"
MIB = 1 << 20

# git log is newest first; each header is %x01%an%x02%at%x02%ct as the log stores it.
LOG = ["\x01bob\x021000000500\x021000000600\n", "src/a.py\n",
       "\x01alice\x021000000000\x021000000100\n", "src/a.py\n", "src/b.py\n"]


class CountingPart:
    """The tee's scratch file, with its writes counted."""

    def __init__(self, handle):
        self._handle = handle
        self.writes = 0

    def write(self, data):
        self.writes += 1
        return self._handle.write(data)

    def __getattr__(self, name):
        return getattr(self._handle, name)


@pytest.fixture()
def git(monkeypatch):
    state = {"log": list(LOG), "parts": []}
    monkeypatch.setattr(churn_log, "_window_log", lambda root, months: iter(state["log"]))
    monkeypatch.setattr(churn_log, "head_commit", lambda root: HEAD)
    monkeypatch.setattr(churn_cache, "head_commit", lambda root: HEAD)
    opened = churn_log._open_part

    def counted(path, key):
        part = opened(path, key)
        if part is not None:
            part = CountingPart(part)
            state["parts"].append(part)
        return part

    monkeypatch.setattr(churn_log, "_open_part", counted)
    return state


def stored_text(root) -> str:
    blob = (root / ".crapkit" / churn_log.LOG_NAME).read_bytes()
    return zlib.decompress(blob).decode("utf-8")


def big_log(size: int) -> list[str]:
    lines, total, n = [], 0, 0
    while total < size:
        block = [f"\x01author{n % 7}\x02{1000000000 + n}\x02{1000000000 + n}\n",
                 f"src/module_{n % 911}/file_{n % 97}.py\n"]
        lines += block
        total += sum(len(line) for line in block)
        n += 1
    return lines


def test_a_small_log_is_deflated_in_one_write_and_its_flush(tmp_path, git):
    assert list(churn_log.log_lines(tmp_path, 12))

    assert [part.writes for part in git["parts"]] == [2]
    assert stored_text(tmp_path) == "".join(LOG)


def test_an_empty_window_is_laid_down_as_the_flush_alone(tmp_path, git):
    git["log"] = []

    assert list(churn_log.log_lines(tmp_path, 12)) == []
    assert [part.writes for part in git["parts"]] == [1]
    assert stored_text(tmp_path) == ""


def test_a_large_log_is_deflated_a_megabyte_at_a_time(tmp_path, git):
    git["log"] = big_log(int(2.5 * MIB))

    served = list(churn_log.log_lines(tmp_path, 12))

    assert len(served) == len(git["log"])
    # 2.5 MB is three batches (two full, one remainder) plus the final flush.
    assert [part.writes for part in git["parts"]] == [4]
    assert stored_text(tmp_path) == "".join(git["log"])


def test_a_line_without_its_ending_is_stored_as_one_line(tmp_path, git):
    git["log"] = [line.rstrip("\n") for line in LOG]

    assert len(list(churn_log.log_lines(tmp_path, 12))) == len(LOG)
    assert stored_text(tmp_path) == "".join(LOG)


def test_a_map_rebuild_reads_the_commit_date_off_each_header_itself(tmp_path, git, monkeypatch):
    list(churn_log.log_lines(tmp_path, 12))  # a command that needs structure laid the log down
    strips = []
    shipped = churn_log._shipped
    monkeypatch.setattr(churn_log, "_shipped", lambda line: strips.append(line) or shipped(line))

    churn = churn_cache.load_churn(tmp_path, 12)

    assert strips == [], "the map parser reads the stored header; no strip pass runs first"
    # The newest commit weighs 0.5 and the oldest 1/(1+e^12), which rounds away.
    assert churn == {"src/a.py": FileChurn(2, 2, 0.5), "src/b.py": FileChurn(1, 1, 0.0)}


def test_only_a_quoted_path_goes_through_the_unquoter(monkeypatch):
    calls = []
    unquote = churn.unquote_path
    monkeypatch.setattr(churn, "unquote_path", lambda line: calls.append(line) or unquote(line))
    log = "\x01alice\nsrc/a.py\n" + r'"docs/r\303\251sum\303\251.md"' + "\nsrc/b.py\n"

    parsed = churn.parse_git_log(log)

    assert calls == [r'"docs/r\303\251sum\303\251.md"']
    assert set(parsed) == {"src/a.py", "docs/résumé.md", "src/b.py"}
