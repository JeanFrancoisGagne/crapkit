"""Replay fixed assertions with the local reader extension omitted."""
from pathlib import Path
import sys
import crapkit
import pytest

ROOT = Path(__file__).resolve().parents[5]
assert Path(crapkit.__file__).resolve() == ROOT / 'src/crapkit/__init__.py'

class StockReplay:
    def pytest_collection_modifyitems(self, items):
        for item in items:
            module = item.module
            if module.__name__.endswith('test_lizardtypescript_expressions'):
                original = module.functions
                def stock(source, filename='case.ts', *, corrected=True, original=original):
                    return original(source, filename, corrected=False)
                module.functions = stock
                break

sys.exit(pytest.main([
    str(ROOT / 'tests/unit/test_lizardtypescript_expressions.py'),
    '-k', 'each_expression_arrow or multiline_siblings',
    '-o', 'addopts=--tb=short -p no:cacheprovider', '-q', '-p', 'no:randomly',
], plugins=[StockReplay()]))
