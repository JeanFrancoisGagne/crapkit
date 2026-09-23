"""Coupling sends only a quoted path line through the unquoter.

With core.quotePath off, git quotes a path only when it holds a double quote
or a control character, so every other path line already is the path. The
call on every line cost 116 of 579 ms in churn's parser over a large consumer
repo's log, and coupling reads the same log on every HEAD move. Calls are
counted, not timed.
"""
from crapkit import coupling

QUOTED = r'"docs/r\303\251sum\303\251.md"'


def test_only_a_quoted_path_goes_through_the_unquoter(monkeypatch):
    calls = []
    unquote = coupling.unquote_path
    monkeypatch.setattr(coupling, "unquote_path", lambda line: calls.append(line) or unquote(line))
    log = ("\x01alice\x021000000000\x021000000000\nsrc/a.py\n" + QUOTED + "\nsrc/b.py\n"
           "\x01bob\x021000000500\x021000000500\nsrc/a.py\nsrc/b.py\n")

    pairs = coupling.change_coupling(log, min_support=1, top=None)

    assert calls == [QUOTED]
    assert {tuple(pair["files"]) for pair in pairs} == {
        ("src/a.py", "src/b.py"), ("docs/résumé.md", "src/a.py"), ("docs/résumé.md", "src/b.py")}
