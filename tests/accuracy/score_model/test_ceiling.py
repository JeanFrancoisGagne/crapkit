"""Ceiling per row: a scope's own `target`, else the repo's.

crapkit's Config (read from crapkit.toml text) against docs/configuration.md
lines 72 and 147 and agent-json.md line 929 (the `ceilings` label), through
model_score.ceiling.
"""
from __future__ import annotations

from hypothesis import given, strategies as st
import pytest

from accuracy.kit.settings import pure
from accuracy.score_model import cases, model_score, production

CEILING_ROWS = cases.hand("Ceiling per row")


def config_text(repo_target: int | None, scopes: dict[str, int | None]) -> str:
    head = "[crapkit]\n" + (f"target = {repo_target}\n" if repo_target is not None else "")
    blocks = [f'[[scope]]\nname = "{name}"\npaths = ["{name}"]\nlanguages = ["python"]\n'
              + (f"target = {own}\n" if own is not None else "") for name, own in scopes.items()]
    return head + "\n" + "\n".join(blocks)


def crapkit_config(repo_target: int | None, scopes: dict[str, int | None]):
    return production.load("config:load_config_text")(config_text(repo_target, scopes))


def _own(text: str) -> int | None:
    return None if text == "none" else int(text)


@pytest.mark.parametrize("given_,expected", [row[1:] for row in CEILING_ROWS],
                         ids=[row[0] for row in CEILING_ROWS])
def test_hand_rows(given_, expected):
    cfg = crapkit_config(int(given_["repo"]), {given_["scope"]: _own(given_["own"])})

    assert cfg.ceiling_of(given_["scope"]) == int(expected["ceiling"])


def test_the_default_target_is_six():
    """docs/configuration.md:72: `target` defaults to 6."""
    assert crapkit_config(None, {"api": None}).ceiling_of("api") == 6


def scope_tables():
    names = st.lists(st.sampled_from([f"s{n}" for n in range(8)]), min_size=1, max_size=6,
                     unique=True)
    return names.flatmap(lambda found: st.fixed_dictionaries(
        {name: st.one_of(st.none(), st.integers(1, 60)) for name in found}))


@given(st.integers(1, 60), scope_tables())
@pure
def test_every_scope_s_ceiling_matches_the_docs(repo_target, scopes):
    cfg = crapkit_config(repo_target, scopes)
    probes = [*scopes, "undeclared"]

    assert {name: cfg.ceiling_of(name) for name in probes} == {
        name: model_score.ceiling(name, repo_target, scopes) for name in probes}


@given(st.integers(1, 60), scope_tables())
@pure
def test_the_ceilings_label_lists_only_scopes_off_the_default(repo_target, scopes):
    """agent-json.md:929: `default` and every scope whose own target differs."""
    own = {name: model_score.ceiling(name, repo_target, scopes) for name in scopes}
    want = {"default": repo_target, **{name: value for name, value in own.items()
                                       if value != repo_target}}

    assert crapkit_config(repo_target, scopes).ceilings == want


@given(st.integers(1, 60), scope_tables(), st.integers(1, 60))
@pure
def test_a_new_scope_moves_only_its_own_ceiling(repo_target, scopes, new_target):
    before = crapkit_config(repo_target, scopes)
    after = crapkit_config(repo_target, {**scopes, "fresh": new_target})

    assert {name: after.ceiling_of(name) for name in scopes} == {
        name: before.ceiling_of(name) for name in scopes}
    assert after.ceiling_of("fresh") == new_target
