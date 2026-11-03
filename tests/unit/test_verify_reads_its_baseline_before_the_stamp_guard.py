"""verify settles its baseline first, then refuses a stale stamp with the seed that clears it.

The stamp guard used to run before verify read `--baseline`. On a store pinned
behind a failed verify (#75) that cost three things: a named run that could not
serve was refused for the stamp instead of for itself, the taint warning naming
`--baseline` never printed, and the stamp refusal prescribed a plain `ratchet
seed`, which reads the pinned run and cannot clear it. Reading the baseline
first lets the refusal name the run to seed from.
"""
from pathlib import Path

from cli_inproc_repo import repo, template_repo  # noqa: F401
from pinned_store import pinned, stale_marks, write_run, twin

import lizard

from crapkit import invocation
from crapkit import ratchet
from crapkit.cli import main
from crapkit.cli.verifying import _stamp_refusal
from crapkit.invocation import _self

ROOT = Path(__file__).resolve().parent.parent.parent


def verify(repo, capsys, *argv: str) -> tuple[int, str, str]:
    code = main(["verify", "--reuse-artifacts", *argv, "--repo", str(repo)])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_a_named_run_this_crapkit_measured_is_the_run_the_refusal_seeds_from(repo, capsys):
    _, _, fresh = pinned(repo)
    stale_marks(repo)
    capsys.readouterr()

    code, _, err = verify(repo, capsys, "--baseline", str(fresh))

    assert code == 3
    assert err.strip().endswith(
        "CRAP scores are not comparable across metric versions; re-baseline from run "
        f"{fresh} with `{_self()} ratchet seed --baseline {fresh}`"), err


def test_a_named_run_an_older_crapkit_measured_needs_a_coverage_run_first(repo, capsys):
    pinned(repo)
    older = write_run(repo, [twin(1), twin(2)],
                      versions={"analysis_version": "7", "lizard": lizard.version})
    stale_marks(repo)
    capsys.readouterr()

    code, _, err = verify(repo, capsys, "--baseline", str(older))

    assert code == 3
    assert err.strip().endswith(
        f"; run {older} was measured under another metric too, so run `{_self()} coverage`, "
        f"then re-baseline from the run it writes with `{_self()} ratchet seed --baseline ID`"), err


def test_a_named_run_that_cannot_serve_is_refused_for_itself_not_for_the_stamp(repo, capsys):
    _, failed, _ = pinned(repo)
    stale_marks(repo)
    capsys.readouterr()

    code, _, err = verify(repo, capsys, "--baseline", str(failed))

    assert code == 1
    assert f"run {failed} is a failed verify and cannot serve as a baseline" in err, err
    assert "were recorded under" not in err, err


def test_the_taint_warning_prints_before_the_stamp_refusal(repo, capsys):
    """Without a name, the warning above the refusal carries the run to seed
    from, and the refusal says a plain `ratchet seed` is pinned: the refusal
    itself names no run id, since the Action quotes it where this store is not."""
    _, failed, fresh = pinned(repo)
    stale_marks(repo)
    capsys.readouterr()

    code, _, err = verify(repo, capsys)

    assert code == 3
    warning = f"warning: run {fresh} is not the baseline: verify run {failed} FAILED"
    assert warning in err and f"pass `--baseline {fresh}`" in err, err
    assert err.index(warning) < err.index("were recorded under"), err
    assert err.strip().endswith("pins a plain seed to an older run, and seed's line then "
                                "names the newer run to read instead"), err


def test_the_ratchet_page_prints_the_refusal_a_named_baseline_gets(monkeypatch):
    """The page's session is `$ crapkit verify --baseline 12` on a crapkit that
    runs Python at analysis 10 over marks a single number 9 stamped, and run 12
    recorded the running stamp, so the quoted line is the one that process prints."""
    monkeypatch.setattr(invocation, "_runs_here", lambda found: True)
    src = {"analysis": {"python": 10}, "coverage": "cc-only"}
    running = ratchet.ScopeStamps("1.24.0", {"src": ratchet.ScopeStamp(**src)})
    named = {"id": 12, "tool_versions": {"lizard": "1.24.0", "scope_stamps": {"src": src}}}
    conflict = ratchet.stamp_conflict(ratchet.SingleNumberStamp(9, "1.24.0"), running, frozenset({"src"}))

    refusal = _stamp_refusal(conflict, "crapkit-ratchet.tsv", named)

    page = (ROOT / "docs" / "ratchet.md").read_text(encoding="utf-8")
    assert refusal.endswith("re-baseline from run 12 with `crapkit ratchet seed --scope src --baseline 12`")
    assert f"crapkit: {refusal}" in page


# --- verify names the scopes that moved -------------------------------------------

