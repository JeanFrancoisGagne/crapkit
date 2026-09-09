"""tools/release/release.py: one table of version surfaces, and the chain that
bumps, publishes and verifies them.

Eight releases were re-scripted by hand in a scratchpad, and the surfaces
drifted once (PyPI served a version README did not name). The table lives in
one place now, the bump refuses a tree whose surfaces disagree, and `verify`
reads every surface through its live API rather than a cached page.
"""
import json
import os
import sys
from types import SimpleNamespace
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


COMMIT = "c0ffee1234567890"


def _live(version: str, *, latest: str | None = None) -> dict:
    latest = latest or version
    return {
        "https://api.github.com/repos/JeanFrancoisGagne/crapkit/pages/builds":
            json.dumps({"status": "built", "commit": COMMIT}),
        "https://glama.ai/": f"a page rendering the README: uses: JeanFrancoisGagne/crapkit@v{latest}",
        "https://pypi.org/pypi/crapkit/": json.dumps({"info": {"version": version}, "urls": [{}, {}]}),
        "https://registry.modelcontextprotocol.io/": json.dumps({"servers": [
            {"server": {"name": "io.github.JeanFrancoisGagne/crapkit", "version": latest,
                        "repository": {"url": "https://github.com/JeanFrancoisGagne/crapkit"},
                        "packages": [{"registryType": "pypi", "identifier": "crapkit", "version": latest}]},
             "_meta": {"io.modelcontextprotocol.registry/official": {"isLatest": True}}}]}),
    }


def test_verify_reads_every_surface_and_passes_when_they_agree(tmp_path):
    root = _tree(tmp_path)
    release.bump(root, "0.5.2", date="2026-09-06")

    rows = release.verify(root, "0.5.2", fetch=_fetch(_live("0.5.2")), tag_commit=lambda: COMMIT,
                          git_tag=lambda: "v0.5.2", gh_release=lambda v: f"https://github.com/x/releases/tag/v{v}")

    assert {r.surface for r in rows} >= {"git tag", "PyPI", "GitHub release", "registry", "plugin.json",
                                         "server.json", "README"}
    assert all(r.ok for r in rows), [r for r in rows if not r.ok]


def test_verify_flags_the_one_surface_that_serves_another_version(tmp_path):
    """The drift that motivated the tool: PyPI answered a version README did not name."""
    root = _tree(tmp_path)
    release.bump(root, "0.5.2", date="2026-09-06")

    rows = release.verify(root, "0.5.2", fetch=_fetch(_live("0.5.1", latest="0.5.2")), tag_commit=lambda: COMMIT,
                          git_tag=lambda: "v0.5.2", gh_release=lambda v: f"https://github.com/x/releases/tag/v{v}")

    bad = [r for r in rows if not r.ok]
    assert [r.surface for r in bad] == ["PyPI"]
    assert bad[0].observed == "0.5.1" and bad[0].expected == "0.5.2"


def test_verify_asks_the_version_specific_pypi_endpoint_not_the_cached_project_page(tmp_path):
    root = _tree(tmp_path)
    release.bump(root, "0.5.2", date="2026-09-06")
    fetch = _fetch(_live("0.5.2"))

    release.verify(root, "0.5.2", fetch=fetch, git_tag=lambda: "v0.5.2", tag_commit=lambda: COMMIT,
                   gh_release=lambda v: f"v{v}")

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


# --- what the 0.7.2 release cost -------------------------------------------------
#
# Every fault below fired after PyPI and the GitHub release were already public,
# because nothing proved the environment before the chain pushed.

def test_a_bare_command_name_resolves_to_a_real_executable(tmp_path):
    """Windows `CreateProcess` appends only `.exe`, so a bare `claude` never found
    the npm `claude.CMD` shim and the plugin step died with WinError 2 after PyPI
    and the GitHub release were public. `shutil.which` honours PATHEXT."""
    root = _tree(tmp_path)

    resolved = release._arguments(("git", "status"), root)

    assert Path(resolved[0]).is_absolute(), resolved
    assert resolved[1:] == ["status"]


def test_a_github_api_read_carries_the_credential_the_publish_commands_use(monkeypatch):
    """The Pages build API answers 404 to an anonymous reader, so the readback
    reported `absent` forever and the stage could never confirm a build it had
    just requested. The publishing commands next to it are authenticated `gh`."""
    monkeypatch.setattr(release, "_gh_token", lambda: "T0KEN")

    github = release._api_request("https://api.github.com/repos/x/y/pages/builds/latest")
    pypi = release._api_request("https://pypi.org/pypi/crapkit/0.5.2/json")

    assert github.get_header("Authorization") == "Bearer T0KEN"
    assert pypi.get_header("Authorization") is None, "a PyPI read must not carry a GitHub token"


def test_the_verify_step_note_names_the_stage_not_the_bare_command():
    """`plan` printed `crapkit verify`, which stamps no watermark; publication then
    refused evidence the operator had just watched pass."""
    note = next(s for s in release.plan("0.5.2") if s.name == "verify").note

    assert "run verify" in note


def test_verify_reads_the_two_surfaces_no_version_string_can_prove(tmp_path):
    """Pages serves no version anywhere, and Glama is a separate index. Both were
    silent in `verify`, so a green report meant nothing about either."""
    root = _tree(tmp_path)
    release.bump(root, "0.5.2", date="2026-09-06")

    rows = release.verify(root, "0.5.2", fetch=_fetch(_live("0.5.2")), git_tag=lambda: "v0.5.2",
                          tag_commit=lambda: COMMIT, gh_release=lambda v: f"v{v}")

    assert {r.surface for r in rows} >= {"Pages", "Glama"}
    assert all(r.ok for r in rows), [r for r in rows if not r.ok]


