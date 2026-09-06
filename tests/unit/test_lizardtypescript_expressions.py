"""Expression separators cannot merge sibling functions or hide their branches."""
import lizard
import pytest
from lizard_languages.typescript import TypeScriptReader, TypeScriptStates

from crapkit.analyze import _extensions_for
from crapkit.errors import ToolError
from crapkit.lizardtypescript import LizardExtension


def functions(source, filename="case.ts", *, corrected=True):
    extensions = [extension for extension in _extensions_for(filename)
                  if not isinstance(extension, LizardExtension)]
    if corrected:
        extensions.insert(0, LizardExtension())
    return lizard.FileAnalyzer(extensions).analyze_source_code(filename, source).function_list


def shape(rows):
    return [(row.long_name, row.start_line, row.end_line, row.cyclomatic_complexity)
            for row in rows]


CASES = [
    ("const f = [(x) => x + 1, (x) => x + 2];", [1, 1]),
    ("const f = [(x) => x ? 1 : 2, (x) => x && 2];", [2, 2]),
    ("const f = [(x) => [(y) => x ? y : 0, (z) => z || 0], (w) => w && 2];", [2, 2, 1, 2]),
    ("const f = [(x) => (y) => x ? y : 0, (z) => z || 0];", [2, 1, 2]),
    ("const f = [(x) => (x++, x ? 1 : 2), (x) => x && 2];", [2, 2]),
    ("call((x) => x ? 1 : 2, (x) => x && 2);", [2, 2]),
    ("const f = [(x) => table[x ? 1 : 2], (x) => x && 2];", [2, 2]),
    ("const f = [(x: number): number => x ? 1 : 2, (x: number): number => x && 2];", [2, 2]),
    ("const f = [<T>(x: T) => x, <T, U>(x: T, y: U) => x];", [1, 1]),
    ('const f = [() => "<T,U> =>", () => ",[]"];', [1, 1]),
]


@pytest.mark.parametrize("source, expected_ccn", CASES)
def test_each_expression_arrow_keeps_its_own_complexity(source, expected_ccn):
    rows = functions(source)
    assert [row.cyclomatic_complexity for row in rows] == expected_ccn
    assert {row.long_name for row in rows} == {"(anonymous)"}
    assert {(row.start_line, row.end_line) for row in rows} == {(1, 1)}


def test_multiline_siblings_end_before_the_next_sibling_or_array_delimiter():
    rows = functions("const f = [\n(x) => x ? 1 : 2,\n(x) => x && 2\n];")
    assert shape(rows) == [("(anonymous)", 2, 2, 2), ("(anonymous)", 3, 3, 2)]


def test_multiline_array_expression_bodies_keep_later_branches():
    source = "const f = [\n (x) =>\n  x ? 1 : 2,\n (x) =>\n  x\n  && 2\n];"
    assert shape(functions(source)) == [("(anonymous)", 3, 3, 2), ("(anonymous)", 5, 6, 2)]


def test_nested_array_expression_spans_include_the_inner_array_body():
    source = "const f = [\n(x) => [\n (y) => x ? y : 0,\n (z) => z || 0\n],\n (w) => w && 2\n];"
    assert shape(functions(source)) == [("(anonymous)", 3, 3, 2), ("(anonymous)", 4, 4, 2),
                                       ("(anonymous)", 2, 5, 1), ("(anonymous)", 6, 6, 2)]


def test_block_body_asi_and_computed_members_keep_their_owners():
    source = "class C { [name](x) { return x ? 1 : 2; } f = [(x) => x && 2, (x) => x || 1]; }"
    assert shape(functions(source)) == [("name ( x )", 1, 1, 2), ("(anonymous)", 1, 1, 2),
                                       ("(anonymous)", 1, 1, 2)]
    source = "const f = [(x) => {\n const g = y => y\n const h = y => y && 2\n return g(x)\n}, x=> x||1];"
    assert shape(functions(source)) == [("g", 2, 2, 1), ("h", 3, 3, 2),
                                       ("(anonymous)", 1, 5, 1), ("(anonymous)", 5, 5, 2)]


def test_named_typed_and_generic_arrows_remain_supported():
    source = "const f = <T, U>(x: T, y: U) => [x, y]; const g = (x: number): number => x;"
    assert shape(functions(source)) == shape(functions(source, corrected=False))
    assert [row.name for row in functions(source)] == ["f", "g"]


