"""The C family enters as ONE language, `cpp`, on the reader lizard already ships.

A `c` label beside a `cpp` one is unimplementable, not merely unwanted. lizard
resolves every C and C++ suffix to a single `CLikeReader`, so the two labels
could never measure differently — and `.h` is the header both dialects use, so
no rule assigns it to one of them. One label, six suffixes, and `.hh`/`.hxx`/
`.ipp` deliberately left out: lizard's own reader does not declare them, and a
suffix it does not declare is served by a silent fallback rather than a mapping.

Admission is the two constants Rust needed (`SUPPORTED_LANGUAGES`,
`LANGUAGE_EXTENSIONS`) and no reader work at all: `CLikeReader` is lizard's
oldest and needs no correction. The measurement work was cognitive complexity,
where every C++ rvalue reference read as a logical `and`; see the last section.
"""
import json
from pathlib import Path

import lizard
from lizard_languages.clike import CLikeReader

from crapkit.analyze import ANALYSIS_VERSION, analyze_source
from crapkit.config import SUPPORTED_LANGUAGES, Config, Scope, load_config_text
from crapkit.scaffold import sniff_scopes, source_candidates
from crapkit.universe import LANGUAGE_EXTENSIONS, scan_files

ROOT = Path(__file__).resolve().parent.parent.parent

CPP_SCOPE = Scope(name="src", paths=("src",), languages=("cpp",))

# Five if/else-if links plus the base: ccn 6, hand-counted.
CLASSIFY = """int classify(int n) {
    if (n == 0) return 0;
    else if (n == 1) return 1;
    else if (n == 2) return 2;
    else if (n == 3) return 3;
    else if (n == 4) return 4;
    return 9;
}
"""
CLASSIFY_CCN = 6

# A header carries real code in C: an inline definition is not a declaration.
# Two ifs plus the base: ccn 3.
CLAMP_HEADER = """static inline int clamp(int v, int lo, int hi) {
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}
"""

# Three labelled arms plus `default`. Standard counts the three: ccn 4.
DISPATCH = """int dispatch(int n) {
    switch (n) {
        case 0: return 0;
        case 1: return 1;
        case 2: return 2;
        default: return 9;
    }
}
"""

# The 6-branch shape tests/unit/test_cognitive_reader_chain.py measures in
# Python, TypeScript, Swift and Kotlin, written in C++. ccn 6, cognitive 10.
PROBE = """int probe(int a, int b) {
    if (a > 0 && b > 0) {
        for (int i = 0; i < b; i++) {
            if (i == a) return i;
        }
    } else {
        while (b > 0) b--;
    }
    return 0;
}
"""


# --- the config seam ----------------------------------------------------------

