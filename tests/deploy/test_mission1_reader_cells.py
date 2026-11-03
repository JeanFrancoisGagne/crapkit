"""Mission 1's readers under every harness: an lcov lane gives a Rust repo coverage-based CRAP.

The lcov cells (row `m1-readers-03`) measure a Rust crate whose lane copies a
committed tracefile into place: the crate and the tracefile cargo-llvm-cov
0.9.1 wrote for it, from tests/fixtures/coverage/lcov. Each runs fresh and
upgrade under each harness tools/deploy/pins.toml pins, in that harness's
image, and asks crapkit's MCP server what an agent in that harness's shell
gets: get_next_item and list_worklist name `untouched` with cov 0 and the
add-tests remedy. The row's nightly = "core" keeps the 3 core-image harnesses
nightly, and the release candidate runs all 17 (MAP.toml [every_harness]).

Fresh: the candidate installed as the README's 60-second start installs it.
Upgrade: crapkit N-1 measures the crate cc-only (it reads no lcov), the
guide's pip row upgrades it, and the config gains the lane.

Cell `lin-up-0.8.1-cc-only-rust` (no harness): 0.8.1 seeds a cc-only Rust
scope, the guide's pip row upgrades it, and verify passes with no conflict,
since a new reader moves no existing score. Once the config adds the lcov lane,
verify refuses with the one flip conflict naming the scope and its reseed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from kit import profiles, state, wheels
from kit.cells import cell
from kit.mcp_client import McpClient
from kit.state import output

PACKET = "m1-readers-03"
ROW = "m1-readers-03"
HARNESSES = profiles.pins()["harness"]
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "coverage" / "lcov"
PIP = "pip in the active environment"
USE_CASES = "coverage, lcov lane, list_worklist, get_next_item"
OLD = state.source_version("0.8.1")

SCOPE = '[crapkit]\ntarget = 5\n\n[[scope]]\nname = "rust"\npaths = ["rust"]\nlanguages = ["rust"]\n{optional}\n'
LANE = ('[exclude]\nglobs = ["make_lcov.py"]\n\n'
        '[[lane]]\nname = "rust"\ncommand = "python make_lcov.py"\n'
        'artifact = ".crapkit/cov/lcov.info"\nparser = "lcov"\nscopes = ["rust"]\n')
CC_ONLY = SCOPE.format(optional="coverage_optional = true\n")
MEASURED = SCOPE.format(optional="") + LANE
# The lane's command: the committed tracefile copied to the artifact path.
MAKE_LCOV = ("from pathlib import Path\n"
             "Path('.crapkit/cov').mkdir(parents=True, exist_ok=True)\n"
             "Path('.crapkit/cov/lcov.info').write_bytes(Path('tracefile.lcov').read_bytes())\n")
# A ccn-8 function: over the ceiling cc-only, so a 0.8.1 seed marks it.
ROUTE = "pub fn route(code: i32) -> i32 {\n" + "".join(
    f"    if code == {n} {{\n        return {n};\n    }}\n" for n in range(1, 8)) + "    0\n}\n"


def _marks(decorator) -> list:
    """The marks a decorator puts on a test. A lambda would not do: pytest
    reads one as a mark argument, not as the test."""
    def test():
        pass
    return decorator(test).pytestmark


def _harness_cells(prefix: str, scenario: str) -> list:
    """One parameter per pinned harness, each marked by @cell as a cell of the
    lcov row, in the image pins.toml runs that harness in."""
    return [pytest.param(key, id=key, marks=_marks(cell(
        f"{prefix}-{key}", channel="pip venv", harness=key, scenario=scenario, use_cases=USE_CASES,
        os="linux", image=spec["image"], cadence="nightly", row=ROW, packet=PACKET)))
        for key, spec in sorted(HARNESSES.items())]


def _crate(box, config: str, extra: dict | None = None) -> Path:
    """The fixture crate under rust/, its committed tracefile and `config`,
    in a git repo."""
    repo = box.root / "repo"
    files = {f"rust/{name}": (FIXTURE / "rust" / name).read_text(encoding="utf-8")
             for name in ("Cargo.toml", "drive.rs", "shapes.rs")}
    files.update({"tracefile.lcov": (FIXTURE / "cargo-llvm-cov-0.9.1.lcov").read_text(encoding="utf-8"),
                  "make_lcov.py": MAKE_LCOV, ".gitignore": ".crapkit/\n", **(extra or {})})
    (repo / "rust").mkdir(parents=True)
    state.write(repo, files)
    _configure(repo, config)
    box.run(["git", "init", "-q", "-b", "main"], cwd=repo, expect=0)
    profiles.commit(box, repo, "a rust crate")
    return repo


def _configure(repo: Path, config: str) -> None:
    """crapkit.toml as `config`, a lane keyed for the container it runs in."""
    state.write(repo, {"crapkit.toml": config})
    if profiles.in_container():
        profiles.allow_container_lane(repo)


def _in_the_harness_shell(box, key: str) -> None:
    """The pinned harness CLIs on PATH, as their user has them; a harness with
    a command of its own must resolve to it."""
    profiles.add_harnesses(box)
    command = HARNESSES[key].get("command")
    assert command is None or box.which(command), f"{key}: {command} is not on PATH: {box.env['PATH']}"


def assert_the_agent_gets_cov_and_add_tests(box, repo: Path) -> None:
    """crapkit coverage from the shell exits 0, and over MCP stdio
    get_next_item and list_worklist name `untouched` at cov 0, add-tests."""
    box.script("crapkit coverage", shell="sh", cwd=repo, expect=0)
    with McpClient.in_box(box, ["crapkit", "mcp"], cwd=repo) as client:
        client.initialize()
        item = client.call("get_next_item", {})
        worklist = client.call("list_worklist", {"top": 50})

    assert not item["isError"] and not worklist["isError"], (item, worklist)
    picked = json.loads(item["content"][0]["text"])["item"]
    rows = {row["handle"]: row for row in json.loads(worklist["content"][0]["text"])["active"]}
    for row in (picked, rows["untouched"]):
        assert (row["path"], row["handle"], row["cov"], row["remedy"]) == (
            "rust/shapes.rs", "untouched", 0.0, "add-tests"), box.transcript.text()


@pytest.mark.parametrize("key", _harness_cells(
    "lin-lcov", "fresh: README pip start; a Rust crate's lcov lane (cargo-llvm-cov 0.9.1 tracefile) measures "
                "it, and get_next_item and list_worklist show cov 0 and add-tests on untouched"))
def test_lcov_lane_fresh(box, templates, key):
    profiles.session_crapkit(box, templates)
    repo = _crate(box, MEASURED)
    _in_the_harness_shell(box, key)
    assert_the_agent_gets_cov_and_add_tests(box, repo)


@pytest.mark.parametrize("key", _harness_cells(
    "lin-up-lcov", "upgrade from N-1 by the guide's pip row: N-1 scores the crate cc-only, the config gains "
                   "an lcov lane, and get_next_item and list_worklist show cov 0 and add-tests"))
def test_lcov_lane_upgrade(box, candidate, record_property, key):
    n1 = wheels.n_minus_1()
    record_property("n_minus_1", n1)
    state.pip_venv(box, "3.12")
    state.pip_install(box, f"crapkit=={n1}")
    repo = _crate(box, CC_ONLY)
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    state.upgrade_to(box, repo, candidate, state.upgrade_line(PIP))
    _configure(repo, MEASURED)
    profiles.commit(box, repo, "the rust scope gains an lcov lane")
    _in_the_harness_shell(box, key)
    assert_the_agent_gets_cov_and_add_tests(box, repo)


@cell("lin-up-0.8.1-cc-only-rust", channel="pip venv", harness="none",
      scenario="upgrade from 0.8.1 by the guide's pip row: a seeded cc-only Rust scope verifies with no "
               "conflict; once the config adds an lcov lane, verify names the flip once, exit 3",
      use_cases="upgrade guide, ratchet lifecycle, lcov lane", os="linux", image="core", cadence="nightly")
def test_lin_up_0_8_1_cc_only_rust(box, candidate):
    state.pip_venv(box, "3.12")
    state.pip_install(box, f"crapkit=={OLD}")
    repo = _crate(box, CC_ONLY, {"rust/route.rs": ROUTE})
    for step in (["coverage"], ["ratchet", "seed"]):
        box.run(["crapkit", *step], cwd=repo, expect=0)
    state.commit(box, repo, "seeded cc-only under 0.8.1")

    state.upgrade_to(box, repo, candidate, state.upgrade_line(PIP))
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    state.verify(box, repo)

    _configure(repo, MEASURED)
    state.commit(box, repo, "the rust scope gains an lcov lane")
    box.run(["crapkit", "coverage"], cwd=repo, expect=0)
    refusal = output(box.run(["crapkit", "verify"], cwd=repo, expect=3))

    assert "rust was cc-only and is now measured by its lcov lane(s)" in refusal, box.transcript.text()
    assert refusal.count("ratchet seed --scope rust") == 1, box.transcript.text()
