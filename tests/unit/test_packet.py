"""The start-editing packet, assembled from values alone.

Every builder here takes data a caller already read and returns a dict, so the
shape an agent parses can be argued about without a repo, a store, a git spawn
or a lane. The wiring that feeds them lives in cli/queue.py and is measured in
test_brief_batch.py.
"""
from types import SimpleNamespace

from crapkit import packet
from crapkit.score import ScoredRow


def row(name: str = "alpha( a , b )", *, start: int = 1, end: int = 20, ccn: int = 8,
        crap: float | None = None, remedy: str = "decompose",
        scope: str = "core", path: str = "core/alpha.py") -> ScoredRow:
    return ScoredRow(scope, path, name, start, end, ccn, ccn, ccn, 18, 2, 1, 0.5,
                     "measured", float(ccn * ccn) if crap is None else crap, remedy, 0)


# --- the whole file, not the one row brief kept -------------------------------

def test_every_scored_row_in_the_file_is_published():
    rows = [row(), row("helper( a )", start=22, end=25, ccn=2, remedy="ok")]

    assert packet.file_functions(rows) == [
        {"function": "alpha( a , b )", "start": 1, "end": 20, "ccn": 8,
         "crap": 64.0, "remedy": "decompose"},
        {"function": "helper( a )", "start": 22, "end": 25, "ccn": 2,
         "crap": 4.0, "remedy": "ok"},
    ]


def test_the_file_totals_count_against_each_rows_own_ceiling():
    rows = [row(), row("helper( a )", ccn=3, scope="tools")]

    totals = packet.file_totals(rows, {"core": 6, "tools": 20}, 6)

    assert totals == {"functions": 2, "over_target": 1, "crap_load": 73.0}, \
        "9 is under the tools ceiling of 20; 64 is over core's 6"


def test_a_scope_with_no_ceiling_of_its_own_takes_the_repo_target():
    assert packet.file_totals([row(scope="unlisted")], {}, 6)["over_target"] == 1


# --- the gate rule, spelled out -----------------------------------------------

def test_the_gate_rule_names_the_ceiling_the_mark_and_what_it_binds():
    rule = packet.gate_rule(ceiling=6, mark=72.0, mark_age_days=12, diff_uncovered_max=None)

    assert rule == {"ceiling": 6, "binds": packet.GATE_BINDS, "ratchet_mark": 72.0,
                    "mark_age_days": 12, "diff_uncovered_max": None}
    assert "changed functions only" in packet.GATE_BINDS


def test_the_age_of_a_mark_is_measured_from_the_newest_commit_in_the_history():
    key = ("core/alpha.py", "alpha( a , b )")
    events = [(0, key, "added", 72.0), (5 * packet.DAY, ("x", "y"), "added", 9.0)]

    assert packet.mark_age_days(events, key) == 5


def test_a_repaid_mark_that_came_back_is_aged_from_its_return():
    key = ("core/alpha.py", "alpha( a , b )")
    events = [(0, key, "added", 72.0), (2 * packet.DAY, key, "dropped", 72.0),
              (3 * packet.DAY, key, "added", 80.0)]

    assert packet.mark_age_days(events, key) == 0


def test_a_key_no_commit_ever_marked_has_no_age():
    assert packet.mark_age_days([(0, ("a", "b"), "added", 1.0)], ("c", "d")) is None


def test_a_repaid_mark_has_no_age():
    key = ("a", "b")
    events = [(0, key, "added", 1.0), (packet.DAY, key, "dropped", 1.0)]

    assert packet.mark_age_days(events, key) is None


# --- the lane that measures this function -------------------------------------

LANE = SimpleNamespace(name="py", command="python -m pytest", artifact="cov.json",
                       parser="coveragepy", cwd="", env=(("CI", "1"),),
                       timeout_seconds=0, scopes=("core",))


def test_the_lane_record_is_published_verbatim():
    assert packet.lane_record(LANE) == {
        "name": "py", "command": "python -m pytest", "artifact": "cov.json",
        "parser": "coveragepy", "cwd": "", "env": {"CI": "1"}, "timeout_seconds": 0}


def test_no_lane_is_null_rather_than_an_empty_record():
    assert packet.lane_record(None) is None


def test_the_lane_is_the_first_one_claiming_the_scope():
    other = SimpleNamespace(name="js", scopes=("web",))

    assert packet.lane_for("core", [other, LANE]) is LANE
    assert packet.lane_for("web", [other, LANE]) is other
    assert packet.lane_for("nobody", [other, LANE]) is None
    assert packet.lane_for(None, [other, LANE]) is None


# --- the commands an agent runs next ------------------------------------------

def test_the_commands_carry_the_path_and_the_function_filled_in():
    out = packet.commands("core/alpha.py", 'pytest "core/alpha.py"')

    assert out == {
        "gate": "crapkit rescore core/alpha.py --gate",
        "scoped_tests": 'pytest "core/alpha.py"',
        "verify": "crapkit verify",
        # a second `brief` re-reads the snapshot that is already stale, so the
        # field that answers `stale: true` has to be the one that writes a run
        "refresh": "crapkit coverage --reuse-unchanged",
        "refresh_writes_run": True,
    }


def test_an_unconfigured_scoped_test_command_is_null_and_says_why():
    out = packet.commands("core/alpha.py", None,
                          note="no [crapkit.scoped_tests] template for scope 'core'")

    assert out["scoped_tests"] is None
    assert out["scoped_tests_note"] == "no [crapkit.scoped_tests] template for scope 'core'"


def test_a_configured_command_carries_no_note():
    assert "scoped_tests_note" not in packet.commands("a.py", "pytest")


# --- regrowth: complexity that came back --------------------------------------

