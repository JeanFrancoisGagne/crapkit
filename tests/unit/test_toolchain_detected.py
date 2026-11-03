"""Which toolchains the repo-root files show init: `toolchain.detected`.

init reads a toolchain's markers at the repo root and writes that toolchain's
lane commented. The rows come back in table order, so two markers give the
same stanzas in the same order on every run.
"""
import pytest

from crapkit.toolchain import detected


@pytest.mark.parametrize("markers, names", [
    ({"Cargo.toml"}, ["cargo llvm-cov"]),
    ({"bun.lock"}, ["bun"]),
    ({"bun.lockb"}, ["bun"]),
    ({"bunfig.toml"}, ["bun"]),
    ({"deno.json"}, ["deno"]),
    ({"deno.jsonc"}, ["deno"]),
    ({"Cargo.toml", "bun.lock"}, ["bun", "cargo llvm-cov"]),
    ({"CMakeLists.txt"}, ["gcovr"]),
    ({"CMakeLists.txt", "Cargo.toml"}, ["cargo llvm-cov", "gcovr"]),
    ({"build.gradle.kts"}, ["gradle"]),
    ({"pom.xml", "build.gradle"}, ["gradle", "maven"]),
    ({"gradlew"}, []),
    ({"bun.lock", "bun.lockb", "bunfig.toml"}, ["bun"]),
    (set(), []),
    ({"go.mod"}, []),
    ({"pyproject.toml", "package.json"}, []),
], ids=lambda value: ",".join(sorted(value)) if isinstance(value, set) else None)
def test_each_marker_names_its_toolchain_in_table_order(markers, names):
    assert [row.name for row in detected(frozenset(markers))] == names


def test_a_nested_path_is_not_a_root_marker():
    """init hands over root file names; `svc/Cargo.toml` is a nested crate."""
    assert detected(frozenset({"svc/Cargo.toml"})) == ()