def test_chained_callbacks_keep_their_signatures_spans_and_complexity():
    source = "const f = rows.map((x) => x ? 1 : 2).filter((x) => x && 2);"
    assert shape(functions(source)) == shape(functions(source, corrected=False))
    assert [row.cyclomatic_complexity for row in functions(source)] == [2, 2]


@pytest.mark.parametrize("suffix", ["ts", "tsx"])
def test_unambiguous_generic_arrow_parameters_still_parse(suffix):
    source = "const f = [<T,>(x: T) => x, <T,U>(x: T, y: U) => x];"
    assert [row.cyclomatic_complexity for row in functions(source, f"case.{suffix}")] == [1, 1]


@pytest.mark.parametrize("suffix", ["js", "jsx", "tsx"])
def test_shared_javascript_family_states_recover_sibling_callbacks(suffix):
    source = "const f = [(x) => x ? 1 : 2, (x) => x && 2];"
    assert [row.cyclomatic_complexity for row in functions(source, f"case.{suffix}")] == [2, 2]


@pytest.mark.parametrize("body", ["x < 0", "pair<T,U>(x) || x", "pair<Map<T,U>,V>(x) || x"])
def test_ambiguous_type_argument_or_comparison_comma_is_a_precise_refusal(body):
    source = f"const f = [x => {body}, x => x + 1];"
    with pytest.raises(ToolError, match=r"case.ts:1: expression-arrow body.*parentheses or a block"):
        functions(source)


@pytest.mark.parametrize("body, ccn", [("x < 0", 1), ("pair<T,U>(x) || x", 2),
                                      ("pair<Map<T,U>,V>(x) || x", 2)])
def test_parenthesized_body_resolves_the_comma_ambiguity(body, ccn):
    rows = functions(f"const f = [x => ({body}), x => x + 1];")
    assert [row.cyclomatic_complexity for row in rows] == [ccn, 1]


def test_comparison_without_an_ambiguous_comma_remains_accepted():
    assert len(functions("const f = [x => x + 1, x => x < 0];")) == 2
    assert len(functions("const f = [x => x < 0, x => x + 1];", "case.js")) == 2


@pytest.mark.parametrize("suffix, source", [
    ("py", "def f(x):\n if x:\n  return 1\n return 0\n"),
    ("cpp", "int f(int x) { if(x) return 1; return 0; }"),
    ("rs", "fn f(x:i32)->i32 { match x {0=>0,1=>1,_=>2} }"),
    ("swift", "func f(x: Int) -> Int { if x > 0 { return 1 }; return 0 }"),
    ("kt", "fun f(x: Int): Int { if(x > 0) return 1; return 0 }"),
    ("sh", "f() { if test -f a; then echo 1; fi; }"),
    ("ps1", "function F { if ($x) { return 1 }; return 0 }"),
    ("js", "function f(x) { return x ? 1 : 0; }"),
    ("tsx", "const C = (p: P) => { return <div>{p.x}</div>; };"),
    ("jsx", "const C = (p) => { return <div>{p.x}</div>; };"),
])
def test_other_supported_reader_results_do_not_change(suffix, source):
    before = functions(source, f"case.{suffix}", corrected=False)
    after = functions(source, f"case.{suffix}")
    assert [vars(row) for row in after] == [vars(row) for row in before]


def test_extension_consumes_the_existing_tokens_once_and_leaves_stock_classes_unchanged():
    source = "const f = [(x) => x + 1, (x) => x + 2];"
    context = lizard.FileInfoBuilder("case.ts")
    reader = TypeScriptReader(context)
    stock_method = TypeScriptStates._state_global
    iterations = []

    class Once:
        def __iter__(self):
            iterations.append(True)
            assert len(iterations) == 1
            yield from reader.generate_tokens(source)

    tokens = Once()
    for extension in [LizardExtension(), *lizard.get_extensions([])]:
        tokens = extension(tokens, reader)
    list(reader(tokens, reader))
    assert len(context.fileinfo.function_list) == 2
    assert TypeScriptStates._state_global is stock_method
    assert len(lizard.analyze_file.analyze_source_code("case.ts", source).function_list) == 1
