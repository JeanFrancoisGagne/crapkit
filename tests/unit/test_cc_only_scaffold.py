"""`crapkit init` on scopes it writes no live lane for.

init writes live lanes for pytest, vitest and jest only, so a scope built only
from the other languages can own no lane init writes. init wrote it bare
anyway once: every function then scored `no-lane`, a wiring gap the repo had
no lane to close, and the summary told the reader to declare a lane first.
`coverage_optional = true` is the key that says "measured on complexity
alone", and init has everything it needs to write it: the languages it just
sniffed. When a repo-root file shows a toolchain whose format a registered
reader reads (Cargo.toml for cargo llvm-cov, bun.lock for bun, deno.json for
deno), init also leaves that toolchain's lane commented, and the scope stays
cc-only until its owner uncomments it.

A scope with ONE live-lane language keeps the key off: a lane can still
measure part of it, and coverage_optional would turn every `add-tests` verdict
in the whole scope into `ok`.
"""
import re

from crapkit.config import SUPPORTED_LANGUAGES, load_config_text
from crapkit.scaffold import (LIVE_LANE_LANGUAGES, cc_only_scope, detect_lanes,
                              npm_package, starter_toml)

GO_SCOPES = {"cmd": ("go",)}
PY_SCOPES = {"calc": ("python",)}
MIXED_SCOPES = {"calc": ("python",), "cmd": ("go",)}
RUST_SCOPES = {"core": ("rust",)}
TS_SCOPES = {"web": ("typescript",)}
HEADING = ("# Lanes for the toolchains this repo uses: uncomment one and delete coverage_optional "
           "from its scopes to measure them "
           "(docs/upgrading.md#turning-a-cc-only-scope-into-a-measured-one).")
RUST_STANZA = ["# [[lane]]", '# name = "rust"',
               '# command = "cargo llvm-cov --lcov --output-path .crapkit/cov/rust.lcov"',
               '# artifact = ".crapkit/cov/rust.lcov"', '# parser = "lcov"',
               '# scopes = ["core"]']


def _py_lanes():
    return detect_lanes(frozenset({"pyproject.toml"}), None)


def _uncommented(text: str, stanza_start: str) -> str:
    """The config with the stanza that opens at `stanza_start` uncommented and
    every coverage_optional line deleted."""
    lines = text.split("\n")
    at = lines.index(stanza_start) - 1
    end = lines.index("", at)
    body = [line[2:] for line in lines[at:end]]
    kept = [line for line in lines[:at] + body + lines[end:] if line != "coverage_optional = true"]
    return "\n".join(kept)


# --- which scopes init writes no live lane for ------------------------------------

def test_a_go_scope_is_cc_only():
    assert cc_only_scope(("go",)) is True


def test_a_python_scope_is_not_cc_only():
    assert cc_only_scope(("python",)) is False


def test_one_live_lane_language_keeps_a_mixed_scope_off_the_cc_only_list():
    """A lane measuring the .ts files in a scope that also holds .go files is a
    real lane. Marking the scope optional would forgive the TypeScript too."""
    assert cc_only_scope(("go", "typescript")) is False


def test_every_language_init_writes_no_live_lane_for_is_cc_only():
    """The handbook's language table marks each of these cc-only by default.
    The set is the code's answer to that table, so the two cannot drift."""
    assert LIVE_LANE_LANGUAGES == frozenset({"python", "javascript", "typescript", "tsx", "vue"})
    assert SUPPORTED_LANGUAGES - LIVE_LANE_LANGUAGES == frozenset({
        "swift", "go", "rust", "shell", "powershell", "cpp", "objectivec", "java", "zig"})


def test_every_live_lane_language_is_a_language_crapkit_supports():
    assert LIVE_LANE_LANGUAGES <= SUPPORTED_LANGUAGES


def test_every_language_a_detectable_lane_measures_is_a_live_lane_language():
    """The lane detector is the other half of the same fact. A language a lane
    can claim but this set calls cc-only would get both a lane and the key that
    says it needs none."""
    lanes = detect_lanes(frozenset({"pyproject.toml"}),
                         npm_package({"scripts": {"test": "vitest run"}}))
    measured = {lang for lane in lanes for lang in lane.languages}

    assert measured <= LIVE_LANE_LANGUAGES


# --- what init writes ---------------------------------------------------------

def test_init_marks_a_go_only_scope_coverage_optional():
    cfg = load_config_text(starter_toml(GO_SCOPES))

    assert cfg.coverage_optional_scopes == frozenset({"cmd"})


def test_init_leaves_the_key_off_a_python_scope():
    """The key is a promise that no lane init writes can measure the scope. On
    python it would forgive coverage the repo is one `pip install pytest-cov`
    away."""
    assert "coverage_optional" not in starter_toml(PY_SCOPES, _py_lanes())


