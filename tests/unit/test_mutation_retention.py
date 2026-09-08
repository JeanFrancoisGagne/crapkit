"""Mutation retains only the requested idle workers and proves cleanup ownership."""
from crapkit.cli import main
from crapkit.locks import exclusive_lock
from crapkit import mutate_pool
from pathlib import Path
import json
import time
import pytest
from mutation_fixtures import holding_checkout_hook, holding_suite, running_mutation, stop_caller
from test_mutation_cancellation import git, invoke, mutation_repo, recorded


def test_a_smaller_public_run_removes_surplus_idle_workers(mutation_repo):
    root, _ = mutation_repo
    config = root / 'crapkit.toml'
    one = config.read_text()
    config.write_text(one.replace('mutation_workers=1', 'mutation_workers=2'))
    assert invoke(root) == 0
    pool = root / '.crapkit/mutate-pool'
    assert sorted(path.name for path in pool.iterdir()) == ['w0', 'w1']
    config.write_text(one)
    assert invoke(root) == 0
    assert sorted(path.name for path in pool.iterdir()) == ['w0']
    listed = git(root, 'worktree', 'list', '--porcelain')
    assert str(pool / 'w1').replace('\\', '/') not in listed
    assert main(['mutate', '--repo', str(root), '--drop-pool']) == 0
    assert not pool.exists()


def test_a_concurrent_public_run_uses_a_recoverable_private_directory(mutation_repo):
    root, events = mutation_repo
    with exclusive_lock(root / '.crapkit/mutate-pool.lock', label='other run'):
        assert invoke(root) == 0
    paths = {Path(row['cwd']) for row in recorded(events)}
    assert len(paths) == 1
    tree = paths.pop()
    assert tree.is_relative_to(root / '.crapkit/mutate-tmp')
    assert not tree.parent.exists()
    assert len(list((root / '.crapkit/mutate-leases').glob('*.lock'))) == 1
    assert str(tree).replace('\\', '/') not in git(root, 'worktree', 'list', '--porcelain')


@pytest.mark.parametrize('change', ['missing-receipt', 'wrong-root', 'wrong-run',
                                    'wrong-version', 'boolean-version', 'float-version',
                                    'invalid-count', 'missing-lease'])
def test_recovery_keeps_unproven_directories_and_their_bytes(mutation_repo, change):
    root, _ = mutation_repo
    base = root / '.crapkit/mutate-tmp' / ('a' * 32)
    base.mkdir(parents=True)
    kept = base / 'private.txt'
    kept.write_bytes(b'keep these bytes')
    lease = root / '.crapkit/mutate-leases' / (base.name + '.lock')
    lease.parent.mkdir()
    lease.touch()
    receipt = {'version': 1, 'root': str(root.resolve()), 'run': base.name, 'workers': 1}
    changes = {'wrong-root': ('root', str(root.parent)), 'wrong-run': ('run', 'b' * 32),
               'wrong-version': ('version', 2), 'boolean-version': ('version', True),
               'float-version': ('version', 1.0), 'invalid-count': ('workers', True)}
    if change in changes:
        key, value = changes[change]
        receipt[key] = value
    if change != 'missing-receipt':
        (base / 'owner.json').write_text(json.dumps(receipt))
    if change == 'missing-lease':
        lease.unlink()
    result = mutate_pool.recover_temporary(root)
    assert [(row.path, row.status) for row in result] == [(base, 'unproven')]
    assert result[0].reason
    assert kept.read_bytes() == b'keep these bytes'


def recover_after_owner_exit(root):
    deadline = time.monotonic() + 15
    while True:
        result = mutate_pool.recover_temporary(root, dry_run=True)
        if not any(row.status == 'active' for row in result):
            return result
        assert time.monotonic() < deadline, result
        time.sleep(.02)


def test_recovery_skips_a_live_run_and_removes_it_only_after_its_writer_stops(mutation_repo):
    root, events = mutation_repo
    holding_suite(root, events)
    with exclusive_lock(root / '.crapkit/mutate-pool.lock', label='other run'):
        with running_mutation(root, events) as caller:
            tree = Path((events / 'started').read_text())
            active = mutate_pool.recover_temporary(root)
            assert [(row.path, row.status) for row in active] == [(tree.parent, 'active')]
            assert tree.exists()
            stop_caller(caller, events)
            caller.wait(timeout=15)
            planned = recover_after_owner_exit(root)
            assert [(row.path, row.status) for row in planned] == [(tree.parent, 'planned')]
            assert tree.exists()
            with exclusive_lock(events / 'writer.lock', label='writer is stopped'):
                assert not (events / 'finished').exists()
            recovered = mutate_pool.recover_temporary(root)
            assert [(row.path, row.status) for row in recovered] == [(tree.parent, 'recovered')]
            assert not tree.parent.exists()
            assert mutate_pool.recover_temporary(root) == []
    assert str(tree).replace('\\', '/') not in git(root, 'worktree', 'list', '--porcelain')


def test_recovery_waits_for_a_dead_callers_git_hook_before_reusing_its_directory(mutation_repo):
    root, events = mutation_repo
    holding_checkout_hook(root, events)
    with exclusive_lock(root / '.crapkit/mutate-pool.lock', label='other run'):
        with running_mutation(root, events) as caller:
            tree = Path((events / 'started').read_text())
            stop_caller(caller, events)
            caller.wait(timeout=15)
            planned = recover_after_owner_exit(root)
            assert [(row.path, row.status) for row in planned] == [(tree.parent, 'planned')]
            with exclusive_lock(events / 'writer.lock', label='Git hook is stopped'):
                assert not (events / 'finished').exists()
            assert mutate_pool.recover_temporary(root)[0].status == 'recovered'
            assert not tree.parent.exists()
