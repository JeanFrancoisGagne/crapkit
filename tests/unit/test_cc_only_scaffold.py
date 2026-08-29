"""`crapkit init` on scopes no coverage parser can read.

Nine of the fourteen languages have no coverage parser, so a scope built only
from them can never own a lane. init wrote it bare anyway: every function then
scored `no-lane`, a wiring gap the repo has no way to close, and the summary
told the reader to declare a lane first. `coverage_optional = true` is the key
that says "measured on complexity alone", and init has everything it needs to
write it — the languages it just sniffed.

A scope with ONE coverable language keeps the key off: a lane can still measure
part of it, and coverage_optional would turn every `add-tests` verdict in the
whole scope into `ok`.
"""
from crapkit.config import SUPPORTED_LANGUAGES, load_config_text
from crapkit.scaffold import (COVERABLE_LANGUAGES, cc_only_scope, detect_lanes,
                              starter_toml)

GO_SCOPES = {"cmd": ("go",)}
PY_SCOPES = {"calc": ("python",)}
MIXED_SCOPES = {"calc": ("python",), "cmd": ("go",)}


def _py_lanes():
    return detect_lanes(frozenset({"pyproject.toml"}), "")


# --- which scopes have no parser to wait for ---------------------------------

def test_a_go_scope_is_cc_only():
    assert cc_only_scope(("go",)) is True


def test_a_python_scope_is_not_cc_only():
    assert cc_only_scope(("python",)) is False


def test_one_coverable_language_keeps_a_mixed_scope_off_the_cc_only_list():
    """A lane measuring the .ts files in a scope that also holds .go files is a
    real lane. Marking the scope optional would forgive the TypeScript too."""
    assert cc_only_scope(("go", "typescript")) is False


def test_every_language_no_parser_reads_is_cc_only():
    """The README's language table has one `none: cc-only` row per language here.
    The set is the code's answer to that table, so the two cannot drift."""
    assert SUPPORTED_LANGUAGES - COVERABLE_LANGUAGES == frozenset({
        "swift", "go", "rust", "shell", "powershell", "cpp", "objectivec", "java", "zig"})


def test_every_coverable_language_is_a_language_crapkit_supports():
    assert COVERABLE_LANGUAGES <= SUPPORTED_LANGUAGES


def test_every_language_a_detectable_lane_measures_is_coverable():
    """The lane detector is the other half of the same fact. A language a lane
    can claim but this set calls cc-only would get both a lane and the key that
    says it needs none."""
    lanes = detect_lanes(frozenset({"pyproject.toml"}),
                         '{"scripts": {"test": "vitest run"}}')
    measured = {lang for lane in lanes for lang in lane.languages}

    assert measured <= COVERABLE_LANGUAGES


# --- what init writes ---------------------------------------------------------

def test_init_marks_a_go_only_scope_coverage_optional():
    cfg = load_config_text(starter_toml(GO_SCOPES))

    assert cfg.coverage_optional_scopes == frozenset({"cmd"})


def test_init_leaves_the_key_off_a_python_scope():
    """The key is a promise that no lane can ever measure the scope. On python
    it would forgive coverage the repo is one `pip install pytest-cov` away."""
    assert "coverage_optional" not in starter_toml(PY_SCOPES, _py_lanes())


def test_a_mixed_repo_marks_only_the_scope_with_no_parser():
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