def test_the_github_credential_is_read_once_not_once_per_readback(monkeypatch):
    """A release makes a readback per artifact; each one spawning `gh` would be
    a process per file for a value that cannot change mid-release."""
    reads = []
    monkeypatch.setattr(release, "_GH_TOKEN", {})
    monkeypatch.setattr(release, "_read_gh_token", lambda: reads.append(1) or "T0KEN")

    assert [release._gh_token(), release._gh_token()] == ["T0KEN", "T0KEN"]
    assert len(reads) == 1


def test_a_missing_gh_leaves_the_read_anonymous_instead_of_failing(monkeypatch):
    """Reading a public surface must still work on a machine with no `gh`."""
    monkeypatch.setattr(release.shutil, "which", lambda name: None)

    assert release._read_gh_token() == ""


def test_the_credential_comes_from_gh_and_a_refusal_reads_as_none(monkeypatch):
    monkeypatch.setattr(release.shutil, "which", lambda name: "gh")
    results = iter([SimpleNamespace(returncode=0, stdout="T0KEN" + NL),
                    SimpleNamespace(returncode=1, stdout="")])
    monkeypatch.setattr(release.subprocess, "run", lambda *a, **k: next(results))

    assert release._read_gh_token() == "T0KEN"
    assert release._read_gh_token() == ""


def test_gh_failing_to_launch_leaves_the_read_anonymous(monkeypatch):
    monkeypatch.setattr(release.shutil, "which", lambda name: "gh")

    def boom(*args, **kwargs):
        raise OSError("gh is not executable here")

    monkeypatch.setattr(release.subprocess, "run", boom)

    assert release._read_gh_token() == ""


# --- preflight: what must be true before stage 1 ---------------------------------

def _owned(tmp_path, *names):
    """A purelib that carries each named tool, and a locator that reads it."""
    home = tmp_path / "site-packages"
    inside = {name: str(home / name / "__init__.py") for name in names}
    return str(home), inside.get


def test_preflight_names_each_tool_the_release_environment_does_not_own(tmp_path):
    """A thin venv resolves pytest from the base install, so crapkit's bridge has
    nothing to hand the throwaway venvs three tests build, and those tests fail
    inside the release with `No module named pytest`."""
    home, locate = _owned(tmp_path, "coverage", "build", "twine")

    problems = release.preflight(purelib=home, locate=locate, credential=lambda: True)

    assert len(problems) == 1, problems
    assert "pytest" in problems[0] and "coverage" not in problems[0]


def test_preflight_refuses_before_a_push_when_no_pypi_credential_is_reachable(tmp_path):
    """0.7.2 pushed main and the tag, then found it could not authenticate."""
    home, locate = _owned(tmp_path, *release.RELEASE_TOOLING)

    problems = release.preflight(purelib=home, locate=locate, credential=lambda: False)

    assert len(problems) == 1, problems
    assert "TWINE_PASSWORD" in problems[0]


def test_preflight_is_silent_when_the_machine_can_actually_publish(tmp_path):
    home, locate = _owned(tmp_path, *release.RELEASE_TOOLING)

    assert release.preflight(purelib=home, locate=locate, credential=lambda: True) == []


def test_check_refuses_a_machine_that_cannot_finish_the_release(tmp_path, capsys, monkeypatch):
    """`check` is stage 1's first command, so it is where the chain must stop."""
    root = _tree(tmp_path)
    monkeypatch.setattr(release, "preflight", lambda: ["no PyPI credential reachable"])

    code = release.main(["check", "0.5.2", "--repo", str(root)])

    assert code == 1
    assert "no PyPI credential reachable" in capsys.readouterr().out


def test_a_module_resolves_to_its_file_and_an_absent_one_to_nothing():
    assert release._module_origin("json").endswith("json" + os.sep + "__init__.py")
    assert release._module_origin("crapkit_no_such_module") is None


def test_the_environment_beats_keyring_and_a_missing_one_falls_through(monkeypatch):
    monkeypatch.setattr(release, "_keyring_has", lambda url: "keyring")
    monkeypatch.setenv("TWINE_USERNAME", "__token__")
    monkeypatch.setenv("TWINE_PASSWORD", "pypi-x")
    assert release._twine_credential() is True

    monkeypatch.delenv("TWINE_PASSWORD")
    assert release._twine_credential() == "keyring"


def test_keyring_absent_or_refusing_reads_as_no_credential(monkeypatch):
    monkeypatch.setitem(sys.modules, "keyring", None)
    assert release._keyring_has("https://upload.pypi.org/legacy/") is False

    class Refused(Exception):
        pass

    def refuse(url, username):
        raise Refused("locked")

    fake = SimpleNamespace(get_credential=refuse, errors=SimpleNamespace(KeyringError=Refused))
    monkeypatch.setitem(sys.modules, "keyring", fake)
    assert release._keyring_has("https://upload.pypi.org/legacy/") is False

    found = SimpleNamespace(get_credential=lambda url, username: object(),
                            errors=SimpleNamespace(KeyringError=Refused))
    monkeypatch.setitem(sys.modules, "keyring", found)
    assert release._keyring_has("https://upload.pypi.org/legacy/") is True
