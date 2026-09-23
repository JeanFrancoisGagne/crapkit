"""The commit table answers what the line-by-line parser answered.

Churn is now computed from a table of the window's commits rather than from
running counts. These are the log shapes where the two could part: a path whose
commits are only partly timestamped, path lines ahead of the first header, and
a commit that touched nothing. Expected values are worked from the recency
logistic, 0.5 at the newest author date and 1/(1+e^12) = 0.0000061 at the oldest.
"""
from crapkit.churn import FileChurn, fold, parse_git_log


def test_an_untimestamped_commit_counts_but_does_not_weigh():
    log = ("\x01a\x022000000000\nsrc/a.ts\n\n"
           "\x01b\nsrc/a.ts\n\n"
           "\x01c\x021000000000\nsrc/b.ts\n")

    churn = parse_git_log(log)

    assert churn["src/a.ts"] == FileChurn(2, 2, 0.5), "b's commit counts, only a's weighs"
    assert churn["src/b.ts"] == FileChurn(1, 1, 0.0)


def test_paths_ahead_of_the_first_header_are_nobodys():
    churn = parse_git_log("src/orphan.py\n\x01a\x021000000000\nsrc/a.ts\n")

    assert churn == {"src/a.ts": FileChurn(1, 1, 1.0)}


def test_a_commit_that_touched_nothing_dates_no_range():
    """x's date would make y's commit the newest of two (0.5); alone it is one
    date, no range, and counts once."""
    churn = parse_git_log("\x01y\x022000000000\nsrc/a.ts\n\x01x\x021000000000\n")

    assert churn == {"src/a.ts": FileChurn(1, 1, 1.0)}


def test_a_stored_header_gives_up_its_commit_date():
    table = fold(["\x01a\x021000000000\x021000000600\n", "src/a.ts\n",
                  "\x01b\x021000000001\x02notadate\n", "src/b.ts\n"])

    assert [(c.at, c.ct) for c in table.commits.values()] == [(1000000000, 1000000600),
                                                              (1000000001, None)]
    assert not table.dated, "a commit without its commit date cannot expire"
