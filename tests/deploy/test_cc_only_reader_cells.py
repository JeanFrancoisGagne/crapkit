"""A cc-only Rust repo learns its language has a reader: the init stanza and doctor's note.

The fresh cells (row `m1-readers-04`) run the README's pip start on a Rust repo
whose `Cargo.toml` sits at the root, under each harness tools/deploy/pins.toml
pins, in that harness's image: `crapkit init` writes the scope cc-only with the
rust lane commented, and `crapkit doctor` exits 0 and notes that parser = "lcov"
from cargo llvm-cov can measure it. The row's nightly = "core" keeps the 3
core-image harnesses nightly, and the release candidate runs all 17.

Cell `lin-up-0.8.1-cc-only-reader` (no harness): 0.8.1 initializes and seeds
the same repo, the guide's pip row upgrades it, crapkit.toml stays byte for
byte as 0.8.1 wrote it, and doctor still exits 0, now with the note.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from kit import profiles, state
from kit.cells import cell
from kit.state import output

PACKET = "m1-readers-04"
ROW = "m1-readers-04"
HARNESSES = profiles.pins()["harness"]
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "coverage" / "lcov"
PIP = "pip in the active environment"
USE_CASES = "init, doctor, cc-only scope, lcov reader note"
OLD = state.source_version("0.8.1")
STANZA = '# command = "cargo llvm-cov --lcov --output-path .crapkit/cov/rust.lcov"'
NOTE = 'a reader can measure rust through parser = "lcov" from cargo llvm-cov'


def _marks(decorator) -> list:
    """The marks a decorator puts on a test. A lambda would not do: pytest
    reads one as a mark argument, not as the test."""
    def test():
        pass
    return decorator(test).pytestmark


def _harness_cells(prefix: str, scenario: str) -> list:
    """One parameter per pinned harness, each marked by @cell as a cell of the
    row, in the image pins.toml runs that harness in."""
    return [pytest.param(key, id=key, marks=_marks(cell(
        f"{prefix}-{key}", channel="pip venv", harness=key, scenario=scenario, use_cases=USE_CASES,
        os="linux", image=spec["image"], cadence="nightly", row=ROW, packet=PACKET)))
        for key, spec in sorted(HARNESSES.items())]


def _crate(box) -> Path:
    """The fixture crate under rust/ and a workspace Cargo.toml at the root, in
    a git repo with no crapkit.toml yet."""
    repo = box.root / "repo"
    files = {f"rust/{name}": (FIXTURE / "rust" / name).read_text(encoding="utf-8")
             for name in ("Cargo.toml", "drive.rs", "shapes.rs")}
    files.update({"Cargo.toml": '[workspace]\nmembers = ["rust"]\n', ".gitignore": ".crapkit/\n"})
    (repo / "rust").mkdir(parents=True)
    state.write(repo, files)
    box.run(["git", "init", "-q", "-b", "main"], cwd=repo, expect=0)
    profiles.commit(box, repo, "a rust crate")
    return repo


@pytest.mark.parametrize("key", _harness_cells(
    "lin-cc-only-reader", "fresh: README pip start on a Rust repo with a root Cargo.toml; init writes the "
                          "rust lane commented and doctor notes the lcov reader"))
def test_cc_only_reader_fresh(box, templates, key):
    profiles.session_crapkit(box, templates)
    repo = _crate(box)
    profiles.add_harnesses(box)
    box.run(["crapkit", "init"], cwd=repo, expect=0)
    config = (repo / "crapkit.toml").read_text(encoding="utf-8")
    doctor = output(box.run(["crapkit", "doctor"], cwd=repo, expect=0))

    assert STANZA in config and "coverage_optional = true" in config, box.transcript.text()
    assert NOTE in doctor, box.transcript.text()


@cell("lin-up-0.8.1-cc-only-reader", channel="pip venv", harness="none",
      scenario="upgrade from 0.8.1 by the guide's pip row: a Rust repo 0.8.1 initialized and seeded keeps "
               "crapkit.toml byte-identical, and doctor adds the lcov reader note",
      use_cases="upgrade guide, init, doctor, cc-only scope", os="linux", image="core", cadence="nightly")
def test_lin_up_0_8_1_cc_only_reader(box, candidate):
    state.pip_venv(box, "3.12")
    state.pip_install(box, f"crapkit=={OLD}")
    repo = _crate(box)
    for step in (["init"], ["coverage"], ["ratchet", "seed"]):
        box.run(["crapkit", *step], cwd=repo, expect=0)
    state.commit(box, repo, "initialized and seeded under 0.8.1")
    before = (repo / "crapkit.toml").read_bytes()
    assert NOTE not in output(box.run(["crapkit", "doctor"], cwd=repo, expect=0))

    state.upgrade_to(box, repo, candidate, state.upgrade_line(PIP))
    doctor = output(box.run(["crapkit", "doctor"], cwd=repo, expect=0))

    assert (repo / "crapkit.toml").read_bytes() == before, box.transcript.text()
    assert NOTE in doctor, box.transcript.text()
