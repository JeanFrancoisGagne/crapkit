"""lizard moves while crapkit stays: the metric stamp names lizard's version,
so the marks a repo signed under one lizard are refused under the next.

No newer lizard is released, so kit/fakewheel.py repacks the wheelhouse's
lizard under the next patch version into a find-links directory this cell
alone reads. The user upgrades lizard in the venv crapkit runs from, meets the
refusal, and recovers with the guide's reseed steps.
"""
from __future__ import annotations

import zipfile

import pytest

from kit import fakewheel, state, state_manifest
from kit.cells import cell
from kit.state import output

PACKET = "deploy-upgrade"


def next_patch(version: str) -> str:
    major, minor, patch = version.split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


@cell("lin-up-lizard-only", channel="pip", harness="none",
      scenario="lizard fake wheel one patch up: verify refusal names both stamps; guide recovery",
      use_cases="verify", os="linux", image="core", cadence="nightly")
def test_lin_up_lizard_only(box, templates, candidate):
    lizard, fake = state.lizard_version(), next_patch(state.lizard_version())
    source = state.build(box, candidate.version, cache=templates)
    repo = source.checkout(box)
    state.pip_venv(box)
    state.pip_install(box, f"crapkit[py]=={candidate.version}")
    links = fakewheel.lizard(box.toolchain["wheelhouse"], box.root / "fake-links", fake, old=lizard)
    before = state_manifest.take(repo)

    box.run(["python", "-m", "pip", "install", "-q", "--upgrade", "--find-links", str(links), "lizard"], expect=0)
    assert f"lizard {fake}" in output(box.run(["crapkit", "doctor"], cwd=repo, expect=0))
    refusal = box.run(["crapkit", "verify"], cwd=repo, expect=3)
    analysis = state.analysis_version(candidate)
    assert state.stamp_refusal(analysis, analysis, old_lizard=lizard, new_lizard=fake) in output(refusal), \
        box.transcript.text()

    state.measure(box, repo)
    state.reseed_and_verify(box, repo, candidate, lizard=fake)
    state.kept(box, repo, source)
    state_manifest.check(box, before, state_manifest.take(repo), label="lizard upgrade")


# --- the kit's own check of the repack --------------------------------------------------

def _wheel(path, files: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)


@pytest.mark.kit
def test_fakewheel_repacks_name_version_module_and_record(tmp_path):
    source = tmp_path / "lizard-1.0.0-py3-none-any.whl"
    _wheel(source, {"lizard_ext/version.py": 'version = "1.0.0"\n',
                    "lizard-1.0.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: lizard\nVersion: 1.0.0\n",
                    "lizard-1.0.0.dist-info/RECORD": "stale\n"})

    wheel = fakewheel.repack(source, tmp_path / "out", "lizard", "1.0.0", "1.0.1")

    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        assert wheel.name == "lizard-1.0.1-py3-none-any.whl"
        assert names == {"lizard_ext/version.py", "lizard-1.0.1.dist-info/METADATA", "lizard-1.0.1.dist-info/RECORD"}
        assert "Version: 1.0.1" in archive.read("lizard-1.0.1.dist-info/METADATA").decode()
        assert archive.read("lizard_ext/version.py").decode() == 'version = "1.0.1"\n'
        record = archive.read("lizard-1.0.1.dist-info/RECORD").decode().splitlines()
    assert record[-1] == "lizard-1.0.1.dist-info/RECORD,,"
    assert [line.split(",")[0] for line in record[:-1]] == ["lizard_ext/version.py", "lizard-1.0.1.dist-info/METADATA"]
