"""Rust enters the file universe as a cc-only language, on the corrected reader.

Two constants admit it — `SUPPORTED_LANGUAGES` and `LANGUAGE_EXTENSIONS` — and no
third. Go needed a `**/*_test.go` default exclude because it puts its tests beside
the source; Rust puts integration tests in `tests/`, which `_TEST_DIR` already
catches, and unit tests inside a `#[cfg(test)] mod` in the source file itself,
which no path glob can separate from the code around it.

The measurement is the other half of admission: lizard resolves `.rs` to a reader
that counts no `match` arm (lizard #494), so a Rust corpus admitted on the stock
reader would report a dispatch table as trivial. `crapkit.analyze` registers
`CorrectedRustReader` at import, which is the module the process pool's children
import too.
"""
import os
import subprocess
import sys
from pathlib import Path

import crapkit
from crapkit.analyze import ANALYSIS_VERSION, analyze_source
from crapkit.config import Config, Scope, load_config_text
from crapkit.scaffold import DEFAULT_EXCLUDES, sniff_scopes, source_candidates
from crapkit.universe import scan_files

SRC = str(Path(crapkit.__file__).resolve().parent.parent)

RUST_SCOPE = Scope(name="rs", paths=("src",), languages=("rust",))

# Five arms that match a value, plus the wildcard: base 1 + 5 = 6, the same as the
# five-branch if/else-if chain doing the same work. The stock reader reads 2.
CLASSIFY = """fn classify(n: i32) -> &'static str {
    match n {
        0 => "zero",
        1 => "one",
        2 => "two",
        3 => "three",
        4 => "four",
        _ => "many",
    }
}
"""
CLASSIFY_CCN = 6

# The 6-branch shape tests/unit/test_cognitive_reader_chain.py measures in Python,
# TypeScript, Swift and Kotlin, written in Rust. ccn 6, cognitive 10.
PROBE = """fn probe(a: i32, b: i32) -> i32 {
    let mut b = b;
    if a > 0 && b > 0 {
        for i in 0..b {
            if i == a {
                return i;
            }
        }
    } else {
        while b > 0 {
            b -= 1;
        }
    }
    0
}
"""


# --- the config seam ----------------------------------------------------------

def test_a_scope_can_declare_rust():
    cfg = load_config_text(
        '[[scope]]\nname = "rs"\npaths = ["src"]\nlanguages = ["rust"]\n')
    assert cfg.scopes[0].languages == ("rust",)


def test_a_rust_scope_can_be_coverage_optional():
    """cc-only is the whole shape for Rust: no coverage parser, no lane, no artifact."""
    cfg = load_config_text('[[scope]]\nname = "rs"\npaths = ["src"]\n'
                           'languages = ["rust"]\ncoverage_optional = true\n')
    assert cfg.coverage_optional_scopes == frozenset({"rs"})


# --- the file universe --------------------------------------------------------

def test_a_rust_source_file_joins_its_scope():
    uni = scan_files(["src/main.rs", "src/notes.md"], Config(scopes=(RUST_SCOPE,)))
    assert uni.by_scope == {"rs": ["src/main.rs"]}


def test_a_rust_integration_test_needs_no_new_default_exclude():
    """Cargo puts integration tests in `tests/`, and `_TEST_DIR` has always cut
    that directory. Go needed a glob only because `foo_test.go` sits beside the
    source; nothing in Cargo's layout does."""
    scope = Scope(name="rs", paths=("src", "tests"), languages=("rust",))
    cfg = Config(scopes=(scope,), exclude_globs=DEFAULT_EXCLUDES)
    uni = scan_files(["src/main.rs", "tests/cli.rs"], cfg)

    assert uni.by_scope == {"rs": ["src/main.rs"]}


def test_init_never_proposes_a_rust_integration_test_as_source():
    assert source_candidates(["src/main.rs", "tests/cli.rs"]) == ["src/main.rs"]


def test_init_sniffs_a_rust_directory_as_a_rust_scope():
    assert sniff_scopes(["src/main.rs", "src/lib.rs"]) == {"src": ("rust",)}


# --- the measurement ----------------------------------------------------------

def _reader_after_importing_analyze(filename: str) -> str:
    """The reader a FRESH interpreter resolves once it has imported nothing but
    crapkit.analyze. A pool worker makes exactly that import and no other, so a
    registration wired anywhere else would measure Rust with the stock reader."""
    code = ("import lizard, crapkit.analyze\n"
            f"print(lizard.get_reader_for({filename!r}).__name__)\n")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([SRC, env.get("PYTHONPATH", "")])
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True,
                          text=True, env=env, timeout=180)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_importing_the_analysis_module_alone_registers_the_corrected_reader():
    assert _reader_after_importing_analyze("src/main.rs") == "CorrectedRustReader"


def test_crapkit_reads_the_hand_counted_match_ccn_off_a_rust_file():
    (record,) = analyze_source("src/main.rs", CLASSIFY)

    assert record.ccn == CLASSIFY_CCN


def test_the_modified_column_does_not_refund_the_match_arms():
    """crapkit takes min(ccn_std, ccn_mod). lizard's modified rule adds a point
    for a `match` opener only when the reader sets `_keyword_match`, which
    RustReader never does, so the two columns agree and nothing is refunded."""
    (record,) = analyze_source("src/main.rs", CLASSIFY)

    assert (record.ccn_std, record.ccn_mod) == (CLASSIFY_CCN, CLASSIFY_CCN)


def test_rust_takes_the_standard_chain_with_cognitive_at_index_zero():
    """The same 6-branch shape tests/unit/test_cognitive_reader_chain.py counts in
    four languages, written in Rust. 10 under the Sonar spec: if +1, && +1, for
    +2, inner if +3, else +1, while +2. RustReader does not inherit the Swift
    preprocessor that drains the stream ahead of index 0, so a 0 here would mean
    Rust needs the second chain."""
    (record,) = analyze_source("src/main.rs", PROBE)

    assert (record.cognitive, record.ccn) == (10, 6)


def test_a_match_block_costs_nothing_cognitive():
    """The documented gap, pinned so it reads as a decision. The cognitive
    extension's keyword set is the C family's and holds `switch`, not `match`;
    widening it would move every Python `match` score too. Rust's cyclomatic
    column is where match arms are counted."""
    (record,) = analyze_source("src/main.rs", CLASSIFY)

    assert record.cognitive == 0


def test_analysis_version_invalidates_caches_written_before_the_rust_fix():
    """Cached Rust records were measured by the stock reader at version 4, and a
    cache keys on content plus this fingerprint, so 4 must not be reusable."""
    assert ANALYSIS_VERSION > 4
