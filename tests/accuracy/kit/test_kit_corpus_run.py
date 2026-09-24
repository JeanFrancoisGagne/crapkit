"""kit.corpus_run: a corpus measured once, its surfaces on disk, private copies for writers."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from accuracy.kit import corpus_run, surfaces

pytestmark = pytest.mark.process


@pytest.fixture(scope="module")
def seed_run(tmp_path_factory):
    return corpus_run.measure(corpus_run.SEED, tmp_path_factory.mktemp("corpus"))


def _runs(root):
    connection = sqlite3.connect((root / ".crapkit" / "crap.sqlite").as_uri() + "?mode=ro",
                                 uri=True)
    try:
        return connection.execute("select kind from runs order by id").fetchall()
    finally:
        connection.close()


def test_one_inventory_and_one_coverage_run_write_the_store(seed_run):
    assert _runs(seed_run.root) == [("inventory",), ("coverage",)]
    assert seed_run.codes["coverage.json"] == 0
    assert json.loads(seed_run.output("coverage.json"))["functions"] == 9


def test_every_surface_is_on_disk_and_readable(seed_run):
    roster = surfaces.roster_from_tsv(seed_run.output("scored.tsv"))

    assert len(surfaces.from_tsv(seed_run.output("scored.tsv"), roster)) == 9
    assert len(surfaces.from_tsv(seed_run.output("inventory.tsv"), roster)) == 9
    assert json.loads(seed_run.output("coverage.sarif"))["version"] == "2.1.0"
    assert surfaces.from_html(seed_run.output("report.html"), roster)
    assert set(seed_run.codes) == {name for name, _ in corpus_run.WRITES + corpus_run.SURFACES}


def _worklist_counts(run):
    worklist = json.loads(run.output("worklist.json"))
    return worklist["active_total"], worklist["dormant_count"]


def test_the_clock_is_the_corpus_epoch(seed_run, tmp_path):
    # The seed commit is one day old at the corpus epoch, so its one
    # over-floor function is active; 13 months later the 12-month churn
    # window holds no commit and the same function is dormant.
    later = corpus_run.measure(corpus_run.SEED, tmp_path, corpus_run.DEFAULT_NOW + 400 * 86_400)

    assert seed_run.date_now == corpus_run.DEFAULT_NOW
    assert _worklist_counts(seed_run) == (1, 0)
    assert _worklist_counts(later) == (0, 1)


def test_a_second_ask_reads_the_first_measurement(seed_run, tmp_path_factory):
    again = corpus_run.measure(corpus_run.SEED, seed_run.root.parent.parent)

    assert again.root == seed_run.root
    assert _runs(again.root) == [("inventory",), ("coverage",)]


def test_workers_that_ask_at_once_share_one_measurement(tmp_path):
    with ThreadPoolExecutor(3) as pool:
        runs = list(pool.map(lambda _: corpus_run.measure(corpus_run.SEED, tmp_path), range(3)))

    assert len({run.root for run in runs}) == 1
    assert _runs(runs[0].root) == [("inventory",), ("coverage",)]


def test_a_private_copy_leaves_the_shared_run_alone(seed_run, tmp_path):
    copy = seed_run.private_copy(tmp_path / "mine")
    (copy / "src" / "py" / "calc.py").write_text("x = 1\n", encoding="utf-8")

    assert "def classify" in (seed_run.root / "src" / "py" / "calc.py").read_text(
        encoding="utf-8")


def test_the_epoch_comes_from_corpus_toml(tmp_path):
    toml = tmp_path / "corpus.toml"
    toml.write_text("[small]\ngit_test_date_now = 1788220799\n", encoding="utf-8")

    assert corpus_run.date_now(toml) == 1788220799
    assert corpus_run.date_now(tmp_path / "absent.toml") == corpus_run.DEFAULT_NOW


def test_the_digest_moves_with_a_byte(tmp_path):
    (tmp_path / "a.py").write_bytes(b"x = 1\n")
    before = corpus_run.digest(tmp_path)
    (tmp_path / "a.py").write_bytes(b"x = 2\n")

    assert corpus_run.digest(tmp_path) != before
