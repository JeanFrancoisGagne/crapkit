"""holding_suite raises the fixture's mutation deadline to the hold, or fails.

A deadline under the hold lets the product end the suite on its own and release
the writer lock, which passes a test whose product never stopped the writer.
Before, a config the rewrite did not match kept its deadline without a word.
"""
import pytest

from hang_guard import HOLD_SECONDS
from mutation_fixtures import holding_suite


def _fixture(root, config):
    (root / 'suite.py').write_text('import time\ntime.sleep(.1)\n', encoding='utf-8')
    (root / 'crapkit.toml').write_text(config, encoding='utf-8')
    return root


@pytest.mark.parametrize('spelled', ['mutation_timeout_seconds=10',
                                     'mutation_timeout_seconds = 10'], ids=['tight', 'spaced'])
def test_the_mutation_deadline_rises_to_the_hold(tmp_path, spelled):
    root = _fixture(tmp_path, f'[crapkit]\nmutation_workers=1\n{spelled}\n')

    holding_suite(root, tmp_path / 'events')

    assert (root / 'crapkit.toml').read_text(encoding='utf-8') == (
        f'[crapkit]\nmutation_workers=1\nmutation_timeout_seconds={HOLD_SECONDS}\n')


def test_a_config_without_a_mutation_deadline_fails_the_fixture(tmp_path):
    root = _fixture(tmp_path, '[crapkit]\nmutation_workers=1\n')

    with pytest.raises(AssertionError, match='mutation_timeout_seconds'):
        holding_suite(root, tmp_path / 'events')
