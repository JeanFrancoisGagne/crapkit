"""Fixtures the verdict-model tests share: a seeded World repository per module,
and a bare repository that holds only a crapkit.toml and a marks file."""
from __future__ import annotations

import pytest

from accuracy.kit import repos
from accuracy.verdict_model import verdict_world as vw

BARE_CONFIG = ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "app"\npaths = ["src"]\n'
               'languages = ["python"]\n')


@pytest.fixture(scope="module")
def seeded_world(repo_templates, tmp_path_factory):
    """seeded_world(world) is a Scenario whose world ran one coverage run, had
    the ratchet seeded from it and committed. Each call builds its own."""
    def build(world: vw.World) -> vw.Scenario:
        top = tmp_path_factory.mktemp("seeded") / "repo"
        scenario = vw.Scenario(repo_templates.copy(vw.spec(world), top), world)
        assert scenario.run("coverage").code == 0
        assert scenario.run("ratchet", "seed").code == 0
        scenario.commit("seed the ratchet")
        return scenario
    return build


@pytest.fixture
def bare_repo(make_repo):
    """A git repository holding a crapkit.toml with one scope, and nothing else."""
    spec = repos.Spec(steps=(repos.Commit(files={"crapkit.toml": BARE_CONFIG}, message="seed"),))
    return make_repo(spec).root
