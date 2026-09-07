"""Mutation refuses linked input and worker identities before writing them."""
import os

import pytest

from crapkit.errors import ToolError
from crapkit.mutate_pool import run_mutants
from test_r2_execution_mutation import SOURCE, fixture_repo, git, mutation


def symlink(target, link, *, directory=False):
    try:
        os.symlink(target, link, target_is_directory=directory)
    except OSError as error:
        pytest.skip(f'symlink creation unavailable: {error}')


def test_a_tracked_source_link_cannot_mutate_the_external_source(tmp_path):
    outside = tmp_path / 'source.py'
    outside.write_text(SOURCE, encoding='utf-8')
    observed = tmp_path / 'observed.txt'
    runner = ('from pathlib import Path\nimport m\n'
              'if not m.enabled():\n'
              f'    Path({str(observed)!r}).write_text(Path({str(outside)!r}).read_text())\n')
    root = fixture_repo(tmp_path, runner)
    (root / 'm.py').unlink()
    symlink(outside, root / 'm.py')
    git(root, 'config', 'core.symlinks', 'true')
    git(root, 'add', 'm.py')
    git(root, 'commit', '-qm', 'tracked source link')
    assert git(root, 'ls-files', '--stage', 'm.py').startswith(b'120000 ')
    cfg, mutant = mutation(root)

    with pytest.raises(ToolError, match='mutation.*link'):
        run_mutants(root, cfg, [mutant], lambda *a: None)

    assert not observed.exists()
    assert outside.read_text() == SOURCE


def test_dirty_replay_cannot_write_through_a_link_in_the_committed_worker(tmp_path):
    outside = tmp_path / 'settings.txt'
    outside.write_text('external input', encoding='utf-8')
    root = fixture_repo(tmp_path, 'import m\nassert m.enabled()\n')
    symlink(outside, root / 'settings.txt')
    git(root, 'config', 'core.symlinks', 'true')
    git(root, 'add', 'settings.txt')
    git(root, 'commit', '-qm', 'linked setting')
    (root / 'settings.txt').unlink()
    (root / 'settings.txt').write_text('dirty input', encoding='utf-8')
    cfg, mutant = mutation(root)

    with pytest.raises(ToolError, match='mutation.*link'):
        run_mutants(root, cfg, [mutant], lambda *a: None)

    assert outside.read_text() == 'external input'
    assert (root / 'settings.txt').read_text() == 'dirty input'


@pytest.mark.parametrize('link_kind', ['symlink', 'link'])
def test_restoration_cannot_write_through_a_link_left_by_the_suite(tmp_path, link_kind):
    outside = tmp_path / 'source.py'
    outside.write_text('external source', encoding='utf-8')
    # Establish link support before asking a child interpreter to create one.
    link = tmp_path / 'link-probe'
    symlink(outside, link)
    link.unlink()
    runner = ('from pathlib import Path\nimport os, m\n'
              'if not m.enabled():\n'
              '    Path("m.py").unlink()\n'
              f'    os.{link_kind}({str(outside)!r}, "m.py")\n')
    root = fixture_repo(tmp_path, runner)
    cfg, mutant = mutation(root)

    with pytest.raises(ToolError, match='mutation.*link'):
        run_mutants(root, cfg, [mutant], lambda *a: None)

    assert outside.read_text() == 'external source'
    assert (root / 'm.py').read_text() == SOURCE
