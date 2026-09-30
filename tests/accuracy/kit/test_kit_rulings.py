"""Rulings rows: what a packet may write, and what pin_ruling asserts."""
import re

import pytest

from accuracy.kit import rulings

HEADER = "\t".join(rulings.COLUMNS)
DEFINITION = "D5\tCRAP score\tkit.exact\t4 dp tie\t0.1235\t0.1234\tdefinition\thttps://docs.python.org/3/library/functions.html#round\tREADME.md#grade\tt::a\t"
DEFECT = "D2\tCognitive complexity\tsonarjs\t??\t2\t1\tdefect\thttps://www.sonarsource.com/docs/CognitiveComplexity.pdf\t\tt::b\t#81"
FIXED = "D13\tCRAP score\tkit.exact\tpow\t3.5\t3.5\tfixed\tpaper: IEEE 754-2008 section 9.2\t\tt::c\t#82"


def _write(root, packet, *rows, header=HEADER):
    folder = root / packet
    folder.mkdir()
    (folder / "rulings.tsv").write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")


def test_rows_load_from_every_packet(tmp_path):
    _write(tmp_path, "score_model", DEFINITION, FIXED)
    _write(tmp_path, "analysis_oracles", DEFECT)

    rows = rulings.load(tmp_path)

    assert sorted(rows) == ["D13", "D2", "D5"]
    assert rows["D2"].issue == "#81"
    assert rows["D5"].source.endswith("rulings.tsv")


def test_an_id_in_two_packets_is_refused(tmp_path):
    _write(tmp_path, "a", DEFINITION)
    _write(tmp_path, "b", DEFINITION)

    with pytest.raises(rulings.RulingsError, match="D5 appears in"):
        rulings.load(tmp_path)


def test_a_wrong_header_is_refused(tmp_path):
    _write(tmp_path, "a", DEFINITION, header="id\tcalc")

    with pytest.raises(rulings.RulingsError, match="header must be id calc oracle"):
        rulings.load(tmp_path)


@pytest.mark.parametrize("row, problem", [
    (DEFECT.replace("\t#81", "\t"), "a defect names its issue"),
    (DEFINITION.replace("https://docs.python.org/3/library/functions.html#round", "radon said so"),
     "outside_support is a URL"),
    (DEFINITION.replace("\tdefinition\t", "\tmaybe\t"), "the ruling is one of"),
    (FIXED.replace("3.5\t3.5", "3.5\t3.6"), "a fixed row records one value"),
])
def test_a_row_the_kit_cannot_trust_is_refused(tmp_path, row, problem):
    _write(tmp_path, "a", row)

    with pytest.raises(rulings.RulingsError, match=problem):
        rulings.load(tmp_path)


def test_convention_only_rows_are_listed(tmp_path):
    _write(tmp_path, "a", DEFINITION.replace(
        "https://docs.python.org/3/library/functions.html#round", "convention_only"), FIXED)

    assert rulings.convention_only(rulings.load(tmp_path)) == ["D5"]


@pytest.fixture
def rows(tmp_path):
    _write(tmp_path, "a", DEFINITION, DEFECT, FIXED)
    return rulings.load(tmp_path)


def test_a_definition_pins_both_values(rows):
    rulings.pin_ruling("D5", crapkit=0.1235, oracle="0.1234", rows=rows)

    with pytest.raises(AssertionError, match="D5: crapkit says 0.1236"):
        rulings.pin_ruling("D5", crapkit=0.1236, oracle="0.1234", rows=rows)
    with pytest.raises(AssertionError, match="D5: kit.exact now says 0.1233"):
        rulings.pin_ruling("D5", crapkit=0.1235, oracle="0.1233", rows=rows)


def test_a_defect_raises_the_defect_while_crapkit_keeps_its_wrong_value(rows):
    with pytest.raises(rulings.RulingDefect, match="crapkit still says 2, sonarjs says 1"):
        rulings.pin_ruling("D2", crapkit=2, oracle=1, rows=rows)


def test_a_defect_whose_wrong_value_moved_fails_outright(rows):
    with pytest.raises(AssertionError, match="crapkit now says 3") as raised:
        rulings.pin_ruling("D2", crapkit=3, oracle=1, rows=rows)

    assert not isinstance(raised.value, rulings.RulingDefect)


def test_a_fixed_defect_passes_its_pin(rows):
    rulings.pin_ruling("D2", crapkit=1, oracle=1, rows=rows)
    rulings.pin_ruling("D13", crapkit=3.5, oracle=3.5, rows=rows)


def test_a_defect_is_a_strict_xfail_on_its_own_exception(rows):
    mark = rulings.applies("D2", rows).mark

    assert mark.name == "xfail"
    assert mark.kwargs["strict"] is True
    assert mark.kwargs["raises"] is rulings.RulingDefect
    assert "#81" in mark.kwargs["reason"]


def test_a_definition_or_fixed_row_leaves_the_test_as_it_is(rows):
    def probe():
        pass

    assert rulings.applies("D5", rows)(probe) is probe
    assert rulings.applies("D13", rows)(probe) is probe


def test_an_unknown_row_is_refused(rows):
    with pytest.raises(rulings.RulingsError, match="no rulings row D99"):
        rulings.applies("D99", rows)


def test_the_repo_rulings_load():
    """Every packet's rulings.tsv parses and passes the row checks."""
    rulings.load()


# Citation addresses that answer 404 (curl -L, 2026-09), each with the form that
# resolves: the JSX spec left facebook.github.io for facebook/jsx's spec.emu; the
# istanbuljs monorepo tags a release `<package>-v<version>`, never `<package>@<version>`;
# nyc tags `nyc-v<version>`; crap-typescript lives at github.com/fabian-barney.
MOVED = (
    (re.compile(r"facebook\.github\.io/jsx"), "github.com/facebook/jsx/blob/<commit>/spec.emu#L<n>"),
    (re.compile(r"istanbuljs/istanbuljs/blob/istanbul-lib-[a-z-]+(%40|@)"),
     "istanbuljs/istanbuljs/blob/istanbul-lib-<package>-v<version>"),
    (re.compile(r"istanbuljs/nyc/blob/v\d"), "istanbuljs/nyc/blob/nyc-v<version>"),
    (re.compile(r"github\.com/barney-media/"), "github.com/fabian-barney/"),
)


def dead_citations(rows: dict) -> list[str]:
    """`<id>: <live form>` for each row whose outside support cites a moved address."""
    return [f"{key}: {live}" for key, row in sorted(rows.items())
            for pattern, live in MOVED if pattern.search(row.outside_support)]


def test_a_citation_of_a_moved_address_is_named_with_its_live_form(tmp_path):
    _write(tmp_path, "a", DEFINITION.replace("https://docs.python.org/3/library/functions.html#round",
                                             "https://github.com/istanbuljs/nyc/blob/v18.0.0/README.md"))

    assert dead_citations(rulings.load(tmp_path)) == ["D5: istanbuljs/nyc/blob/nyc-v<version>"]


def test_no_repo_ruling_cites_an_address_that_answers_404():
    assert dead_citations(rulings.load()) == []
