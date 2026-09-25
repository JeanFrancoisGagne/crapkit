"""Upgrades through the tool installers: pipx, uv tool and uvx.

A tool install keeps crapkit out of the repo's own environment, so each cell
puts the repo's test environment (pytest, pytest-cov) on PATH beside it, the
way such a user runs the lane init wrote. The old release goes in the way a
user put it there the day it was current: an unpinned `pipx install crapkit`
or `uv tool install crapkit` against an index that held nothing newer
(state.era_links), since both tools record a pinned spec and then refuse to
upgrade past it.

The upgrade command comes from docs/upgrading.md's table. Where the table has
no row for a channel, the cell runs the tool's own upgrade command, finishes
every other check, and raises the gap last (state.Gaps), so its strict xfail
holds the doc gap alone.
"""
from __future__ import annotations

import re

import pytest

from kit import docsnip, pyindex, repos, state, wheels
from kit.cells import cell
from kit.mcp_client import McpClient
from kit.state import output

PACKET = "deploy-upgrade"
NO_PIPX_ROW = pytest.mark.xfail(
    strict=True, raises=state.GuideGap,
    reason="deploy-bug deploy-upgrade-2 (doc gap): docs/upgrading.md gives no pipx upgrade line")
NO_UVX_ROW = pytest.mark.xfail(
    strict=True, raises=state.GuideGap,
    reason="deploy-bug deploy-upgrade-3 (doc gap): docs/upgrading.md never says a cached `uvx crapkit` keeps "
           "running the release it first fetched, nor names `uvx crapkit@latest`")
SCOPE_COUNT = re.compile(r"scope '([^']+)': (\d+) files?")
MUTATION_KEYS = 'mutation_command = "python -m pytest -q -x -p no:cacheprovider"\nmutation_workers = 2\n'


def crapkit_version(box, argv=("crapkit",)) -> str:
    return box.run([*argv, "--version"], expect=0).stdout.split()[-1]


# --- pipx, from 0.4.15 ----------------------------------------------------------------

def scope_counts(step) -> dict[str, int]:
    return {name: int(count) for name, count in SCOPE_COUNT.findall(output(step))}


def worktrees(box, repo) -> int:
    listed = box.run(["git", "worktree", "list", "--porcelain"], cwd=repo, expect=0).stdout
    return sum(1 for line in listed.splitlines() if line.startswith("worktree "))


def mutation_pool(box, repo) -> None:
    """A 0.4.15 user turns on parallel mutation and runs it once: the pool of
    worktrees stays behind under .crapkit/mutate-pool/."""
    state.rewrite(repo / "crapkit.toml", lambda text: text.replace(state.MAIN, state.MAIN + MUTATION_KEYS, 1))
    box.transcript.note("user edit: mutation_command and mutation_workers = 2 under 0.4.15")
    state.commit(box, repo, "run mutants two at a time")
    box.run(["crapkit", "mutate", "--files", "calc/grade.py", "--max-mutants", "3"], cwd=repo)
    assert worktrees(box, repo) == 3, "0.4.15's mutate left no worktree pool behind"


def walked_up(box, repo) -> None:
    """From a directory below the root, the candidate finds the root's config."""
    step = box.run(["crapkit", "worklist"], cwd=repo / "calc", expect=0, note="walk-up: worklist from calc/")
    assert f"crapkit: using crapkit.toml at {repo}" in step.stderr, box.transcript.text()


@NO_PIPX_ROW
@cell("lin-up-pipx-0.4.15", channel="pipx", harness="none",
      scenario="upgrade with `pipx upgrade crapkit`: walk-up config, `**/` counts, mutate-pool reclaimed",
      use_cases="upgrade guide, mutate, clean", os="linux", image="core", cadence="nightly")
def test_lin_up_pipx_0_4_15(box, templates, candidate):
    gaps = state.Gaps()
    source = state.build(box, state.source_version("0.4.15"), cache=templates)
    repo = source.checkout(box)
    state.suite_venv(box)
    state.pipx_install(box, source.version)
    assert crapkit_version(box) == source.version
    mutation_pool(box, repo)
    counts = scope_counts(box.run(["crapkit", "doctor"], cwd=repo))

    def measured(box, repo, doctor):
        # The `**/` exclude change predates 0.4.15: measured, 0.4.0, 0.4.4, 0.4.15 and
        # the candidate count a root dist/ alike, so the per-scope counts must not move.
        assert scope_counts(doctor) == counts, (counts, scope_counts(doctor))
        walked_up(box, repo)

    state.walk(box, repo, candidate, source, gaps.command("pipx upgrade", "pipx upgrade crapkit"), measured=measured)
    state.run_line(box, repo, state.guide_span("crapkit mutate --drop-pool"))
    assert worktrees(box, repo) == 1, "mutate --drop-pool left pooled worktrees registered"
    assert not (repo / ".crapkit" / "mutate-pool").exists()
    gaps.raise_any()