def test_a_mixed_repo_marks_only_the_scope_with_no_live_lane():
    cfg = load_config_text(starter_toml(MIXED_SCOPES, _py_lanes()))

    assert cfg.coverage_optional_scopes == frozenset({"cmd"})


def test_the_python_lane_still_claims_the_python_scope_in_a_mixed_repo():
    """The go scope going optional must not cost the py lane its own scope."""
    cfg = load_config_text(starter_toml(MIXED_SCOPES, _py_lanes()))

    assert {lane.name: lane.scopes for lane in cfg.lanes} == {"py": ("calc",)}


def test_a_mixed_repo_leaves_no_scope_without_a_lane_or_the_key():
    """The whole point: after init, nothing in this repo scores `no-lane`."""
    cfg = load_config_text(starter_toml(MIXED_SCOPES, _py_lanes()))

    assert cfg.lane_less_scopes == ()


def test_the_key_lands_inside_its_own_scope_stanza():
    """Byte placement, because TOML happily reads a stray key into the wrong
    table: the line must sit under the go scope, not the python one."""
    stanza = starter_toml(MIXED_SCOPES, _py_lanes()).split("[[scope]]")[2]

    assert 'name = "cmd"' in stanza
    assert "coverage_optional = true" in stanza


# --- the commented lane a detected toolchain earns ---------------------------------

def test_a_rust_scope_with_cargo_toml_gains_one_heading_and_the_rust_stanza():
    bare = starter_toml(RUST_SCOPES).split("\n")
    text = starter_toml(RUST_SCOPES, markers=frozenset({"Cargo.toml"}))
    lines = text.split("\n")
    at = lines.index(HEADING)

    assert lines[at + 1:at + 7] == RUST_STANZA
    assert lines[at + 7] == ""
    assert len(lines) == len(bare) + 8, "one heading, six stanza lines, one blank"
    assert lines[:at] + lines[at + 8:] == bare
    assert load_config_text(text).coverage_optional_scopes == frozenset({"core"})


def test_the_uncommented_rust_stanza_loads_and_measures_the_scope():
    text = starter_toml(RUST_SCOPES, markers=frozenset({"Cargo.toml"}))

    cfg = load_config_text(_uncommented(text, '# name = "rust"'))

    assert [(lane.name, lane.parser, lane.scopes) for lane in cfg.lanes] == [
        ("rust", "lcov", ("core",))]
    assert cfg.coverage_optional_scopes == frozenset()


def test_a_go_repo_with_cargo_toml_elsewhere_gets_no_stanza():
    """The stanza needs a scope in its languages; Cargo.toml beside Go code alone
    names nothing to measure."""
    assert starter_toml(GO_SCOPES, markers=frozenset({"Cargo.toml"})) == starter_toml(GO_SCOPES)


def test_a_typescript_scope_with_bun_lock_and_no_live_lane_gets_the_bun_stanza():
    text = starter_toml(TS_SCOPES, markers=frozenset({"bun.lock"}))

    assert HEADING in text
    assert '# name = "bun"' in text and '# scopes = ["web"]' in text
    assert "coverage_optional = true" not in text, "a Bun scope was never cc-only"
    cfg = load_config_text(_uncommented(text, '# name = "bun"'))
    assert [(lane.name, lane.artifact, lane.parser) for lane in cfg.lanes] == [
        ("bun", ".crapkit/cov/bun/lcov.info", "lcov")]


def test_a_deno_scope_gets_the_deno_stanza_and_it_loads():
    text = starter_toml(TS_SCOPES, markers=frozenset({"deno.jsonc"}))

    cfg = load_config_text(_uncommented(text, '# name = "deno"'))

    assert [(lane.name, lane.artifact) for lane in cfg.lanes] == [("deno", ".crapkit/cov/deno.lcov")]


def test_a_live_vitest_lane_over_the_scope_leaves_no_bun_or_deno_stanza():
    lanes = detect_lanes(frozenset(), npm_package({"devDependencies": {"vitest": "5"}}))

    for marker in ("bun.lock", "deno.json"):
        text = starter_toml(TS_SCOPES, lanes, markers=frozenset({marker}))
        assert HEADING not in text and "lcov" not in text, marker
        assert text == starter_toml(TS_SCOPES, lanes)


def test_the_stanza_names_only_the_scopes_no_live_lane_measures():
    scopes = {"calc": ("python",), "core": ("rust",), "web": ("typescript",)}

    text = starter_toml(scopes, _py_lanes(), markers=frozenset({"Cargo.toml", "bun.lock"}))

    assert re.findall(r'^# scopes = (.+)$', text, re.M)[-2:] == ['["web"]', '["core"]']
    assert text.count(HEADING) == 1