def test_a_scope_can_declare_cpp():
    cfg = load_config_text(
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["cpp"]\n')

    assert cfg.scopes[0].languages == ("cpp",)


def test_a_cpp_scope_can_be_coverage_optional():
    """cc-only is the whole shape here: gcov and llvm-cov write neither of the
    two artifact formats crapkit parses."""
    cfg = load_config_text('[[scope]]\nname = "src"\npaths = ["src"]\n'
                           'languages = ["cpp"]\ncoverage_optional = true\n')

    assert cfg.coverage_optional_scopes == frozenset({"src"})


def test_there_is_no_second_label_for_c():
    """A `c` scope and a `cpp` scope would resolve to the same reader and score
    identically, and `.h` belongs to neither dialect alone. Rejecting the label
    says that once, at load time, instead of letting a user build a split
    crapkit cannot honour."""
    assert "c" not in SUPPORTED_LANGUAGES


# --- the file universe --------------------------------------------------------

def test_every_c_family_source_joins_its_scope():
    files = ["src/a.c", "src/b.cc", "src/c.cpp", "src/d.cxx", "src/e.h", "src/f.hpp"]

    uni = scan_files([*files, "src/notes.md"], Config(scopes=(CPP_SCOPE,)))

    assert uni.by_scope == {"src": sorted(files)}


def test_the_claimed_suffixes_are_exactly_the_ones_the_reader_declares():
    """Pinned to the reader's own list rather than to a hand-typed copy, so a
    lizard release that adds or drops a suffix fails here instead of drifting."""
    reader_suffixes = {f".{ext}" for ext in CLikeReader.ext}

    assert set(LANGUAGE_EXTENSIONS["cpp"]) == reader_suffixes


def test_the_opt_in_header_spellings_stay_unclaimed():
    """`.hh`, `.hxx` and `.ipp` are real C++ headers no lizard reader declares.

    Claiming one would not fail: lizard resolves an undeclared suffix with
    `get_reader_for(f) or CLikeReader`, so it would silently take the C reader —
    correct for these three by luck, and the same silent fallback that read
    shell scripts as C until crapkit shipped a shell reader. Every suffix in
    LANGUAGE_EXTENSIONS rests on a declared mapping instead, and these three
    stay an opt-in for whoever needs them.
    """
    uni = scan_files(["src/a.hh", "src/b.hxx", "src/c.ipp"], Config(scopes=(CPP_SCOPE,)))

    assert uni.by_scope == {"src": []}
    assert uni.unclaimed == ()


def test_objective_c_sources_are_not_claimed_by_a_cpp_scope():
    """`.m` and `.mm` belong to `ObjCReader`, which is a different reader with
    different rules; a cpp scope that swallowed them would measure them with the
    wrong one."""
    uni = scan_files(["src/a.m", "src/b.mm"], Config(scopes=(CPP_SCOPE,)))

    assert uni.by_scope == {"src": []}


def test_init_sniffs_a_c_directory_as_a_cpp_scope():
    assert sniff_scopes(["src/main.c", "src/util.h"]) == {"src": ("cpp",)}


def test_init_proposes_c_sources_and_headers_alike():
    assert source_candidates(["src/main.c", "src/util.h"]) == ["src/main.c", "src/util.h"]


# --- the measurement ----------------------------------------------------------

def test_one_reader_serves_every_suffix_in_the_family():
    """The fact that makes a c/cpp split unimplementable, pinned as a fact."""
    readers = {lizard.get_reader_for(f"a{ext}").__name__
               for ext in LANGUAGE_EXTENSIONS["cpp"]}

    assert readers == {"CLikeReader"}


def test_crapkit_reads_the_hand_counted_ccn_off_a_c_file():
    (record,) = analyze_source("src/classify.c", CLASSIFY)

    assert record.ccn == CLASSIFY_CCN


def test_a_header_is_measured_like_any_other_translation_unit():
    """The reason `.h` is claimed at all: C puts `static inline` bodies in
    headers, and a header dropped from the corpus drops that code with it."""
    (record,) = analyze_source("src/clamp.h", CLAMP_HEADER)

    assert (record.long_name, record.ccn) == ("clamp( int v , int lo , int hi)", 3)


def test_a_switch_scores_on_the_modified_column_exactly_as_it_does_in_typescript():
    """crapkit takes min(ccn_std, ccn_mod), and lizard's modified rule refunds
    every `case` and charges one for the `switch`. A three-arm dispatch reads 2,
    not 4 — the same answer the same shape gets in TypeScript today. Nothing
    about admitting C changes that policy; this pins that it did not."""
    (record,) = analyze_source("src/dispatch.c", DISPATCH)

    assert (record.ccn_std, record.ccn_mod, record.ccn) == (4, 2, 2)


def test_cpp_takes_the_standard_chain_with_cognitive_at_index_zero():
    """`CLikeReader` does not inherit the Swift preprocessor that drains the
    token stream ahead of index 0, so a 0 here would mean the family needs the
    second chain. 10 under the Sonar spec: if +1, && +1, for +2, inner if +3,
    else +1, while +2."""
    (record,) = analyze_source("src/probe.cpp", PROBE)

    assert (record.cognitive, record.ccn) == (10, 6)


# --- the surfaces that publish the language set -------------------------------

def test_the_schema_language_enum_carries_cpp():
    schema = json.loads((ROOT / "crapkit.schema.json").read_text(encoding="utf-8"))
    enum = schema["properties"]["scope"]["items"]["properties"]["languages"]["items"]["enum"]

    assert "cpp" in enum


def test_analysis_version_invalidates_records_measured_before_the_rvalue_fix():
    """Every C++ record cached at version 5 carries a cognitive score inflated by
    one per rvalue-reference parameter, and the cache keys on content plus this
    fingerprint."""
    assert ANALYSIS_VERSION > 5
