"""The full corpus: each member's exports against the digests goldens/full.tsv pins.

A golden, not an independent method: it pins what crapkit printed for each
member when the golden was last declared, so a change anywhere shows up here
and change control asks for its CHANGES row. Each export is also noted in the
run log with its text, so the xplat job compares the full corpus across 3.11
to 3.14, Windows and macOS (ints and labels exact, floats within 2 ulp).

The corpus is the one kit.corpus_dir finds: CRAPKIT_ACCURACY_CORPUS, /corpus in
the accuracy image, or the cache `python tools/accuracy/corpus.py fetch` fills;
a missing one is an infra miss. Rewrite the digests with `python tools/accuracy/regenerate.py goldens
--corpus DIR`, then declare the change.
"""
import hashlib

import pytest

from accuracy.corpus_goldens import full_runs
from accuracy.kit import corpus_dir, runlog

pytestmark = [pytest.mark.nightly, pytest.mark.release, pytest.mark.process, pytest.mark.golden]


@pytest.fixture(scope="module")
def corpus():
    return corpus_dir.require()


def test_the_corpus_is_the_one_corpus_toml_pins(corpus):
    tool = full_runs.corpus_tool()

    assert (corpus / "DIGEST").read_text(encoding="utf-8").strip() == tool.digest(full_runs.table())
    assert sorted(path.name for path in corpus.iterdir()
                  if (path / "crapkit.toml").is_file()) == full_runs.member_names()


@pytest.mark.parametrize("member", full_runs.member_names())
def test_each_member_prints_the_exports_its_golden_pins(corpus, member, tmp_path):
    texts = full_runs.measure_member(corpus, member, tmp_path)
    for export, text in texts.items():
        runlog.note("digest", name=f"corpus_goldens full/{member}/{export}",
                    value={"sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                           "text": text})
    golden = full_runs.read_golden()

    assert {export: full_runs.digest(text) for export, text in texts.items()} == {
        export: golden.get((member, export)) for export in texts}