def hist(*ccns: int) -> list[dict]:
    return [{"run_id": i, "ccn": c} for i, c in enumerate(ccns, 1)]


def test_a_function_that_fell_and_rose_again_is_regrown():
    out = packet.regrowth(hist(12, 5, 9))

    assert out == {"regrown": True, "history": [[1, 12], [2, 5], [3, 9]]}


def test_a_function_that_only_ever_grew_is_not_regrown():
    assert packet.regrowth(hist(3, 6, 9))["regrown"] is False


def test_a_function_that_only_ever_fell_is_not_regrown():
    assert packet.regrowth(hist(12, 6, 3))["regrown"] is False


def test_one_run_of_history_is_not_a_trajectory():
    assert packet.regrowth(hist(12)) == {"regrown": False, "history": [[1, 12]]}


# --- params: the signature, parsed --------------------------------------------

def test_python_params_split_on_the_comma_and_drop_the_default():
    assert packet.params("f( a , b = 1 , c : int = 2 )") == [
        {"name": "a", "type": None}, {"name": "b", "type": None},
        {"name": "c", "type": "int"}]


def test_a_star_arg_keeps_its_star_and_loses_the_space_lizard_printed():
    assert packet.params("f( * args , ** kw )") == [
        {"name": "*args", "type": None}, {"name": "**kw", "type": None}]


def test_a_typescript_signature_reads_the_name_first_and_the_type_after():
    assert packet.params("dispatch ( a , b Record , c )") == [
        {"name": "a", "type": None}, {"name": "b", "type": "Record"},
        {"name": "c", "type": None}]


def test_an_empty_parameter_list_parses_to_nothing():
    assert packet.params("f( )") == []


def test_an_anonymous_function_still_has_its_parameter_list_read():
    """lizard names it `(anonymous) ( z )`, so the FIRST `(` is part of the name."""
    assert packet.params("(anonymous) ( z )") == [{"name": "z", "type": None}]
    assert packet.params("(anonymous) ( )") == []


def test_a_name_with_no_parentheses_is_unparseable_rather_than_a_guess():
    assert packet.params("f") == []
    assert packet.params("") == []


def test_a_nested_generic_is_one_parameter():
    assert packet.params("f( a Map<string , number> , b )") == [
        {"name": "a", "type": "Map<string , number>"}, {"name": "b", "type": None}]


# --- neighbours: the two lists gain one field each ----------------------------

RANKED = [{"files": ["core/alpha.py", "tests/test_alpha.py"], "support": 6, "confidence": 1.0},
          {"files": ["core/beta.py", "core/alpha.py"], "support": 5, "confidence": 0.8},
          {"files": ["core/beta.py", "core/gamma.py"], "support": 9, "confidence": 0.9}]


def test_coupling_keeps_its_fields_and_says_which_partner_is_a_test():
    out = packet.coupling_partners(RANKED, "core/alpha.py", lambda p: p.startswith("tests/"))

    assert out == [
        {"path": "tests/test_alpha.py", "support": 6, "confidence": 1.0, "is_test": True},
        {"path": "core/beta.py", "support": 5, "confidence": 0.8, "is_test": False}]


def test_coupling_ranks_before_it_cuts():
    out = packet.coupling_partners(RANKED, "core/alpha.py", lambda p: False, top=1)

    assert [p["path"] for p in out] == ["tests/test_alpha.py"]


def test_a_twin_the_dup_module_did_not_flag_is_not_contained():
    twins = [{"path": "x.py", "similarity": 0.9}, {"path": "y.py", "similarity": 1.0,
                                                   "contained": True}]

    assert packet.with_contained(twins) == [
        {"path": "x.py", "similarity": 0.9, "contained": False},
        {"path": "y.py", "similarity": 1.0, "contained": True}]


# --- notes and versions -------------------------------------------------------

def test_notes_are_null_until_the_config_carries_them():
    assert packet.notes(SimpleNamespace(), SimpleNamespace()) == {"repo": None, "scope": None}


def test_notes_are_read_off_whichever_object_carries_them():
    cfg = SimpleNamespace(notes="repo rule")
    scope = SimpleNamespace(notes="scope rule")

    assert packet.notes(cfg, scope) == {"repo": "repo rule", "scope": "scope rule"}


def test_a_row_in_no_declared_scope_still_reports_the_repo_note():
    assert packet.notes(SimpleNamespace(notes="repo rule"), None) == \
        {"repo": "repo rule", "scope": None}


def test_the_versions_block_adds_the_analysis_version_to_doctors():
    out = packet.versions_block({"crapkit": "0.1.0", "lizard": "1.24.0", "python": "3.13.0"}, 4)

    assert out == {"crapkit": "0.1.0", "lizard": "1.24.0", "python": "3.13.0",
                   "analysis_version": 4}


# --- source: the function's own text ------------------------------------------

TEXT = "def alpha(a):\n    return a\n\n\ndef beta(b):\n    return b\n"


def test_the_source_is_the_span_the_row_names():
    assert packet.function_source(TEXT, 5, 6) == "def beta(b):\n    return b"


def test_a_file_that_was_never_read_has_no_source():
    assert packet.function_source(None, 1, 2) is None


def test_scope_notes_come_from_the_configs_own_table():
    """The config stores per-scope notes in cfg.scope_notes, not on the Scope
    record: the packet must read the table, whatever object names the scope."""
    from types import SimpleNamespace

    from crapkit.packet import notes

    cfg = SimpleNamespace(notes=("housewide",),
                          scope_notes={"src": ("trap one", "trap two")})
    scope = SimpleNamespace(name="src", notes=None)

    assert notes(cfg, scope) == {"repo": ["housewide"],
                                 "scope": ["trap one", "trap two"]}
