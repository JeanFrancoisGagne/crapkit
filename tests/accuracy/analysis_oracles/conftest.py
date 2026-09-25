"""Fixtures the analysis-oracles tests share.

Each measures one file set through crapkit's CLI once per session
(analysis_inventory.shared) and hands back the parsed export, so the hand,
equivalence and oracle tests all read one run:

- `probe_inventory`: every probe file under probes/;
- `src_inventory` and `src_unparsed_inventory`: crapkit's own source tree,
  as written and after ast.unparse;
- `stdlib_inventory` and `stdlib_unparsed_inventory`: the running Python's
  standard library (nightly tests only);
- `py_shape_inventory`: the named Python shapes of analysis_shapes;
- `measure_set(files, launch=PLAIN)`: any other file set, measured once per
  session under that launch.
"""
import os

import pytest

from accuracy.analysis_oracles import (analysis_corpora, analysis_inventory, analysis_shapes,
                                       analysis_tables)


def _shared_base(tmp_path_factory):
    base = tmp_path_factory.getbasetemp()
    return base.parent if os.environ.get("PYTEST_XDIST_WORKER") else base


def _measured(files: dict, tmp_path_factory, name: str, launch=analysis_inventory.PLAIN):
    measured = analysis_inventory.shared(files, _shared_base(tmp_path_factory) / name, launch)
    assert measured.code == 0, measured.stderr
    return measured


@pytest.fixture(scope="session")
def measure_set(tmp_path_factory):
    return lambda files, launch=analysis_inventory.PLAIN: _measured(
        files, tmp_path_factory, "analysis-sets", launch)


@pytest.fixture(scope="session")
def py_shape_inventory(tmp_path_factory):
    return _measured(analysis_shapes.py_shape_files(), tmp_path_factory, "analysis-shapes")


@pytest.fixture(scope="session")
def probe_inventory(tmp_path_factory):
    return _measured(analysis_tables.probe_files(), tmp_path_factory, "analysis-probes")


@pytest.fixture(scope="session")
def src_corpus():
    return analysis_corpora.crapkit_sources()


@pytest.fixture(scope="session")
def src_inventory(src_corpus, tmp_path_factory):
    return _measured(src_corpus.files, tmp_path_factory, "analysis-src")


@pytest.fixture(scope="session")
def src_unparsed(src_corpus):
    return analysis_corpora.unparsed(src_corpus)


@pytest.fixture(scope="session")
def src_unparsed_inventory(src_unparsed, tmp_path_factory):
    return _measured(src_unparsed.files, tmp_path_factory, "analysis-src-unparsed")


@pytest.fixture(scope="session")
def stdlib_corpus():
    return analysis_corpora.stdlib_sources()


@pytest.fixture(scope="session")
def stdlib_inventory(stdlib_corpus, tmp_path_factory):
    return _measured(stdlib_corpus.files, tmp_path_factory, "analysis-stdlib")


@pytest.fixture(scope="session")
def stdlib_unparsed(stdlib_corpus):
    return analysis_corpora.unparsed(stdlib_corpus)


@pytest.fixture(scope="session")
def stdlib_unparsed_inventory(stdlib_unparsed, tmp_path_factory):
    return _measured(stdlib_unparsed.files, tmp_path_factory, "analysis-stdlib-unparsed")
