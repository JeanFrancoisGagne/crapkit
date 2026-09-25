"""The coverage flag: why a function has the coverage number it has, against the README's flag table.

README.md#flags-why-a-coverage-number-is-missing, as a model:

- `cc-only` when the scope sets coverage_optional;
- `no-lane` when no lane's `scopes` names the scope;
- `untested` when a lane covers the scope but its artifact is silent on the
  function: the file is not among the artifact's keys (read with json.load), or
  the producer dropped the function (an ignore hint, ground_truth.tsv `absent`);
- `measured` otherwise, except that README.md#remedy-what-to-do-about-it floors
  a Python def whose body starts on its signature's line to untested.

One repo holds the Python and JS probes four times over: in a scope whose lane
measured them, beside a copy that lane never measured, in a scope no lane
names, and in a coverage_optional scope. Every surface that carries a flag
must carry the model's.
"""
from collections import Counter
import json
import tomllib

import pytest

from accuracy.coverage_oracles import ground_table, mini_repo, probe_repo
from accuracy.kit import surfaces

RECORDED = probe_repo.RECORDED
PROBES = {"python": "py/shapes.py", "javascript": "js/shapes.js"}
SUFFIX = {"python": "py", "javascript": "js"}
RECORDING = {"python": ("coveragepy-7.16.1", "coveragepy"), "javascript": ("vitest-istanbul-5.0.1",
                                                                          "istanbul")}
# scope -> (has a lane, coverage_optional)
SCOPES = {"lane": (True, False), "bare": (False, False), "optional": (False, True)}


def _measured_path(language: str) -> str:
    return f"lane/measured.{SUFFIX[language]}"


def _files() -> dict[str, str]:
    """{repo path: probe path}: the probe measured, the same probe unmeasured
    in the lane's scope, and a copy in each other scope."""
    found = {}
    for language, probe in PROBES.items():
        found[_measured_path(language)] = probe
        found.update({f"{scope}/silent.{SUFFIX[language]}": probe for scope in SCOPES})
    return found


def _artifact(language: str) -> bytes:
    """The recording of the probe, its one file keyed at the measured path."""
    producer, parser = RECORDING[language]
    recorded = json.loads((RECORDED / producer / "call.json").read_bytes())
    if parser == "coveragepy":
        recorded["files"] = {_measured_path(language): recorded["files"][PROBES[language]]}
        return json.dumps(recorded).encode()
    entry = {**recorded[PROBES[language]], "path": _measured_path(language)}
    return json.dumps({_measured_path(language): entry}).encode()


def _config() -> str:
    scopes = [mini_repo.scope(name, [name], list(PROBES), optional=optional)
              for name, (_, optional) in SCOPES.items()]
    lanes = [mini_repo.lane(SUFFIX[language], RECORDING[language][1], ["lane"])
             for language in PROBES]
    return mini_repo.config(scopes, lanes)


@pytest.fixture(scope="module")
def flagged(tmp_path_factory):
    """(driver, coverage --json, scored.tsv rows) over one scored repo."""
    tree = {"crapkit.toml": _config()}
    tree.update({path: (probe_repo.PROBES / probe).read_bytes() for path, probe in _files().items()})
    tree.update({f"recorded/{SUFFIX[language]}.json": _artifact(language) for language in PROBES})
    driver = mini_repo.build(tmp_path_factory.mktemp("flags") / "repo", tree)
    result = driver.run("coverage", "--json", "--export", "scored.tsv")
    assert result.code == 0, result.stderr
    scored = surfaces.read_tsv((driver.root / "scored.tsv").read_text(encoding="utf-8"))[1]
    return driver, result.json(), scored


# --- the model -----------------------------------------------------------------------------------

def _truth(probe: str) -> dict[int, ground_table.Truth]:
    """The call scenario's rows of one probe, by start line."""
    producer = RECORDING["python" if probe.endswith(".py") else "javascript"][0]
    return {row.start: row for row in ground_table.rows_for(producer, "call", (probe,))}


def _artifact_keys(driver) -> set[str]:
    """Every file key of every lane's artifact, read with json.load."""
    config = tomllib.loads((driver.root / "crapkit.toml").read_text(encoding="utf-8"))
    keys = set()
    for lane in config["lane"]:
        artifact = json.loads((driver.root / lane["artifact"]).read_bytes())
        keys |= set(artifact.get("files", artifact))
    return keys


def _speaks(row: ground_table.Truth | None) -> bool:
    """The producer wrote the function, and it is not a Python def-line layout
    the README floors."""
    if row is None or row.arms == ("absent",):
        return False
    return not (row.path.endswith(".py") and row.layout in ground_table.FLOORED)


def flag_model(path: str, start: int, keys: set[str]) -> str:
    scope = path.split("/")[0]
    has_lane, optional = SCOPES[scope]
    if optional:
        return "cc-only"
    if not has_lane:
        return "no-lane"
    spoken = path in keys and _speaks(_truth(_files()[path]).get(start))
    return "measured" if spoken else "untested"


def _expected(driver, scored: list[dict]) -> dict[tuple[str, int], str]:
    keys = _artifact_keys(driver)
    return {(row["path"], int(row["start"])): flag_model(row["path"], int(row["start"]), keys)
            for row in scored}


# --- the checks -----------------------------------------------------------------------------------

@pytest.mark.process
def test_every_scored_flag_follows_the_readme_table(flagged):
    driver, _, scored = flagged
    found = {(row["path"], int(row["start"])): row["flag"] for row in scored}

    assert set(found.values()) == {"measured", "untested", "no-lane", "cc-only"}
    assert found == _expected(driver, scored)


@pytest.mark.process
def test_the_coverage_summary_counts_the_same_flags(flagged):
    driver, summary, scored = flagged
    counts = Counter(_expected(driver, scored).values())

    assert {flag: summary[flag.replace("-", "_")] for flag in counts} == dict(counts)
    assert summary["functions"] == len(scored)


# README.md#flags-why-a-coverage-number-is-missing, the "Scored" column: what a
# row with each flag may read.
SCORED_AS = {
    "untested": lambda row: float(row["cov"]) == 0.0,
    "no-lane": lambda row: float(row["cov"]) == 0.0,
    "cc-only": lambda row: (float(row["crap"]) == int(row["ccn"])
                            and row["remedy"] in ("ok", "decompose")),
    "measured": lambda row: 0.0 <= float(row["cov"]) <= 1.0,
}


@pytest.mark.process
def test_each_flag_scores_as_the_table_says(flagged):
    _, _, scored = flagged

    assert [row for row in scored if not SCORED_AS[row["flag"]](row)] == []


@pytest.mark.process
def test_the_worklist_carries_each_row_s_flag(flagged):
    driver, _, scored = flagged
    flags = {(row["path"], int(row["start"])): row["flag"] for row in scored}
    payload = driver.json("worklist", "--top", "500")
    listed = payload["active"] + payload["dormant_top"]

    assert listed and {(item["path"], item["start"]): item["flag"] for item in listed} == {
        (item["path"], item["start"]): flags[(item["path"], item["start"])] for item in listed}


@pytest.mark.process
def test_next_item_never_hands_out_a_no_lane_row(flagged):
    """README.md#flags-why-a-coverage-number-is-missing: next-item never hands
    one out and counts them in skipped_no_lane."""
    driver, _, scored = flagged
    payload = driver.run("next-item").json()

    assert payload["item"]["flag"] != "no-lane"
    assert payload["skipped_no_lane"] >= 1
