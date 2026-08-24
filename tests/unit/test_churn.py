"""Churn seam: raw git log text in, per-file commit/author counts out. Pure."""
from crapkit.churn import parse_git_log

LOG = "\x01alice\nsrc/a.ts\nsrc/b.ts\n\n\x01bob\nsrc/a.ts\n\n\x01alice\nsrc/a.ts\npylib/mod.py\n"


def test_counts_commits_and_distinct_authors_per_file():
    churn = parse_git_log(LOG)
    assert churn["src/a.ts"].commits == 3
    assert churn["src/a.ts"].authors == 2
    assert churn["src/b.ts"].commits == 1
    assert churn["src/b.ts"].authors == 1
    assert churn["pylib/mod.py"].commits == 1


def test_untouched_files_are_simply_absent():
    assert "never.ts" not in parse_git_log(LOG)


def test_empty_log_gives_empty_churn():
    assert parse_git_log("") == {}


def test_backslash_paths_normalize():
    churn = parse_git_log("\x01a\nsrc\\win.ts\n")
    assert "src/win.ts" in churn


def test_c_style_quoted_paths_decode_to_real_paths():
    # git core.quotePath=true emits literal backslash-octal TEXT for non-ASCII bytes
    log = ('\x01alice\n'
           r'"docs/r\303\251sum\303\251.md"' '\n'
           r'"docs/Prompts/ML TRADING CODEBASE \342\200\224 IMPL.md"' '\n')
    churn = parse_git_log(log)
    assert churn["docs/résumé.md"].commits == 1
    assert churn["docs/Prompts/ML TRADING CODEBASE — IMPL.md"].commits == 1


def test_quoted_path_with_escaped_quote_and_backslash():
    log = '\x01a\n' + r'"we\"ird\\name.py"' + '\n'
    churn = parse_git_log(log)
    assert churn['we"ird/name.py'].commits == 1


def test_timestamped_log_weights_recent_commits_heavier():
    # format %x01author%x02unixtime: same commit COUNT, different ages.
    log = ("\x01alice\x021000000000\nsrc/old.ts\n\n"
           "\x01alice\x021000000000\nsrc/old.ts\n\n"
           "\x01alice\x021999999000\nsrc/new.ts\n\n"
           "\x01alice\x022000000000\nsrc/new.ts\n\n")
    churn = parse_git_log(log)
    assert churn["src/new.ts"].commits == churn["src/old.ts"].commits == 2
    assert churn["src/new.ts"].weight > churn["src/old.ts"].weight, \
        "a file hot THIS month must outrank one hot last year (time-weighted risk)"


def test_untimestamped_log_falls_back_to_commit_count_weight():
    churn = parse_git_log("\x01a\nsrc/a.ts\n\n\x01b\nsrc/a.ts\n")
    assert churn["src/a.ts"].weight == 2.0
