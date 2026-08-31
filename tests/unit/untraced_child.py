"""The environment a probe child needs so that nothing starts a tracer in it.

Several tests here spawn a fresh interpreter to ask what crapkit costs to
import. The answer is only crapkit's if the child runs untraced, and two
different mechanisms will start coverage in a child that inherits this
process's environment:

- pytest-cov 6 and older mark the child with `COV_CORE_SOURCE` and friends, and
  ship a `pytest-cov.pth` that reads them.
- pytest-cov 7.0.0 deleted that ("Dropped support for subprocesses
  measurement", CHANGELOG 7.0.0) and the job moved to coverage's own
  `[run] patch = subprocess`, which exports `COVERAGE_PROCESS_CONFIG` for
  coverage's own `a1_coverage.pth` to read. `COVERAGE_PROCESS_START` is the
  older spelling of the same door and the same .pth still opens it.

Strip one family and the other still starts a tracer, and the probe then
measures coverage's imports on top of crapkit's. crapkit's own lane runs under
both mechanisms at once, so both go.
"""
import os

TRACING_PREFIXES = ("COV_CORE_", "COVERAGE_PROCESS_")


def untraced_env() -> dict[str, str]:
    """This process's environment minus every variable that traces a child."""
    return {k: v for k, v in os.environ.items()
            if not k.startswith(TRACING_PREFIXES)}
