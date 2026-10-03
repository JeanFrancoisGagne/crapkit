"""The toolchain table: which runner a lane runs and what that runner needs.

One row per runner, keyed by its name. init reads the flags it writes into a
lane from here, and every later reader of a runner fact (doctor's runner line,
the runner refusals, the first-run probes) keys on the same row name. Standard
library only, and never a cli module, so any of them can import it cheaply.
"""
from __future__ import annotations

from types import MappingProxyType
from typing import NamedTuple


class Toolchain(NamedTuple):
    """One runner's facts. A fact the row does not carry yet is None, or empty
    where empty is the whole fact (no extra flags, no environment)."""
    name: str
    # Each way the runner is spelled in a command, as a token sequence: one
    # token for most runners, two for `cargo llvm-cov` and `go test`.
    words: tuple[tuple[str, ...], ...]
    # The package whose presence in package.json's devDependencies names it.
    dev_dependency: str | None = None
    # The command init writes when the package declares no test script.
    # `{python}` stands for the interpreter init settled on.
    init_command: str | None = None
    # "Write the coverage report here": the path follows the flag directly.
    reports_dir_flag: str | None = None
    # The junit reporter's flags; `{cov}` is the report directory as the lane's
    # cwd spells it.
    junit_flags: str | None = None
    # The package the junit reporter needs, or None when the runner ships it.
    junit_package: str | None = None
    # Environment pairs the junit reporter reads its output path from.
    junit_env: tuple[tuple[str, str], ...] = ()
    # Flags that follow the reports directory, each with its leading space.
    extra_flags: str = ""
    # The scoped-tests template, `{files}` the files to narrow the run to.
    related_tests: str | None = None


_ROWS = (
    # pytest raises Interrupted at the END of collection when any module fails
    # to import, so pytest-cov's session finish never runs and the lane writes
    # no coverage JSON at all: one renamed module or one missing optional extra
    # takes the whole lane down and every scope falls to no-lane, while the
    # junit still lands and makes the run look half finished. With
    # --continue-on-collection-errors the modules that did collect run and
    # report, and the uncollected file's tests stay in the junit as errors.
    Toolchain("pytest", (("pytest",),),
              init_command="{python} -m pytest --cov --cov-branch",
              reports_dir_flag="--cov-report=json:",
              junit_flags="--junitxml={cov}/junit-py.xml",
              extra_flags=" --continue-on-collection-errors"),
    # Each JS runner spells "write the coverage report here" its own way and
    # rejects the other's spelling outright. vitest ships its junit reporter, so
    # its flags cost the repo nothing. vitest also writes no coverage report
    # when a test fails, so one red test turned `crapkit coverage` into exit 5
    # naming a missing coverage-final.json: reportOnFailure is vitest's alone,
    # since jest reports on a red run already and exits on a flag it does not
    # know.
    Toolchain("vitest", (("vitest",),), dev_dependency="vitest",
              init_command="npx vitest run --coverage",
              reports_dir_flag="--coverage.reportsDirectory=",
              junit_flags="--reporter=default --reporter=junit --outputFile={cov}/junit.xml",
              extra_flags=" --coverage.reportOnFailure",
              related_tests="npx vitest related --run {files}"),
    # jest needs the separate `jest-junit` package, and naming a reporter jest
    # cannot resolve turns a working lane into an error, so its flags are
    # written only when package.json already carries it. jest-junit takes no
    # path on the command line: package.json, the jest config or these two
    # variables are the whole list, and the first two are the repo's files to
    # own. Without them it drops junit.xml at the repo root.
    Toolchain("jest", (("jest",),), dev_dependency="jest",
              init_command="npx jest --coverage",
              reports_dir_flag="--coverageDirectory=",
              junit_flags="--reporters=default --reporters=jest-junit",
              junit_package="jest-junit",
              junit_env=(("JEST_JUNIT_OUTPUT_DIR", "{cov}"),
                         ("JEST_JUNIT_OUTPUT_NAME", "junit.xml")),
              related_tests="npx jest --findRelatedTests {files}"),
    Toolchain("bun", (("bun",),)),
    Toolchain("deno", (("deno",),)),
    Toolchain("cargo llvm-cov", (("cargo", "llvm-cov"),)),
    Toolchain("go test", (("go", "test"),)),
    Toolchain("c8", (("c8",),)),
)

TOOLCHAINS = MappingProxyType({row.name: row for row in _ROWS})
