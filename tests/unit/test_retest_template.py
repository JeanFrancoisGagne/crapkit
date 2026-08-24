"""The retest template's three placeholders. {tests} passes ids verbatim (pytest
style); {files} and {names} split them for runners like vitest, whose junit
classname is the test file and whose -t takes a name regex."""
from crapkit.lanes import build_retest_command


def test_tests_placeholder_passes_ids_verbatim():
    cmd = build_retest_command("pytest {tests} -q", {"b.py::t_two", "a.py::t_one"})
    assert cmd == 'pytest "a.py::t_one" "b.py::t_two" -q', "sorted, quoted, verbatim"


def test_files_placeholder_dedupes_classnames():
    cmd = build_retest_command("vitest run {files}", {
        "src/a.test.ts::adds", "src/a.test.ts::subtracts", "src/b.test.ts::divides"})
    assert cmd == 'vitest run "src/a.test.ts" "src/b.test.ts"'


def test_names_placeholder_is_an_escaped_regex_alternation():
    cmd = build_retest_command('vitest run {files} -t "{names}"', {
        "src/a.test.ts::adds numbers (edge)", "src/a.test.ts::subtracts"})
    # re.escape escapes spaces too; "\ " is a literal space in JS and PCRE alike
    assert '-t "adds\\ numbers\\ \\(edge\\)|subtracts"' in cmd, \
        "regex metacharacters in test names must not change the pattern"
