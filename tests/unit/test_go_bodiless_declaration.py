"""A Go function declared without a body is no function, by hand.

The Go spec (Function declarations): a function declaration without a body
provides the signature for a function implemented outside Go, such as an
assembly routine. A method declaration may omit its body the same way. Go ends
the declaration at its line, where it inserts a semicolon.

lizard's Go reader waits for a `{` past the end of the line, so the next
function's body becomes the declaration's: the declaration gets a row that
runs to the end of the next function and holds its decisions, and the next
function gets no row. The Rust reader had the same defect for a trait's
required method (tests/unit/test_rust_function_discovery.py).
"""
import pytest

from crapkit.analyze import analyze_source

AFTER = """
func After(n int) int {
\tif n > 0 {
\t\treturn 1
\t}
\treturn 0
}
"""


@pytest.mark.xfail(strict=True, reason=(
    "lizard's Go reader waits for a body past a declaration's line. Drop this "
    "marker when the Go reader ends a declaration without a body at its line."))
@pytest.mark.parametrize("declaration", [
    "func add(a, b int) int",
    "func add(a, b int) (int, error)",
    "func add(a, b int)",
    "func (s *S) add(a int) int",
    "func add[T any](a T) T",
])
def test_a_go_function_without_a_body_hides_no_function(declaration):
    code = "package b\n\n" + declaration + "\n" + AFTER
    found = {r.long_name.split()[0]: r for r in analyze_source("b.go", code, note=False)}

    assert "add" not in found
    after = found["After"]
    assert (after.start, after.end, after.ccn_std) == (5, 10, 2)
