"""Four more constants-only languages: objectivec, vue, java, zig.

Each is admitted the way Rust was — `SUPPORTED_LANGUAGES` plus
`LANGUAGE_EXTENSIONS` — because lizard already ships a reader for each and the
probe battery hand-counted every one of them against its reader. No new reader,
no chain placement, no default exclude.

What the battery found per language is pinned below: Vue scores the `<script>`
block and nothing else, Objective-C names a method by its selector, Java refunds
`switch` arms exactly as TypeScript does, and Zig's `switch` counts its `else`
prong as a case — the one wrong number in the four, and it inflates.
"""
import json
from pathlib import Path

import lizard

from crapkit.analyze import analyze_source
from crapkit.config import Config, Scope, load_config_text
from crapkit.scaffold import sniff_scopes
from crapkit.universe import LANGUAGE_EXTENSIONS, scan_files

ROOT = Path(__file__).resolve().parent.parent.parent

LANGUAGES = ("objectivec", "vue", "java", "zig")

OBJC = """#import <Foundation/Foundation.h>
@implementation Probe
- (NSInteger)classify:(NSInteger)n {
    if (n == 0) return 0;
    if (n == 1) return 1;
    return 9;
}
@end
"""

JAVA = """class Probe {
    int classify(int n) {
        if (n == 0) return 0;
        else if (n == 1) return 1;
        else if (n == 2) return 2;
        return 9;
    }
}
"""

JAVA_SWITCH = """class Probe {
    int dispatch(int n) {
        switch (n) {
            case 0: return 0;
            case 1: return 1;
            case 2: return 2;
            default: return 9;
        }
    }
}
"""

# The template's `v-if`, `v-else-if` and `v-for` are three branches a reader
# sees on the page and lizard does not: they are HTML attributes, not code.
VUE = """<template>
  <span v-if="a && b">yes</span>
  <span v-else-if="a || b">maybe</span>
  <p v-for="row in rows" :key="row.id">{{ row.id }}</p>
</template>

<script>
export default {
  methods: {
    classify(n) {
      if (n === 0) return 0;
      else if (n === 1) return 1;
      return 9;
    },
  },
};
</script>

<style scoped>
.probe { color: red; }
</style>
"""

ZIG_IF = """fn guard(a: bool, b: bool) bool {
    if (a and b) {
        return true;
    }
    return false;
}
"""

# Three prongs that match a value, plus `else`. Hand count: base 1 + 3 = 4.
ZIG_SWITCH = """fn classify(n: i32) i32 {
    return switch (n) {
        0 => 0,
        1 => 1,
        2 => 2,
        else => 9,
    };
}
"""


# --- the config seam ----------------------------------------------------------

def test_each_language_can_be_declared_by_a_scope():
    for language in LANGUAGES:
        cfg = load_config_text('[[scope]]\nname = "src"\npaths = ["src"]\n'
                               f'languages = ["{language}"]\n')

        assert cfg.scopes[0].languages == (language,)


def test_each_language_can_be_coverage_optional():
    """None of the four has a coverage parser here: jacoco, xccov, `zig test`
    and the vitest/istanbul path for `.vue` all write formats crapkit does not
    read, so a scope declaring one of these scores cc-only."""
    for language in LANGUAGES:
        cfg = load_config_text('[[scope]]\nname = "src"\npaths = ["src"]\n'
                               f'languages = ["{language}"]\ncoverage_optional = true\n')

        assert cfg.coverage_optional_scopes == frozenset({"src"})


# --- the file universe --------------------------------------------------------

def test_the_extensions_each_language_claims():
    claimed = {lang: LANGUAGE_EXTENSIONS[lang] for lang in LANGUAGES}

    assert claimed == {"objectivec": (".m", ".mm"), "vue": (".vue",),
                       "java": (".java",), "zig": (".zig",)}


def test_every_claimed_suffix_resolves_to_the_reader_it_was_graded_on():
    resolved = {ext: lizard.get_reader_for(f"a{ext}").__name__
                for lang in LANGUAGES for ext in LANGUAGE_EXTENSIONS[lang]}

    assert resolved == {".m": "ObjCReader", ".mm": "ObjCReader",
                        ".vue": "VueReader", ".java": "JavaReader",
                        ".zig": "ZigReader"}


