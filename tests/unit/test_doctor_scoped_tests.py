"""A lane without a scoped_tests template leaves an agent with no way to test.

`crapkit test-scoped FILE` is the one command a start-editing packet can hand
over, and it exits 3 for any scope with no template. The scope is measured, the
lane is green, and the gap says so nowhere — which is what this WARN is for.
"""
from crapkit.config import Lane, load_config_text
from crapkit.doctor import scoped_test_gaps

TWO_SCOPES = """
[crapkit]
target = 6

[crapkit.scoped_tests]
web = "npx vitest run web"

[[scope]]
name = "web"
paths = ["web/src"]
languages = ["typescript"]

[[scope]]
name = "api"
paths = ["api"]
languages = ["python"]

[[lane]]
name = "web"
command = "npx vitest run --coverage"
artifact = "web/coverage/coverage-final.json"
parser = "istanbul"
scopes = ["web", "api"]
"""


def _lane(name: str, *scopes: str) -> Lane:
    return Lane(name=name, command="pytest", artifact="cov.json",
                parser="coveragepy", scopes=scopes)


def test_a_lane_scope_with_no_template_warns():
    gaps = scoped_test_gaps([_lane("py", "src")], ())

    assert [(f.level, "src" in f.text) for f in gaps] == [("WARN", True)]


def test_the_message_names_the_table_the_reader_has_to_add_to():
    text = scoped_test_gaps([_lane("py", "src")], ())[0].text

    assert "[crapkit.scoped_tests]" in text
    assert "test-scoped" in text


def test_a_templated_scope_is_silent():
    assert scoped_test_gaps([_lane("py", "src")], (("src", "python -m pytest"),)) == ()


def test_a_scope_in_no_lane_is_not_this_check_s_business():
    """An unlaned scope already fails doctor on coverage; a second line about
    its test command would just be noise."""
    assert scoped_test_gaps([_lane("py", "src")], ()) == scoped_test_gaps(
        [_lane("py", "src"), _lane("py", "src")], ())


def test_one_warning_per_scope_however_many_lanes_claim_it():
    gaps = scoped_test_gaps([_lane("unit", "src"), _lane("e2e", "src")], ())

    assert len(gaps) == 1


def test_scopes_are_reported_in_sorted_order():
    gaps = scoped_test_gaps([_lane("py", "web", "src", "gen")], ())

    assert [f.text.split("'")[1] for f in gaps] == ["gen", "src", "web"]


def test_a_loaded_config_feeds_the_check_straight_from_its_own_fields():
    """The two fields the check reads are the two a config parses: cfg.lanes and
    cfg.scoped_tests, no assembly in between."""
    cfg = load_config_text(TWO_SCOPES)

    gaps = scoped_test_gaps(cfg.lanes, cfg.scoped_tests)

    assert [f.text.split("'")[1] for f in gaps] == ["api"]


def test_the_lanes_are_walked_once():
    """One pass over the lanes, not one pass per scope.

    A one-shot iterator proves it: a per-scope rescan would find the iterator
    already drained and report nothing.
    """
    lanes = iter([_lane("a", "src"), _lane("b", "web")])

    gaps = scoped_test_gaps(lanes, ())

    assert [f.text.split("'")[1] for f in gaps] == ["src", "web"]
