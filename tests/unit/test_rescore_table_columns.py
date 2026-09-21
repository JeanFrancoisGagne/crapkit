"""Every remedy fits the rescore table's remedy column."""
from crapkit.cli.scoring import _print_rescore_table
from crapkit.score import ScoredRow

LATEST = {"id": 1, "commit": "0123456789abcdef"}


def row(name: str, remedy: str) -> ScoredRow:
    return ScoredRow("src", "a.py", name, 1, 9, 3, 3, 3, 5, 1, 1, 0.0, "untested", 12.0, remedy, 0)


def test_the_function_column_starts_under_its_heading_for_every_remedy(capsys):
    rows = [row("f( )", "split-lines"), row("g( )", "add-tests"), row("h( )", "decompose"),
            row("k( )", "ok")]

    _print_rescore_table(rows, LATEST)

    heading, *body = capsys.readouterr().out.splitlines()[1:]
    assert {line.index("a.py:") for line in body} == {heading.index("function")}