def test_sources_join_a_scope_that_declares_their_language():
    scope = Scope(name="src", paths=("src",), languages=LANGUAGES)
    files = ["src/A.java", "src/App.vue", "src/Probe.m", "src/Probe.mm", "src/main.zig"]

    uni = scan_files([*files, "src/README.md"], Config(scopes=(scope,)))

    assert uni.by_scope == {"src": sorted(files)}


def test_an_objective_c_suffix_is_not_confused_with_a_shorter_one():
    """`.mm` must not read as `.m` plus a letter, in either direction: both
    suffixes belong to objectivec, so a scope declaring it claims both and a
    scope that does not claims neither."""
    scope = Scope(name="src", paths=("src",), languages=("java",))

    uni = scan_files(["src/a.m", "src/b.mm"], Config(scopes=(scope,)))

    assert uni.by_scope == {"src": []}


def test_init_sniffs_each_language_from_its_files():
    assert sniff_scopes(["app/Main.java"]) == {"app": ("java",)}
    assert sniff_scopes(["ui/App.vue"]) == {"ui": ("vue",)}
    assert sniff_scopes(["ios/Probe.m"]) == {"ios": ("objectivec",)}
    assert sniff_scopes(["zig/main.zig"]) == {"zig": ("zig",)}


# --- the measurement ----------------------------------------------------------

def test_objective_c_names_a_method_by_its_selector():
    """`ObjCReader` builds the long_name out of the selector parts, so a method
    is identified the way its callers spell it."""
    (record,) = analyze_source("src/Probe.m", OBJC)

    assert (record.long_name, record.ccn) == ("classify:( NSInteger )", 3)


def test_objective_cpp_is_read_by_the_same_reader():
    (record,) = analyze_source("src/Probe.mm", OBJC)

    assert record.ccn == 3


def test_java_counts_an_else_if_chain():
    (record,) = analyze_source("src/Probe.java", JAVA)

    assert (record.long_name, record.ccn) == ("Probe::classify( int n)", 4)


def test_a_java_switch_refunds_its_arms_exactly_as_a_typescript_one_does():
    """min(ccn_std, ccn_mod), and the modified rule charges the `switch` and
    refunds each `case`. Same policy, same numbers, new language."""
    (record,) = analyze_source("src/Probe.java", JAVA_SWITCH)

    assert (record.ccn_std, record.ccn_mod, record.ccn) == (4, 2, 2)


def test_a_vue_file_scores_its_script_block_and_nothing_else():
    """The single fact that decides whether `.vue` is worth admitting. The
    template holds three branches — `v-if`, `v-else-if`, `v-for` — and
    contributes none of them; the `<style>` block contributes nothing either.
    One record comes out, and it is the method."""
    records = analyze_source("src/App.vue", VUE)

    assert [(r.long_name, r.ccn) for r in records] == [("classify ( n )", 3)]


def test_zig_counts_its_word_operators():
    """Zig spells the logical operators `and` and `or`, and the cognitive
    extension's operator set already held both for Python's sake."""
    (record,) = analyze_source("src/guard.zig", ZIG_IF)

    assert (record.ccn, record.cognitive) == (3, 2)


def test_a_zig_switch_counts_its_else_prong_as_a_case():
    """The battery's one wrong number in these four, pinned so it reads as a
    known quirk. Hand count is 4: the base plus three prongs that match a value.
    lizard reads 5, because `else =>` is a prong like any other to a reader that
    counts `=>`. The error is inflation only — a Zig switch never scores under
    its hand count — so it can cost a refactor that was not needed but can never
    hide one that was."""
    (record,) = analyze_source("src/classify.zig", ZIG_SWITCH)

    assert record.ccn == 5, "hand count is 4; the else prong is the extra point"


# --- the surfaces that publish the language set -------------------------------

def test_the_schema_language_enum_carries_all_four():
    schema = json.loads((ROOT / "crapkit.schema.json").read_text(encoding="utf-8"))
    enum = schema["properties"]["scope"]["items"]["properties"]["languages"]["items"]["enum"]

    assert set(LANGUAGES) <= set(enum)
