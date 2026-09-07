"""The baseline emitted by the public command can be read by the next run."""
import json
import os

import pytest

from conftest import git_commit_all
from test_r2_measurements_verdict import artifact, prepare, run_cli


@pytest.mark.parametrize("separator", ["\u2028", pytest.param("\t", marks=pytest.mark.skipif(
    os.name == "nt", reason="literal tabs in filenames require a POSIX filesystem")),
    pytest.param("\n", marks=pytest.mark.skipif(
        os.name == "nt", reason="literal newlines in filenames require a POSIX filesystem"))])
def test_emitted_portable_baseline_roundtrips_a_real_filename(tmp_path, separator):
    prepare(tmp_path)
    path = "src/a" + separator + "b.py"
    (tmp_path / "src/app.py").rename(tmp_path / path)
    git_commit_all(tmp_path, "exact source path")
    artifact(tmp_path, 2, 0, path)
    coverage = run_cli(tmp_path, "coverage", "--reuse-artifacts", "--json")
    assert coverage.returncode == 0, coverage.stderr
    exported = run_cli(tmp_path, "verify", "--reuse-artifacts", "--emit-baseline", "base.tsv", "--json")
    assert exported.returncode == 0, exported.stderr
    imported = run_cli(tmp_path, "verify", "--reuse-artifacts", "--baseline-tsv", "base.tsv", "--json")
    assert imported.returncode == 0, imported.stderr
    assert json.loads(imported.stdout)["ok"] is True
