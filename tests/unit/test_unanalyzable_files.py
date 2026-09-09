"""A file no reader can tokenize is counted and named, and the run carries on.

Through 0.7.0 the first refusal in a corpus raised, so one ambiguous TypeScript
arrow ended `coverage`, which left the ratchet unseeded and refused every commit
in the repo, in every language. Refusing to read the arrow is specified,
tested behaviour; ending the run over it was not.
"""
from crapkit import analyze
from crapkit.merge import UnanalyzableFile


AMBIGUOUS = 'const f = [(x: number) => x < 2, (y: number) => y];\n'


def test_analyze_one_answers_with_the_refusal_instead_of_raising(tmp_path):
    """The pooled worker returns the refusal, because a raise there kills the pool."""
    path = tmp_path / 'source.ts'
    path.write_text(AMBIGUOUS, encoding='utf-8')

    rel, records = analyze.analyze_one((str(path), 'source.ts'))

    assert rel == 'source.ts'
    assert isinstance(records, UnanalyzableFile)
    assert list(records) == []
    assert 'lizard failed on source.ts' in records.reason


def test_analyze_source_answers_with_the_refusal_and_names_it(capsys):
    """The hook reads blobs through this door and must survive one bad file."""
    records = analyze.analyze_source('source.ts', AMBIGUOUS)

    assert isinstance(records, UnanalyzableFile)
    assert list(records) == []
    assert 'source.ts' in capsys.readouterr().err


def test_a_long_refusal_list_names_the_first_few_and_counts_the_rest(tmp_path, capsys):
    """Six distinct refusals: five named, the rest counted rather than dumped."""
    names = [f'source{n}.ts' for n in range(6)]
    for n, name in enumerate(names):
        (tmp_path / name).write_text(f'// {n}\n' + AMBIGUOUS, encoding='utf-8')

    fresh, _, cache = analyze.analyze_files(tmp_path, names, cache={})

    err = capsys.readouterr().err
    assert '6 file(s) could not be tokenized' in err
    assert '... and 1 more' in err
    assert cache['entries'] == {}, 'a refusal cached as an empty file goes silent'
    assert all(isinstance(fresh[name], UnanalyzableFile) for name in names)
