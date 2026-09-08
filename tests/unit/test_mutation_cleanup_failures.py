"""A failed pool shrink must stop mutation before any suite starts."""
from crapkit import mutate_pool
from test_mutation_cancellation import invoke, mutation_repo, recorded


def test_a_surplus_worker_that_cannot_be_removed_refuses_mutation(mutation_repo, monkeypatch, capsys):
    root, events = mutation_repo
    with mutate_pool._worktrees(root, 2):
        pass
    try:
        with monkeypatch.context() as stalled:
            stalled.setattr(mutate_pool, 'worktree_remove', lambda root, tree, owner=None: None)
            assert invoke(root) == 5
        assert 'could not remove surplus mutation workers' in capsys.readouterr().err
        assert recorded(events) == []
        assert (mutate_pool.pool_dir(root) / 'w1').is_dir()
    finally:
        mutate_pool.drop_pool(root)
