"""tools/release/release.py: one table of version surfaces, and the chain that
bumps, publishes and verifies them.

Eight releases were re-scripted by hand in a scratchpad, and the surfaces
drifted once (PyPI served a version README did not name). The table lives in
one place now, the bump refuses a tree whose surfaces disagree, and `verify`
reads every surface through its live API rather than a cached page.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools" / "release"))

release = pytest.importorskip("release")

DASH = chr(0x2014)
NL = chr(10)


def _tree(tmp_path: Path, version: str = "0.5.1", heading: str = "0.5.2") -> Path:
    """A repo copy carrying every surface at `version`, with the next
    changelog heading still unreleased."""
    root = tmp_path / "repo"
    (root / "src" / "crapkit").mkdir(parents=True)
    (root / "plugin" / ".claude-plugin").mkdir(parents=True)
    (root / "pyproject.toml").write_text(f'[project]{NL}name = "crapkit"{NL}version = "{version}"{NL}',
                                         encoding="utf-8")
    (root / "src" / "crapkit" / "__init__.py").write_text(f'__version__ = "{version}"{NL}',
                                                          encoding="utf-8")
    (root / "README.md").write_text(
        f"# crapkit{NL}{NL}```{NL}$ crapkit --version{NL}crapkit {version}{NL}```{NL}{NL}"
        f"    rev: v{version}{NL}{NL}uses: JeanFrancoisGagne/crapkit@v{version}{NL}"
        f"uses: JeanFrancoisGagne/crapkit@v{version}{NL}", encoding="utf-8")
    (root / "plugin" / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "crapkit", "version": version}, indent=2) + NL, encoding="utf-8")
    (root / "server.json").write_text(
        json.dumps({"name": "io.github.JeanFrancoisGagne/crapkit", "version": version,
                    "packages": [{"identifier": "crapkit", "version": version}]}, indent=2) + NL,
        encoding="utf-8")
    (root / "CHANGELOG.md").write_text(
        f"# Changelog{NL}{NL}## {heading} {DASH} unreleased{NL}{NL}### One thing{NL}{NL}Text.{NL}{NL}"
        f"## {version} {DASH} 2026-09-05{NL}{NL}Older.{NL}", encoding="utf-8")
    return root


# --- the surface table ----------------------------------------------------------

def test_the_table_names_every_surface_a_release_touches():
    files = {s.path for s in release.SURFACES}
    assert files == {"pyproject.toml", "src/crapkit/__init__.py", "README.md",
                     "plugin/.claude-plugin/plugin.json", "server.json"}


def test_check_passes_on_a_tree_whose_surfaces_agree(tmp_path):
    root = _tree(tmp_path)

    report = release.check(root, "0.5.2")

    assert report.current == "0.5.1"
    assert report.problems == []


def test_check_names_the_surface_whose_count_is_off(tmp_path):
    root = _tree(tmp_path)
    (root / "server.json").write_text('{"version": "0.5.1"}' + NL, encoding="utf-8")

    report = release.check(root, "0.5.2")

    assert any("server.json" in p and "x1" in p and "expected 2" in p for p in report.problems), report.problems


def test_check_refuses_a_version_that_does_not_move_forward(tmp_path):
    root = _tree(tmp_path)

    report = release.check(root, "0.5.1")

    assert any("0.5.1" in p and "not after" in p for p in report.problems), report.problems


def test_check_refuses_a_changelog_with_no_unreleased_heading_for_the_version(tmp_path):
    root = _tree(tmp_path, heading="0.6.0")

    report = release.check(root, "0.5.2")

    assert any("CHANGELOG.md" in p and "0.5.2" in p for p in report.problems), report.problems


# --- bump ----------------------------------------------------------------------

def test_bump_rewrites_every_surface_and_dates_the_heading(tmp_path):
    root = _tree(tmp_path)

    changed = release.bump(root, "0.5.2", date="2026-09-06")

    assert sorted(changed) == sorted({s.path for s in release.SURFACES} | {"CHANGELOG.md"})
    surfaces = [p for p in release.check(root, "0.5.3", current="0.5.2").problems
                if not p.startswith("CHANGELOG.md")]
    assert surfaces == [], "every surface now reads 0.5.2 the exact number of times"
    assert f"## 0.5.2 {DASH} 2026-09-06" in (root / "CHANGELOG.md").read_text(encoding="utf-8")
    assert (root / "README.md").read_text(encoding="utf-8").count("crapkit@v0.5.2") == 2


def test_bump_refuses_a_tree_check_would_refuse(tmp_path):
    root = _tree(tmp_path)
    (root / "server.json").write_text('{"version": "0.5.1"}' + NL, encoding="utf-8")

    with pytest.raises(release.ReleaseError) as caught:
        release.bump(root, "0.5.2", date="2026-09-06")

    assert "server.json" in str(caught.value)
    assert (root / "pyproject.toml").read_text(encoding="utf-8").count("0.5.1") == 1, "nothing written"


# --- notes ---------------------------------------------------------------------

def test_notes_is_the_changelog_section_between_two_headings(tmp_path):
    root = _tree(tmp_path)
    release.bump(root, "0.5.2", date="2026-09-06")

    notes = release.notes(root, "0.5.2")

    assert notes.startswith("### One thing")
    assert "Older." not in notes and "## 0.5.1" not in notes


# --- verify: every surface through its live API ---------------------------------

def _fetch(answers: dict):
    """A fetcher that answers by URL prefix and records what was asked."""
    asked = []

    def fetch(url: str) -> str:
        asked.append(url)
        for prefix, body in answers.items():
            if url.startswith(prefix):
                return body
        raise release.ReleaseError(f"no answer for {url}")

    fetch.asked = asked
    return fetch


def _live(version: str, *, latest: str | None = None) -> dict:
    latest = latest or version
    return {
        "https://pypi.org/pypi/crapkit/": json.dumps({"info": {"version": version}, "urls": [{}, {}]}),
        "https://registry.modelcontextprotocol.io/": json.dumps({"servers": [
            {"server": {"version": latest, "repository": {"url": "https://github.com/JeanFrancoisGagne/crapkit"}},
             "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": True}}}]}),
    }


def test_verify_reads_every_surface_and_passes_when_they_agree(tmp_path):
    root = _tree(tmp_path)
    release.bump(root, "0.5.2", date="2026-09-06")

    rows = release.verify(root, "0.5.2", fetch=_fetch(_live("0.5.2")),
                          git_tag=lambda: "v0.5.2", gh_release=lambda v: f"https://github.com/x/releases/tag/v{v}")

    assert {r.surface for r in rows} >= {"git tag", "PyPI", "GitHub release", "registry", "plugin.json",
                                         "server.json", "README"}
    assert all(r.ok for r in rows), [r for r in rows if not r.ok]


def test_verify_flags_the_one_surface_that_serves_another_version(tmp_path):
    """The drift that motivated the tool: PyPI answered a version README did not name."""
    root = _tree(tmp_path)
    release.bump(root, "0.5.2", date="2026-09-06")

    rows = release.verify(root, "0.5.2", fetch=_fetch(_live("0.5.1", latest="0.5.2")),
                          git_tag=lambda: "v0.5.2", gh_release=lambda v: f"https://github.com/x/releases/tag/v{v}")

    bad = [r for r in rows if not r.ok]
    assert [r.surface for r in bad] == ["PyPI"]
    assert bad[0].observed == "0.5.1" and bad[0].expected == "0.5.2"


def test_verify_asks_the_version_specific_pypi_endpoint_not_the_cached_project_page(tmp_path):
    root = _tree(tmp_path)
    release.bump(root, "0.5.2", date="2026-09-06")
    fetch = _fetch(_live("0.5.2"))

    release.verify(root, "0.5.2", fetch=fetch, git_tag=lambda: "v0.5.2", gh_release=lambda v: f"v{v}")

    assert "https://pypi.org/pypi/crapkit/0.5.2/json" in fetch.asked


# --- plan: the chain as a list a person can read before running it -------------

def test_the_plan_orders_the_chain_the_way_the_contracts_require():
    steps = release.plan("0.5.2")
    names = [s.name for s in steps]

    assert names.index("tag") < names.index("contracts"), "the README rev contract reads the newest tag"
    assert names.index("contracts") < names.index("verify")
    assert names.index("verify") < names.index("push"), "nothing leaves the machine before verify is OK"
    assert names.index("push") < names.index("pypi") < names.index("github release")
    assert names.index("github release") < names.index("registry")


def test_the_verify_step_is_marked_as_its_own_background_command():
    verify_step = next(s for s in release.plan("0.5.2") if s.name == "verify")

    assert verify_step.background, "a foreground tool call dies at 600 s and verify takes longer"


def test_the_version_line_is_asked_never_inferred(capsys):
    """The tool bumps the version it is given; it has no notion of what the next
    number should be, because that is a call on visible behaviour change."""
    code = release.main(["plan"])

    assert code == 2
    assert "version" in capsys.readouterr().err


# --- the CLI ---------------------------------------------------------------------

def test_the_cli_check_reports_and_exits_nonzero_on_a_bad_tree(tmp_path, capsys):
    root = _tree(tmp_path)
    (root / "server.json").write_text('{"version": "0.5.1"}' + NL, encoding="utf-8")

    code = release.main(["check", "0.5.2", "--repo", str(root)])

    assert code == 1
    assert "server.json" in capsys.readouterr().out


def test_the_cli_dry_run_prints_every_command_and_runs_none(tmp_path, capsys):
    root = _tree(tmp_path)

    code = release.main(["run", "stage2b", "0.5.2", "--repo", str(root), "--dry-run"])
    out = capsys.readouterr().out

    assert code == 0
    assert "git push" in out and "twine upload" in out and "gh release create v0.5.2" in out
    assert (root / "CHANGELOG.md").read_text(encoding="utf-8").count("unreleased") == 1, "dry run wrote nothing"
