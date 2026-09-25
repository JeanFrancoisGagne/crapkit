"""Shell ccn against shellmetrics.

shellmetrics lives in the accuracy image (oracles/shellmetrics_adapter.py), so
this runs on the Linux nightly cell, over the shell probe files and the full
corpus's nvm member. Each function tree-sitter lists is joined to crapkit's row
and shellmetrics' answer by path and start line. A function a known crapkit
defect or definition shape covers is set aside for that column
(ts_defect_shapes): a case with a `*)` arm (AO-SH-CASE-DEFAULT) is one.

shellmetrics counts no `&&` or `||`, where the McCabe text counts each
short-circuit operator; the transform AO-SHELLMETRICS-LOGICAL adds them back.
shellmetrics also reads a multi-line string's line that ends in `)` as a case
arm (an oracle bug), so a function holding one is set aside
(AO-SHELLMETRICS-STRING-PAREN). A hand case pins each rule with shellmetrics'
raw value and crapkit's.
"""
import pytest

from accuracy.analysis_oracles import analysis_corpora, analysis_tables, analysis_tstests
from accuracy.analysis_oracles import analysis_tooldiff as tooldiff
from accuracy.analysis_oracles.analysis_tooldiff import Tool
from accuracy.analysis_oracles.oracles import shellmetrics_adapter
from accuracy.analysis_oracles.oracles import treesitter_counters as counters
from accuracy.kit import rulings

pytestmark = [pytest.mark.nightly, pytest.mark.process, pytest.mark.platform("linux")]
LISTED = tooldiff.listed_except(set())


def logical_operators(fn, context) -> int:
    """AO-SHELLMETRICS-LOGICAL: each `&&` and `||` the function owns, which
    shellmetrics leaves out and NIST SP 500-235 sec. 4.1 counts."""
    return sum(counters.logical_operators(node, context.spec)
               for node in counters.own_nodes(fn, context.spec))


TEXT = {"string", "raw_string", "heredoc_body"}


def _paren_line(node, data: bytes) -> bool:
    lines = data[node.start_byte:node.end_byte].decode("utf-8", "replace").split("\n")
    return any(line.rstrip().endswith(")") for line in lines[:-1])


def paren_line_in_text(fn, context) -> bool:
    """AO-SHELLMETRICS-STRING-PAREN (oracle bug): shellmetrics reads a line of a
    multi-line string that ends in `)` as a case arm and adds 1."""
    return any(_paren_line(node, context.data) for node in tooldiff.below(fn, TEXT))


TOOLS = {
    "shellmetrics": Tool("ccn_std", lambda root, paths, spans: shellmetrics_adapter.ccn(root, paths),
                         transforms={"AO-SHELLMETRICS-LOGICAL": logical_operators},
                         set_aside={"AO-SHELLMETRICS-STRING-PAREN": paren_line_in_text}),
}


@pytest.fixture(scope="module")
def sh_probes(measure_set, tmp_path_factory):
    files = analysis_tstests.of_language(analysis_tables.probe_files(), "shell")
    return files, measure_set(files), tooldiff.written(files, tmp_path_factory.mktemp("sh-probes"))


@pytest.fixture(scope="module")
def sh_corpus(full_corpus, measure_set, tmp_path_factory):
    files = analysis_corpora.member_files(full_corpus, "nvm", (".sh",))
    return files, measure_set(files), tooldiff.written(files, tmp_path_factory.mktemp("sh-corpus"))


def test_shell_probes_match_shellmetrics(sh_probes, oracle):
    oracle("shellmetrics")
    outcome = tooldiff.differential("shellmetrics", TOOLS["shellmetrics"], LISTED, sh_probes)

    assert outcome.problems == []
    assert outcome.compared > 5


def test_nvm_matches_shellmetrics(sh_corpus, oracle):
    oracle("shellmetrics")
    outcome = tooldiff.differential("shellmetrics", TOOLS["shellmetrics"], LISTED, sh_corpus)

    assert outcome.problems == []
    assert outcome.compared > 50


# --- hand cases: shellmetrics' raw value and crapkit's --------------------------------------------

LOGIC = ('f() {\n    if [ -n "$1" ] && [ -n "$2" ] || [ -n "$3" ]; then\n        return 1\n'
         '    fi\n    return 0\n}\n')
DEFAULT_ARM = ('f() {\n    case "$1" in\n        a) echo 1 ;;\n        b) echo 2 ;;\n'
               '        c) echo 3 ;;\n        *) echo 0 ;;\n    esac\n}\n')
STRING_PAREN = 'f() {\n    msg="one (=)\n\ntwo"\n    echo "$msg"\n}\n'
# ruling id -> source; the first function starts on line 1.
HAND = {"AO-SHELLMETRICS-LOGICAL": LOGIC, "AO-SHELLMETRICS-STRING-PAREN": STRING_PAREN}
# rows a ts_defect_shapes shape sets aside, which shellmetrics' raw value also pins.
SHAPED = {"AO-SH-CASE-DEFAULT": DEFAULT_ARM}


@pytest.fixture(scope="module")
def hand(measure_set, tmp_path_factory):
    files = {f"cases/{ruling}.sh": source.encode() for ruling, source in {**HAND, **SHAPED}.items()}
    return files, measure_set(files), tooldiff.written(files, tmp_path_factory.mktemp("sh-hand"))


@pytest.mark.parametrize("ruling_id", sorted({**HAND, **SHAPED}))
def test_each_shell_rule_is_pinned_by_a_hand_case(ruling_id, hand, oracle):
    oracle("shellmetrics")
    tool = TOOLS["shellmetrics"]
    crapkit, raw, fn, context = tooldiff.hand_values(tool, LISTED, hand, f"cases/{ruling_id}.sh", 1)

    assert ruling_id in tooldiff.set_aside(tool, fn, context) or \
        tooldiff.expected(tool, fn, context, raw) == crapkit
    rulings.pin_ruling(ruling_id, crapkit=crapkit, oracle=raw)


def test_every_shell_rule_has_a_hand_case():
    assert tooldiff.rules(TOOLS) == sorted(HAND)