# --- uv tool, from 0.6.0 ----------------------------------------------------------------

ROUTES_JS = '''export const routes = [1, 2, 3].map(function (n) {
  if (n > 1 && n < 3) { return "mid"; }
  if (n === 1 || n === 7) { return "low"; }
  if (n > 5 && n % 2) { return "odd"; }
  if (n === 3) { return "top"; }
  return "none";
});

export const pick = (list) => list.filter((x) => x.ok).map((x) => {
  if (x.a && x.b) { return 1; }
  if (x.c || x.d) { return 2; }
  if (x.e && x.f) { return 3; }
  if (x.g) { return 4; }
  return 0;
});
'''
WEB_SCOPE = '[[scope]]\nname = "web"\npaths = ["web"]\nlanguages = ["javascript"]\ncoverage_optional = true\n\n'


def js_callbacks(box, repo) -> None:
    """Under 0.6.0: a cc-only JavaScript scope whose anonymous callbacks are
    over the ceiling, seeded. Reader 10 later finds a callback 0.6.0 missed."""
    state.write(repo, {"web/routes.js": ROUTES_JS})
    state.rewrite(repo / "crapkit.toml", lambda text: text.replace("[exclude]", WEB_SCOPE + "[exclude]", 1))
    box.transcript.note("user edit: a cc-only web scope for web/routes.js")
    state.commit(box, repo, "web: routes with anonymous callbacks")
    for step in (["coverage"], ["ratchet", "seed"]):
        box.run(["crapkit", *step], cwd=repo, expect=0)
    state.commit(box, repo, "seed the web marks")
    assert "web/routes.js\t(anonymous)\t" in (repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8")


def duplicate_scope(box, repo) -> None:
    """A scope block pasted twice, which 0.6.0 accepts."""
    block = state.SCOPE_BLOCK.search((repo / "crapkit.toml").read_text(encoding="utf-8"))[0]
    state.rewrite(repo / "crapkit.toml", lambda text: text.replace(block, block + block, 1))
    box.transcript.note("user edit: the calc [[scope]] block pasted twice")
    box.run(["crapkit", "doctor"], cwd=repo, expect=0, note="0.6.0 accepts the duplicate scope")
    state.commit(box, repo, "a scope pasted twice")


def baseline_run(box, repo) -> int:
    return next(run["id"] for run in state.crapkit_json(box, repo, "runs")["runs"] if run["baseline"])


def _place(row: dict) -> tuple[int, int]:
    return int(row["start"]), int(row["occurrence"])


def _mark_key(rows: list[dict], raw: str, value: float) -> str:
    """The current key of the function a mark measured: same raw name, same
    score, its ordinal among that name's functions in (start, occurrence) order."""
    named = sorted((row for row in rows if row["long_name"] == raw), key=_place)
    scores = [float(row["crap"]) for row in named]
    ordinal = 1 + scores.index(value)
    return raw if ordinal == 1 else f"{raw}#{ordinal}"


def _mapped(line: str, path: str, rows: list[dict]) -> str:
    fields = line.split("\t")
    if fields[0] != path:
        return line
    return "\t".join([path, _mark_key(rows, fields[1].split("#")[0], float(fields[2])), fields[2]])


def reconcile_marks(candidate):
    """docs/ratchet.md#reconcile-saved-marks for the file the refusal names:
    each mark carried to the function it measured, found in the coverage
    export by name and score, then the current stamp and the key line."""
    def reconcile(box, repo, refusal):
        path = re.search(r"anonymous function ordinals in (\S+?);", output(refusal))[1]
        rows = [row for row in state.export_rows(state.export_path(repo)) if row["path"] == path]
        lines = state.mark_rows(repo)
        stamp = f"# {state.metric(state.analysis_version(candidate))}\n# crapkit-keys=1\n"
        body = "".join(_mapped(line, path, rows) + "\n" for line in lines)
        state.rewrite(repo / "crapkit-ratchet.tsv", lambda _text: stamp + "path\tlong_name\tcrap\n" + body)
        box.transcript.note(f"user edit: the reviewed mapping for {path}, per docs/ratchet.md#reconcile-saved-marks")
    return reconcile


@cell("lin-up-uvtool-0.6.0", channel="uv tool", harness="none",
      scenario="upgrade: reviewed-mapping refusal and fix; 0.6.0 baseline read; duplicate scope exit 3",
      use_cases="upgrade guide, claims", os="linux", image="core", cadence="nightly")
def test_lin_up_uvtool_0_6_0(box, templates, candidate):
    source = state.build(box, state.source_version("0.6.0"), cache=templates)
    repo = source.checkout(box)
    state.suite_venv(box)
    state.uv_tool_install(box, source.version)
    js_callbacks(box, repo)
    duplicate_scope(box, repo)
    baseline = baseline_run(box, repo)

    def upgraded(box, repo):
        assert baseline_run(box, repo) == baseline, "the candidate did not read 0.6.0's trusted baseline"
        refused = box.run(["crapkit", "doctor"], cwd=repo, expect=3, note="guide step: doctor, on 0.6.0's config")
        assert "duplicate scope name 'calc'; each scope needs its own name" in output(refused), box.transcript.text()
        state.resolve_doctor(box, repo, refused)
        state.commit(box, repo, "one calc scope")

    state.walk(box, repo, candidate, source, state.upgrade_line("uv tool"), upgraded=upgraded,
               reconcile=reconcile_marks(candidate))
    marks = (repo / "crapkit-ratchet.tsv").read_text(encoding="utf-8")
    assert "web/routes.js\t(anonymous)#2\t" in marks, marks


# --- uvx against an index with PyPI's cache headers ----------------------------------------

def uvx_env(box, index) -> None:
    """uv reads the local index, not find-links, for this cell."""
    for name in ("UV_OFFLINE", "UV_NO_INDEX", "UV_FIND_LINKS"):
        box.env.pop(name, None)
    box.env["UV_DEFAULT_INDEX"] = index.simple


def uvx_versions(box, repo) -> dict[str, str]:
    """What each uvx spelling runs once the release is on the index."""
    return {"uvx crapkit": crapkit_version(box, ("uvx", "crapkit")),
            "uvx crapkit mcp": state.server_info(box, repo, ["uvx", "crapkit", "mcp"])["version"],
            "uvx crapkit@latest": crapkit_version(box, ("uvx", "crapkit@latest")),
            "uvx crapkit, after @latest": crapkit_version(box, ("uvx", "crapkit"))}


def uvx_across_release(box, templates, candidate, old: str, gaps) -> dict[str, str]:
    """A repo `uvx crapkit` has served at `old`; then the candidate lands on
    an index with PyPI's cache headers. Returns what each uvx spelling runs;
    a guide that leaves the stale one unexplained is a gap."""
    source = state.build(box, old, cache=templates)
    repo = source.checkout(box)
    state.suite_venv(box)
    with pyindex.serve([state.era_links(box, old)]) as index:
        uvx_env(box, index)
        assert crapkit_version(box, ("uvx", "crapkit")) == old
        index.files[candidate.wheel.name] = candidate.wheel
        seen = uvx_versions(box, repo)
        box.transcript.attach("uvx-after-release", seen)
        state.kept(box, repo, source, launcher=("uvx", "crapkit"))

    assert seen["uvx crapkit@latest"] == candidate.version, seen
    gaps.check(seen["uvx crapkit"] == candidate.version or "uvx crapkit@latest" in state.page(),
               f"{state.GUIDE}: `uvx crapkit` kept running {seen['uvx crapkit']} after {candidate.version} was "
               f"published (the MCP entry `uvx crapkit mcp` too: {seen['uvx crapkit mcp']}); the guide never "
               "names `uvx crapkit@latest`, which fetched it")
    return seen


@NO_UVX_ROW
@cell("lin-up-uvx-n1", channel="uvx against pyindex", harness="none",
      scenario="upgrade: what `uvx crapkit --version` returns under PyPI cache headers; @latest; docs line from this run",
      use_cases="uvx refresh", os="linux", image="core", cadence="nightly")
def test_lin_up_uvx_n1(box, templates, candidate, record_property):
    n1 = wheels.n_minus_1()
    record_property("n_minus_1", n1)
    gaps = state.Gaps()
    uvx_across_release(box, templates, candidate, n1, gaps)
    gaps.raise_any()


# --- Windows: pipx, uvx and uv tool -------------------------------------------------------

def win_pipx(box, templates, candidate, gaps) -> None:
    source = state.build(box, state.source_version("0.7.6"), cache=templates)
    repo = source.checkout(box)
    state.suite_venv(box)
    state.pipx_install(box, source.version)
    state.walk(box, repo, candidate, source, gaps.command("pipx upgrade", "pipx upgrade crapkit"))


def win_uvx(box, templates, candidate, gaps) -> None:
    uvx_across_release(box, templates, candidate, state.source_version("0.7.6"), gaps)


def win_uv_tool(box, templates, candidate, gaps) -> None:
    """The launcher-lock procedure: a server holds crapkit.exe, the upgrade
    fails with error 32, the server stops, the same command runs again."""
    source = state.build(box, state.source_version("0.7.6"), cache=templates)
    repo = source.checkout(box)
    state.suite_venv(box)
    state.uv_tool_install(box, source.version)
    line = state.upgrade_line("uv tool")
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=repo) as client:
        assert client.initialize()["serverInfo"]["version"] == source.version
        client.call("get_next_item")
        locked = state.run_line(box, repo, line, expect=None, note="guide step, while a server holds crapkit.exe")
    assert locked.exit != 0 and "os error 32" in output(locked), box.transcript.text()
    state.run_line(box, repo, line, note="Windows launcher locks, step 2: rerun the same upgrade command")
    state.walk(box, repo, candidate, source, "")


