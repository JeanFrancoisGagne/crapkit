r"""doctor names two path problems no run reports.

An [exclude] glob that matches no tracked file excludes nothing, and before
0ae9ade a glob written `web\dist\**`, `./web/dist/**` or `web/dist/` was one: the
generated files stayed scored while doctor printed `no problems found`. crapkit
now reads those spellings as git's, and doctor WARNs on each glob that still
matches nothing, quoting it as the file holds it. init's default globs are left
out: they guard trees a repo may never have (node_modules/, dist/), and a WARN on
each one would teach every reader to skip the line.

A tracked POSIX file whose name holds `\` is unsupported: crapkit.toml, coverage
reports and JUnit ids read `\` as a directory separator on every OS, so
such a file cannot be measured. doctor names each one.
"""
from __future__ import annotations

import json

import pytest

from crapkit.config import load_config_text
from crapkit.doctor import Finding, backslash_names, unmatched_globs
from crapkit.scaffold import DEFAULT_EXCLUDES

TRACKED = ["web/src/a.js", "web/dist/a.js", "backend/pkg/mod.py", "README.md"]


def _globs(*written: str) -> tuple[tuple[str, str], ...]:
    """Each glob as written, paired with the glob the loader reads."""
    cfg = load_config_text("[[scope]]\nname = 'web'\npaths = ['web']\nlanguages = ['javascript']\n"
                           f"[exclude]\nglobs = {json.dumps(list(written))}\n")
    return tuple(zip(written, cfg.exclude_globs))


@pytest.mark.parametrize("glob", ["web/dist/**", "web\\dist\\**", "web/dist\\**", "./web/dist/**",
                                  ".\\web\\dist\\**", "/web/dist/**", "web/dist/", "WEB/DIST/**",
                                  "**/dist/**", "**\\dist\\**", "web\\dist\\*.js", "**/*.md"])
def test_a_glob_that_matches_a_tracked_file_in_any_spelling_says_nothing(glob):
    assert unmatched_globs(_globs(glob), TRACKED) == ()


@pytest.mark.parametrize("glob, read", [
    ("web/dsit/**", "web/dsit/**"),
    ("web\\dsit\\**", "web/dsit/**"),
    ("./gen/", "gen/**"),
    ("**/*.generated.ts", "**/*.generated.ts"),
])
def test_a_glob_that_matches_no_tracked_file_is_warned_about_as_written(glob, read):
    (finding,) = unmatched_globs(_globs(glob), TRACKED)

    assert finding.level == "WARN"
    assert finding.text.startswith(f"[exclude] glob {glob!r} matches no tracked file"), finding
    assert "excludes nothing" in finding.text and "fix the path or delete the glob" in finding.text
    assert (f"(read as {read!r})" in finding.text) is (glob != read), finding


def test_each_glob_that_matches_nothing_gets_its_own_line():
    findings = unmatched_globs(_globs("web/dist/**", "nope/**", "gone/"), TRACKED)

    assert [f.text.split(" matches")[0] for f in findings] == [
        "[exclude] glob 'nope/**'", "[exclude] glob 'gone/'"]


def test_no_globs_give_no_line_and_an_empty_tree_matches_no_glob():
    assert unmatched_globs((), TRACKED) == ()
    assert len(unmatched_globs(_globs("web/**"), [])) == 1


def test_the_defaults_init_writes_are_globs_doctor_leaves_alone():
    """The default set is written into every init'd config. `**/node_modules/**`
    matching nothing in a repo that tracks no node_modules is the default doing
    its job, not a typo, so the wiring passes these through untouched."""
    from crapkit.cli.admin import _written_globs

    raw = {"exclude": {"globs": [*DEFAULT_EXCLUDES, "nope/**"]}}
    cfg = load_config_text("[[scope]]\nname = 'web'\npaths = ['web']\nlanguages = ['javascript']\n"
                           f"[exclude]\nglobs = {json.dumps([*DEFAULT_EXCLUDES, 'nope/**'])}\n")

    assert _written_globs(raw, cfg) == (("nope/**", "nope/**"),)


# --- tracked names holding a backslash -------------------------------------------

def test_a_tracked_name_holding_a_backslash_is_named_as_unsupported():
    (finding,) = backslash_names(["src/pkg/we\\ird.py", "src/pkg/ok.py", "docs/a\\b.md"])

    assert finding == Finding("WARN", (
        "2 tracked file(s) hold \\ in their name, which crapkit does not support: "
        "crapkit.toml, coverage reports and JUnit ids read \\ as a directory separator, "
        "so such a file cannot be measured: src/pkg/we\\ird.py, docs/a\\b.md; "
        "rename each without the \\"))


def test_a_tree_with_no_such_name_says_nothing():
    assert backslash_names(["src/a.py", "web/b.ts"]) == ()
