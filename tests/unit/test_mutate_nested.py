"""A nested config root runs inside its corresponding private checkout path."""
import sys
from types import SimpleNamespace

import pytest

from crapkit.mutate import file_mutants
from crapkit.mutate_pool import drop_pool, run_mutants
from test_mutate_pool_kept import commit, repo


@pytest.mark.parametrize("workers", [1, 2])
def test_nested_workers_capture_inputs_across_the_git_checkout(repo, workers):
    app = repo / "app"
    app.mkdir()
    (repo / ".gitignore").write_text("artifacts/\n", encoding="utf-8")
    (app / "m.py").write_text("def enabled():\n    return False\n", encoding="utf-8")
    (app / "settings.txt").write_text("old", encoding="utf-8")
    (repo / "checks.py").write_text("raise AssertionError('old tests')\n", encoding="utf-8")
    (repo / "config.txt").write_text("old", encoding="utf-8")
    (app / "runner.py").write_text(
        "from pathlib import Path\nimport m\n"
        "exec(Path('../checks.py').read_text())\n", encoding="utf-8")
    commit(repo, "nested project")
    source = "def enabled():\n    return True\n"
    (app / "m.py").write_text(source, encoding="utf-8")
    (app / "settings.txt").write_text("new", encoding="utf-8")
    (repo / "config.txt").write_text("new", encoding="utf-8")
    (repo / "checks.py").write_text(
        "assert Path('../config.txt').read_text() == 'new'\n"
        "assert Path('settings.txt').read_text() == 'new'\n"
        "assert Path('../new-fixture.txt').read_text() == 'fresh'\n"
        "assert not Path('../gone.py').exists()\n"
        "assert not Path('.crapkit/private-state').exists()\n"
        "assert Path('../.crapkit/input.txt').read_text() == 'outside input'\n"
        "Path('runner-wrote.txt').write_text('private')\n"
        "assert m.enabled()\n", encoding="utf-8")
    (repo / "gone.py").unlink()
    (repo / "new-fixture.txt").write_text("fresh", encoding="utf-8")
    (repo / ".crapkit").mkdir()
    (repo / ".crapkit" / "input.txt").write_text("outside input", encoding="utf-8")
    (app / ".crapkit").mkdir()
    (app / ".crapkit" / "private-state").write_text("keep local", encoding="utf-8")
    mutant = file_mutants(source, None, "python")[0]._replace(path="m.py")
    cfg = SimpleNamespace(mutation_workers=workers, mutation_timeout_seconds=5,
                          mutation_command=f'"{sys.executable}" runner.py')
    try:
        for _ in range(2):
            assert run_mutants(app, cfg, [mutant, mutant], lambda *args: None) == [True, True]
        assert not (app / "runner-wrote.txt").exists()
        assert (app / "m.py").read_text() == source
    finally:
        drop_pool(app)