WIN_CHANNELS = {"pipx": win_pipx, "uvx": win_uvx, "uv-tool": win_uv_tool}


@cell("win-pipx-uvx-uvtool", channel="pipx, uvx, uv tool", harness="cmd.exe",
      scenario="fresh + upgrade from 0.7.6; uv tool upgrade while a server holds the exe",
      use_cases="install, launcher lock", os="windows", image=None, cadence="nightly")
@pytest.mark.parametrize("channel", [pytest.param("pipx", marks=NO_PIPX_ROW), pytest.param("uvx", marks=NO_UVX_ROW),
                                     "uv-tool"])
def test_win_pipx_uvx_uvtool(box, templates, candidate, channel):
    gaps = state.Gaps()
    WIN_CHANNELS[channel](box, templates, candidate, gaps)
    gaps.raise_any()


def tool_install(install):
    """A tool install for a Python repo: the repo's test environment on PATH,
    then crapkit from the installer, as README Install describes."""
    def installed(box) -> None:
        state.suite_venv(box)
        install(box)
    return installed


def tool_steps(launcher: tuple[str, ...]) -> list[list[str]]:
    return [[*launcher, step] for step in ("init", "doctor", "coverage", "worklist")]


def readme_uvx_steps(_launcher: tuple[str, ...]) -> list[list[str]]:
    """README 'A repo that is not Python': uvx is the route it gives a repo
    with no Python of its own, and these are its commands as printed."""
    return [line.split() for line in docsnip.commands(docsnip.fence("README.md", "A repo that is not Python"))]


