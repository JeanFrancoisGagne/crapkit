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
  session under that launch;
- `js_push`: the JS/TS probe files and shapes, written out, listed by the
  TypeScript compiler and measured; `eslint_push(mode)` is ESLint's numbers
  for them under one analysis_js oracle;
- `corpus_language(language)`: one language's files from its full-corpus
  member and crapkit's inventory of them (nightly tests only).
"""
import os

import pytest

from accuracy.analysis_oracles import (analysis_corpora, analysis_inventory, analysis_js,
                                       analysis_shapes, analysis_tables, analysis_tstests)
from accuracy.analysis_oracles.oracles import node_oracles
from accuracy.kit import oracles, runlog


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


@pytest.fixture(scope="session")
def js_push(oracle, measure_set, tmp_path_factory):
    oracle("typescript")
    files = analysis_js.push_files()
    work = tmp_path_factory.mktemp("js-push")
    paths = node_oracles.write(files, work)
    compiled = node_oracles.functions(oracles.node_modules("push"), work, paths)
    return analysis_js.Setup(work, paths, compiled, measure_set(files))


@pytest.fixture(scope="session")
def eslint_push(js_push, oracle):
    cache = {}

    def run(mode: str) -> dict:
        if mode not in cache:
            oracle("eslint-plugin-sonarjs" if mode == "cognitive" else "eslint")
            found = node_oracles.messages(oracles.node_modules("push"), js_push.work, mode,
                                          js_push.paths)
            cache[mode] = analysis_js.numbers(js_push.compiled, found, mode)
        return cache[mode]
    return run


@pytest.fixture(scope="session")
def full_corpus():
    try:
        return analysis_corpora.full_corpus_root()
    except analysis_corpora.CorpusMissing as missing:
        runlog.note("infra", message=str(missing))
        pytest.fail(str(missing), pytrace=False)


@pytest.fixture(scope="session")
def corpus_language(full_corpus, measure_set):
    cache = {}

    def get(language: str):
        if language not in cache:
            suffixes, member = analysis_tstests.LANGUAGES[language]
            files = analysis_corpora.member_files(full_corpus, member, suffixes)
            cache[language] = (files, measure_set(files))
        return cache[language]
    return get
