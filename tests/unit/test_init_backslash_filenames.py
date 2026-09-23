"""init reads a git-listed name that holds a backslash as one filename.

git ls-files separates directories with `/` on every platform, so a backslash in
its output belongs to a filename, which Linux and macOS allow. Scope sniffing and
test detection rewrote it to `/` and placed a root-level file such as
`tests\\test_x.py` inside a `tests/` directory that does not exist.
"""
from crapkit.scaffold import detect_lanes, sniff_scopes, source_candidates, starter_toml
from crapkit.universe import scopes_with_tests

PY = {"pkg": ("python",)}
PY_LANES = detect_lanes(frozenset({"pyproject.toml"}), "")


def _line(text: str, prefix: str) -> str:
    (line,) = [ln for ln in text.splitlines() if ln.startswith(prefix)]
    return line


def test_a_root_level_name_holding_a_backslash_sniffs_no_scope():
    assert sniff_scopes(["src\\main.py", "lib/util.py"]) == {"lib": ("python",)}


def test_a_root_level_name_holding_a_backslash_is_no_source_candidate():
    assert source_candidates(["app\\win.py", "app/m.py"]) == ["app/m.py"]


def test_a_root_level_test_name_holding_a_backslash_is_in_no_scope():
    assert scopes_with_tests(["pkg\\x_test.py"], {"pkg": ("pkg",)}) == frozenset()


def test_a_root_level_test_name_holding_a_backslash_names_no_test_directory():
    tracked = ["pkg/x.py", "tests\\test_x.py", "pyproject.toml"]

    text = starter_toml(PY, PY_LANES, tracked=tracked)

    assert _line(text, "pkg = ") == 'pkg = "python -m pytest -q -p no:cacheprovider"'
    assert _line(text, "# pkg:") == "# pkg: no test file under pkg/, so the whole suite runs"
