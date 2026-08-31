"""The two pyproject keys that keep the CLI entry points measured.

Every `cmd_*` in src/crapkit/cli is reached through `subprocess.run` by tests/e2e
and by nothing else. What carries coverage into those children used to be
pytest-cov's own `pytest-cov.pth`; pytest-cov 7.0.0 deleted it ("Dropped support
for subprocesses measurement") and the job is now coverage's
`[run] patch = subprocess`, added in coverage 7.10.

Neither half fails loudly on its own. Drop the patch and pytest-cov 7 measures
no child at all — `cli/admin.py` reads 0/498 statements and every `cmd_*` a diff
touches trips the gate at 0% coverage. Keep the patch but let an older coverage
in and it answers the unknown key with a CoverageWarning that pytest-cov files
under `once::CoverageWarning`, which is the same silence one warning louder.

This asserts the pair stays consistent. It does not prove the mechanism works:
that is a lane run, and the numbers it produced are in the pyproject comment.
"""
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"

# The release that added `[run] patch`, and pytest-cov 7's own floor.
PATCH_FLOOR = (7, 10, 6)


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _floor_of(requirements: list[str], name: str) -> tuple[int, ...]:
    """The `>=` floor a requirement list puts on one distribution.

    `()` when the list does not name it, which sorts below every real floor."""
    for req in requirements:
        head, _, spec = req.partition(">=")
        if head.strip() == name and spec:
            return tuple(int(part) for part in spec.strip().split("."))
    return ()


def test_the_run_config_asks_coverage_to_patch_subprocess():
    run = _pyproject()["tool"]["coverage"]["run"]

    assert "subprocess" in run["patch"]


def test_every_extra_that_ships_pytest_cov_floors_coverage_at_the_patch_release():
    extras = _pyproject()["project"]["optional-dependencies"]

    shipping = {name: reqs for name, reqs in extras.items()
                if _floor_of(reqs, "pytest-cov")}
    assert set(shipping) == {"dev", "py"}
    for name, reqs in shipping.items():
        assert _floor_of(reqs, "coverage") >= PATCH_FLOOR, f"[{name}] takes a coverage too old"
