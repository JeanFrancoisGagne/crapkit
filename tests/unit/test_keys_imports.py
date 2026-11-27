"""keys.py stays light to import: the merge driver loads it on every merge of
the marks file and the Claude hook on every edit. A fresh interpreter that
imports crapkit.keys loads the standard library and three crapkit modules:
the package, crapkit.errors, and crapkit.languages, a table that imports
nothing. The score import under TYPE_CHECKING never runs, so it loads nothing.
"""
from __future__ import annotations

import json
import sys

import hang_guard

PROBE = """
import json, sys
before = set(sys.modules)
import crapkit.keys
print(json.dumps(sorted(set(sys.modules) - before)))
"""


def _loaded_by_keys() -> list[str]:
    done = hang_guard.run([sys.executable, "-c", PROBE], text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.splitlines()[-1])


def test_importing_keys_loads_only_the_standard_library_errors_and_languages():
    loaded = _loaded_by_keys()
    tops = {name.split(".")[0] for name in loaded}

    assert sorted(name for name in loaded if name.split(".")[0] == "crapkit") == [
        "crapkit", "crapkit.errors", "crapkit.keys", "crapkit.languages"]
    assert sorted(tops - set(sys.stdlib_module_names) - {"crapkit"}) == []