FRESH = {"pipx": (tool_install(state.pipx_install), ("crapkit",), "py-pytest", tool_steps),
         "uv-tool": (tool_install(state.uv_tool_install), ("crapkit",), "py-pytest", tool_steps),
         "uvx": (state.nothing, ("uvx", "crapkit"), "go-rust-shell", readme_uvx_steps)}


@cell("win-pipx-uvx-uvtool", channel="pipx, uvx, uv tool", harness="cmd.exe",
      scenario="fresh: the candidate through each tool installer, the README's steps for that route, one MCP session",
      use_cases="install", os="windows", image=None, cadence="nightly")
@pytest.mark.parametrize("channel", sorted(FRESH))
def test_win_fresh_tool_installs(box, templates, candidate, channel):
    install, launcher, template, steps = FRESH[channel]
    repo = repos.checkout(box, template, cache=templates)
    install(box)

    assert crapkit_version(box, launcher) == candidate.version
    for argv in steps(launcher):
        box.run(argv, cwd=repo, expect=0)
    assert state.server_info(box, repo, [*launcher, "mcp"])["version"] == candidate.version


# --- the kit's own check of the reviewed mapping -----------------------------------------

EXPORT = [{"path": "web/routes.js", "long_name": "(anonymous) ( n )", "start": "1", "occurrence": "1", "crap": "8.0"},
          {"path": "web/routes.js", "long_name": "(anonymous)", "start": "9", "occurrence": "2", "crap": "1.0"},
          {"path": "web/routes.js", "long_name": "(anonymous)", "start": "9", "occurrence": "3", "crap": "8.0"}]


@pytest.mark.kit
def test_a_mark_moves_to_the_function_with_its_name_and_score():
    assert _mapped("web/routes.js\t(anonymous)\t8.0000", "web/routes.js", EXPORT) == \
        "web/routes.js\t(anonymous)#2\t8.0000"
    assert _mapped("web/routes.js\t(anonymous) ( n )\t8.0000", "web/routes.js", EXPORT) == \
        "web/routes.js\t(anonymous) ( n )\t8.0000"
    assert _mapped("calc/a.py\tf( x )\t9.0000", "web/routes.js", EXPORT) == "calc/a.py\tf( x )\t9.0000"
