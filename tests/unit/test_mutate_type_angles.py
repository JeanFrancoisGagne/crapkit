"""Type arguments are syntax, not testable comparison operators."""
import pytest

from crapkit.errors import ToolError
from crapkit.mutate import file_mutants


@pytest.mark.parametrize("language,source", [
    ("typescript", "const values: Array<string> = [];"),
    ("cpp", "std::vector<int> values;"),
    ("cpp", "factory<int>();"),
    ("typescript", "const x = factory<number>();"),
    ("rust", "fn f(value: Vec<String>) {}"),
    ("java", "class A { List<String> values; }"),
    ("typescript", "const values: Map<string, Array<number>> = new Map();"),
    ("cpp", "std::map<int, std::vector<int>> values;"),
    ("rust", "fn f(value: Vec<Vec<String>>) {}"),
    ("java", "class A { List<List<String>> values; }"),
])
def test_type_argument_angles_never_become_mutants(language, source):
    assert file_mutants(source, None, language) == []


@pytest.mark.parametrize("language", ["typescript", "cpp", "rust", "java"])
def test_comparisons_keep_their_mutants_when_angles_are_unrelated(language):
    source = "return a < b && b > c;"
    assert len(file_mutants(source, None, language)) == 5


@pytest.mark.parametrize("language", ["typescript", "cpp", "rust"])
def test_unresolved_type_or_comparison_angles_are_refused(language):
    with pytest.raises(ToolError, match="ambiguous.*angle"):
        file_mutants("left < middle > right;", None, language)


def test_unchanged_ambiguous_angles_do_not_block_changed_comparisons():
    source = "left < middle > right;\nreturn value > 0;\n"
    assert len(file_mutants(source, {2}, "cpp")) == 2


def test_types_and_comparisons_can_share_a_line():
    source = "const values: Array<string> = []; return count > 0;"
    assert {mutant.op for mutant in file_mutants(source, None, "typescript")} == {"> -> >=", "> -> <="}


def test_separate_statements_do_not_pair_comparisons_across_a_type_declaration():
    source = "a < b;\nint count = 0;\nd > e;\n"
    assert len(file_mutants(source, None, "cpp")) == 4


def test_a_cast_between_comparisons_does_not_make_their_angles_types():
    assert len(file_mutants("return a < int(b) && b > c;", None, "cpp")) == 5


def test_parenthesized_expression_inside_type_arguments_keeps_its_comparison():
    mutants = file_mutants("std::array<int, (3 > 2)> values;", None, "cpp")
    assert [mutant.op for mutant in mutants] == ["> -> >=", "> -> <="]
