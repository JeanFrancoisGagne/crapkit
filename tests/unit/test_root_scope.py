"""A declared repository-root scope must own its tracked source files."""
import pytest

from crapkit.config import Config, Scope, load_config_text
from crapkit.universe import assign_files, owning_scope, path_matchers, scope_matchers, scopes_with_tests


@pytest.mark.parametrize("spelling", [".", "./", ".\\"])
def test_admitted_root_scope_claims_root_and_nested_source(spelling):
    config = load_config_text('[crapkit]\ntarget=6\n[[scope]]\nname="root"\n'
                              f"paths=['{spelling}']\nlanguages=['python']\n")
    assert assign_files(["app.py", "lib/helper.py", "notes.md", "tests/test_app.py"], config) == {
        "root": ["app.py", "lib/helper.py"]}


def test_nested_and_file_scopes_beat_root_but_language_fallback_remains():
    config = Config(scopes=(Scope("root", (".",), ("python",)),
                            Scope("nested", ("src",), ("typescript",)),
                            Scope("file", ("app.py",), ("python",))))
    matchers = scope_matchers(config.scopes)
    assert [owning_scope(path, matchers) for path in
            ("app.py", "other.py", "src/app.ts", "src/helper.py", "notes.md")] == [
                "file", "root", "nested", "root", None]


def test_root_path_matchers_route_configs_and_tests_without_language_filter():
    paths = {"root": (".",), "nested": ("src",)}
    matchers = path_matchers(paths)
    assert [owning_scope(path, matchers) for path in
            ("crapkit.toml", "tests/test_app.py", "src/data.json")] == ["root", "root", "nested"]
    assert scopes_with_tests(["tests/test_app.py", "src/test_nested.py"], paths) == {"root", "nested"}
