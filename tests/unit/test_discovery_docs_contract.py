"""Every page that documented `--repo` as the working directory says the walk.

0.5.0 finds crapkit.toml upward (ADR 0002). The README's `--repo` line, the
lanes page's monorepo section, the agents page's server line and AGENTS.md all
stated the one-directory read; a reader who trusts any of them cds into a
workspace and reaches for `--repo ..` they no longer need.
"""
from crapkit.cli import build_parser
from test_docs_claims_contract import _section
from test_docs_claims_contract import _doc as _raw_doc

ADR = "0002-configuration-is-found-upward-nearest-wins.md"
USING = "crapkit: using crapkit.toml at"


def _doc(rel: str) -> str:
    """The page with its prose unwrapped, so a phrase is found whichever column
    the writer broke the line at."""
    return " ".join(_raw_doc(rel).split())


def test_the_lanes_page_documents_the_walk_where_it_denied_a_monorepo_mode():
    section = " ".join(_section(_raw_doc("docs/lanes.md"),
                                "## A crapkit root below the repo top").split())

    assert "There is no monorepo mode" not in section
    assert "nearest" in section and ADR in section, "the rule and its ADR are not named"
    assert USING in section, "the stderr line a user sees from a workspace is not shown"
    assert "`.git`" in section, "the stop that keeps a worktree off its parent's store"


def test_the_readme_repo_lines_say_the_default_is_the_walk():
    page = _doc("README.md")

    assert "(default: the current directory)" not in page
    assert "(default `.`)" not in page
    assert "nearest `crapkit.toml` at or above the current directory" in page


def test_the_agents_page_says_the_server_walks_from_where_it_started():
    page = _doc("docs/agent-json.md")

    assert "serves the directory the client started it in" not in page
    assert "nearest `crapkit.toml` at or above the directory the client started it in" in page


def test_agents_md_no_longer_states_the_dot_default():
    page = _doc("AGENTS.md")

    assert "`--repo PATH`, default `.`" not in page
    assert ADR in page, "the walk's ADR is where an agent reads the rule"


def test_the_flags_help_names_the_walk():
    sub = build_parser()._subparsers._group_actions[0].choices["worklist"]
    (repo,) = [act for act in sub._actions if "--repo" in act.option_strings]

    assert "nearest crapkit.toml at or above cwd" in repo.help
