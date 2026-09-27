"""The 60-second start on repos whose environment a tool pins: uv, poetry, pdm,
pipenv, and a checked-in .venv.

init reads the lockfile beside the pytest marker and writes the lane through
that tool (`uv run python -m pytest ...`), or through the repo's own .venv
interpreter when there is no lockfile. Each cell builds the repo the way its
user already has it (the lock committed, the environment installed), runs the
README start with crapkit in its own venv, and holds the lane init wrote to
the runner the lockfile names. poetry, pdm and pipenv read a PEP 503 index
where uv and pip read find-links, so they lock against kit/pyindex.
"""
from __future__ import annotations

from pathlib import Path

from kit import installers, pyindex, repos
from kit.cells import cell
from kit.installers import readme_start

PACKET = "deploy-channels"


def _tool(box, name: str) -> None:
    """A project tool the way its users install it: `uv tool install`, offline."""
    box.run(["uv", "tool", "install", "-q", name], expect=0)
    box.prepend_path(box.run(["uv", "tool", "dir", "--bin"], expect=0).stdout.strip())


def _uv(box, repo: Path, index) -> None:
    box.run(["uv", "sync"], cwd=repo, expect=0)


SOURCE = '\n[[tool.poetry.source]]\nname = "company"\nurl = "{url}"\npriority = "primary"\n'


def _poetry(box, repo: Path, index) -> None:
    """A poetry repo that resolves against its company index, as most do."""
    _tool(box, "poetry")
    with (repo / "pyproject.toml").open("a", encoding="utf-8") as pyproject:
        pyproject.write(SOURCE.format(url=index.simple))
    box.run(["poetry", "lock"], cwd=repo, expect=0)
    box.run(["poetry", "install"], cwd=repo, expect=0)


def _pdm(box, repo: Path, index) -> None:
    _tool(box, "pdm")
    box.env["PDM_PYPI_URL"] = index.simple
    box.run(["pdm", "lock"], cwd=repo, expect=0)
    box.run(["pdm", "install"], cwd=repo, expect=0)


def _pipenv(box, repo: Path, index) -> None:
    _tool(box, "pipenv")
    box.env.update(PIPENV_PYPI_MIRROR=index.simple, PIPENV_NOSPIN="1", PIPENV_YES="1")
    box.run(["pipenv", "lock"], cwd=repo, expect=0)
    box.run(["pipenv", "install", "--dev"], cwd=repo, expect=0)


def _dot_venv(box, repo: Path, index) -> None:
    """The template built its own .venv with pytest and pytest-cov already."""


PROJECTS = {
    "uv": ("uv-project", _uv, 'command = "uv run python -m pytest'),
    "poetry": ("poetry-project", _poetry, 'command = "poetry run python -m pytest'),
    "pdm": ("pdm-project", _pdm, 'command = "pdm run python -m pytest'),
    "pipenv": ("pipenv-project", _pipenv, 'command = "pipenv run python -m pytest'),
    "dotvenv": ("dot-venv", _dot_venv, 'command = "{python:.venv} -m pytest'),
}


def _lockfile_start(box, templates, key: str) -> Path:
    """crapkit from its own venv; the repo locked and installed by its tool; then
    the README start. The lock is the user's, so it is committed before init."""
    template, prepare, lane = PROJECTS[key]
    installers.pip_venv(box, "3.12", name="crapkit-venv")
    repo = repos.checkout(box, template, cache=templates)
    with pyindex.serve([Path(box.toolchain["wheelhouse"]), *map(Path, box.find_links[1:])]) as index:
        prepare(box, repo, index)
        if box.run(["git", "status", "--porcelain"], cwd=repo, expect=0).stdout.strip():
            installers.commit(box, repo, f"lock the {key} environment")
        readme_start(box, repo)
    assert lane in (repo / "crapkit.toml").read_text(encoding="utf-8")
    return repo


def _cell(key: str):
    return cell(f"lin-lockfile-{key}", channel="pip venv", harness=key if key != "dotvenv" else "a checked-in .venv",
                scenario="fresh: start on each template with the lane init writes", use_cases="init, coverage",
                os="linux", image="core", cadence="nightly")


@_cell("uv")
def test_a_uv_locked_repo_gets_a_uv_run_lane(box, templates):
    _lockfile_start(box, templates, "uv")


@_cell("poetry")
def test_a_poetry_locked_repo_gets_a_poetry_run_lane(box, templates):
    _lockfile_start(box, templates, "poetry")


@_cell("pdm")
def test_a_pdm_locked_repo_gets_a_pdm_run_lane(box, templates):
    _lockfile_start(box, templates, "pdm")


@_cell("pipenv")
def test_a_pipenv_locked_repo_gets_a_pipenv_run_lane(box, templates):
    _lockfile_start(box, templates, "pipenv")


@_cell("dotvenv")
def test_a_repo_with_its_own_venv_gets_that_interpreter_in_its_lane(box, templates):
    repo = _lockfile_start(box, templates, "dotvenv")

    assert (repo / ".venv").is_dir()