TWO_SCOPES = """[crapkit]
target = 6

[[scope]]
name = "api"
paths = ["api"]
languages = ["python"]
coverage_optional = true

[[scope]]
name = "web"
paths = ["web"]
languages = ["typescript"]

[[lane]]
name = "ui"
command = "python -c pass"
artifact = "coverage/ui.json"
parser = "istanbul"
scopes = ["web"]
"""
API_PY = "def pick(x):\n    if x:\n        return 1\n    return 0\n"
MARKED = [ratchet.RatchetEntry("api/app.py", "pick( x )", 40.0),
          ratchet.RatchetEntry("web/ui.ts", "render( n : number )", 30.0)]


def _two_scopes(repo, toml: str = TWO_SCOPES):
    from cli_inproc_repo import commit_all, seed_artifacts

    (repo / "api").mkdir()
    (repo / "api" / "app.py").write_text(API_PY, encoding="utf-8")
    (repo / "crapkit.toml").write_text(toml, encoding="utf-8")
    commit_all(repo, "two scopes")
    seed_artifacts(repo, unit=False)
    from crapkit.config import load_config_text

    return load_config_text(toml, root=repo)


def _stamped(repo, stamp, entries=MARKED) -> None:
    text = ratchet.dump_ratchet(entries, stamp=stamp, key_version=1, file_name="crapkit-ratchet.tsv")
    (repo / "crapkit-ratchet.tsv").write_text(text, encoding="utf-8", newline="\n")


def _coverage(repo, capsys) -> int:
    from pinned_store import store_of

    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0, capsys.readouterr().err
    capsys.readouterr()
    store = store_of(repo)
    try:
        return store.list_runs()[-1]["id"]
    finally:
        store._conn.close()


def test_a_raised_typescript_number_names_web_and_not_api_before_any_lane_runs(repo, capsys, monkeypatch):
    from crapkit.analyze import ANALYSIS_VERSIONS

    monkeypatch.setattr(invocation, "_runs_here", lambda found: True)
    cfg = _two_scopes(repo)
    _stamped(repo, ratchet.running_stamp(cfg))
    monkeypatch.setitem(ANALYSIS_VERSIONS, "typescript", ANALYSIS_VERSIONS["typescript"] + 1)
    run_id = _coverage(repo, capsys)

    code, _, err = verify(repo, capsys)
    named_code, _, named = verify(repo, capsys, "--baseline", str(run_id))

    assert code == 3 and named_code == 3, err
    old, new = ANALYSIS_VERSIONS["typescript"] - 1, ANALYSIS_VERSIONS["typescript"]
    assert (f"crapkit: ratchet marks were recorded under [crapkit-stamp=2 lizard={lizard.version}] in "
            f"crapkit-ratchet.tsv, and the rules have moved since in 1 scope(s): web (typescript analysis {old} to {new}) - ") in err, err
    assert "api (" not in err and err.strip().endswith("`crapkit ratchet seed --scope web`"), err
    assert err.isascii() and "Traceback" not in err, err
    assert named.strip().endswith(
        f"re-baseline from run {run_id} with `crapkit ratchet seed --scope web --baseline {run_id}`"), named


def test_a_python_only_repo_passes_verify_when_go_and_istanbul_move(repo, capsys, monkeypatch):
    """A raise in a language or a reader the scope does not use leaves its
    marks comparable."""
    from crapkit.analyze import ANALYSIS_VERSIONS
    from crapkit.coverage_format import READER_VERSIONS

    toml = TWO_SCOPES.split("[[scope]]\nname = \"web\"")[0]
    cfg = _two_scopes(repo, toml)
    _stamped(repo, ratchet.running_stamp(cfg), MARKED[:1])
    monkeypatch.setitem(ANALYSIS_VERSIONS, "go", ANALYSIS_VERSIONS["go"] + 1)
    monkeypatch.setitem(READER_VERSIONS, "istanbul", READER_VERSIONS["istanbul"] + 1)
    _coverage(repo, capsys)

    code, out, err = verify(repo, capsys)

    assert code == 0, err
    assert "were recorded under" not in err and "records one analysis number" not in err, err
    assert ratchet.read_stamp((repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8")) == (
        ratchet.running_stamp(cfg))


def test_a_single_number_file_warns_once_naming_the_scopes_whose_mode_it_inferred(repo, capsys):
    _two_scopes(repo)
    _stamped(repo, ratchet.MetricStamp.parse(f"crapkit-analysis=13 lizard={lizard.version}"))
    _coverage(repo, capsys)

    code, _, err = verify(repo, capsys, "--no-tighten")

    assert code == 0, err
    assert err.count("records one analysis number and no coverage mode") == 1, err
    assert ("warning: crapkit-ratchet.tsv records one analysis number and no coverage mode, so verify "
            "read scope(s) web as measured by the coverage readers 0.8.x read for their lanes; "
            "nothing to do") in err, err


def test_a_per_scope_file_prints_no_inferred_mode_warning(repo, capsys):
    cfg = _two_scopes(repo)
    _stamped(repo, ratchet.running_stamp(cfg))
    _coverage(repo, capsys)

    code, _, err = verify(repo, capsys, "--no-tighten")

    assert code == 0 and "records one analysis number" not in err, err
