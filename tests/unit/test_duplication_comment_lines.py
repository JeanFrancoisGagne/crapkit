"""duplication leaves out a function's blank lines and comment lines, and a
comment is what the file's language calls one.

One prefix list served every language: a line starting with `#`, `//`, `/*`,
`*` or three quotes was a comment everywhere. So a Python `**spread(...)`,
`*args` or `// 2` line, a C `*out = x;` or `#define` line, a Rust `#[attr]`
line and a JavaScript `*method() {` line never entered a shingle, and two
functions that differ only there read as closer than they are. The other way
round, a block comment's lines that open with no star were read as code.
"""
import pytest

from crapkit.dup import find_duplicates, function_index
from crapkit.snapshot import InventoryRow

NL = "\n"


def row(path, name, start, end):
    return InventoryRow(scope="src", path=path, long_name=name, start=start, end=end,
                        ccn_std=3, ccn_mod=3, ccn=3, nloc=end - start + 1, params=1, nesting=1,
                        occurrence=1)


def one_slot(head, body, slot, rest):
    """head, five body lines, the slot line, the rest: the slot sits at 6 of 13."""
    return NL.join([head, *body[:5], slot, *rest]) + NL


PY = ["    acc = 0", "    for entry in items:", "        acc += entry", "    merged = dict(",
      "        base=acc,"]
PY_REST = ["        limit=limit,", "    )", "    size = len(items)", "    if size > limit:",
           "        return merged", "    return None"]
PYF = ["    acc = 0", "    for entry in items:", "        acc += entry", "    middle = (acc",
       "              + limit"]
PYF_REST = ["              - 1)", "    size = len(items)", "    if size > middle:", "        return acc",
            "    acc -= size", "    return None"]
C = ["    int acc = 0;", "    for (int i = 0; i < limit; i++) {", "        acc += i;", "    }",
     "    int size = limit * 2;"]
C_REST = ["    if (size > acc) {", "        acc = size;", "    }", "    acc -= limit;",
          "    return acc;", "}"]
RS = ["    let mut acc = 0;", "    for item in items {", "        acc += item;", "    }",
      "    let size = items.len() as i32;"]
RS_REST = ["    let spare = acc - size;", "    if spare > limit {", "        return spare;", "    }",
           "    acc", "}"]
JS_BODY = ["    let acc = 0;", "    for (const entry of this.items) {", "      acc += entry;", "    }",
           "    const size = this.items.length;", "    if (size > this.limit) {",
           "      acc -= size;", "    }", "    yield acc;", "    yield size;", "  }"]

# Two functions differing in one line that starts like a comment and is code.
# Hand containment: 13 lines, 10 shingles, of which the def line spoils one and
# the slot line four, so 5/10 = 0.5. The JavaScript pair differ in their first
# line only, over 12 lines: 8 of 9 shingles clear of it.
VARIANTS = {
    "python-star": ("s1.py", one_slot("def star_a(items, limit):", PY, "        **first(items),", PY_REST),
                    "s2.py", one_slot("def star_b(items, limit):", PY, "        **second(items),", PY_REST),
                    (1, 13), 0.5),
    "python-floordiv": ("f1.py", one_slot("def half_a(items, limit):", PYF, "              // 2", PYF_REST),
                        "f2.py", one_slot("def half_b(items, limit):", PYF, "              // 3", PYF_REST),
                        (1, 13), 0.5),
    "c-deref": ("d1.c", one_slot("int deref_a(int *out, int limit) {", C, "    *out = acc + 1;", C_REST),
                "d2.c", one_slot("int deref_b(int *out, int limit) {", C, "    *out = acc + 2;", C_REST),
                (1, 13), 0.5),
    "rust-attribute": ("r1.rs", one_slot("fn attr_a(items: &[i32], limit: i32) -> i32 {", RS,
                                         "    #[allow(unused_variables)]", RS_REST),
                       "r2.rs", one_slot("fn attr_b(items: &[i32], limit: i32) -> i32 {", RS,
                                         "    #[allow(dead_code)]", RS_REST),
                       (1, 13), 0.5),
    "js-generator": ("g1.js", NL.join(["class Alpha {", "  *alpha() {", *JS_BODY, "}"]) + NL,
                     "g2.js", NL.join(["class Beta {", "  *beta() {", *JS_BODY, "}"]) + NL,
                     (2, 13), 0.8889),
}


@pytest.mark.parametrize("variant", VARIANTS)
def test_a_code_line_that_starts_like_a_comment_is_shingled(variant):
    left, left_text, right, right_text, (start, end), expected = VARIANTS[variant]
    rows = [row(left, "left", start, end), row(right, "right", start, end)]

    (pair,) = find_duplicates(rows, lambda: {left: left_text, right: right_text}, similarity=0.0)

    assert pair["similarity"] == expected


# Each language's own comment syntax: every function below holds some code
# lines and some comment lines, and the index keeps exactly the code lines.
# `kept` counts them; a function of n kept lines, all distinct, has n - 3 shingles.
LANGUAGES = {
    "python": ("m.py", ['def spread(items, limit):', '    # a comment', '    """Docstring line."""',
                        '    merged = call(', '        *items,', '        **options,', '    )',
                        '    half = (limit', '            // 2)', '    return merged, half'], 8),
    "c": ("m.c", ['int deref(int *out, int limit) {', '    // a comment', '    /* one line */', '    /*',
                  '     * a block', '       without a star', '     */', '#define SCALE 2',
                  '    *out = limit', '        * SCALE;', '    /* lead */ limit += 1;',
                  '    return *out;', '}'], 7),
    "rust": ("m.rs", ['fn attr(items: &[i32]) -> i32 {', '    // a comment', '    #[allow(unused_variables)]',
                      '    let spare = 1;', '    /* block */', '    let total: i32 = items.iter().sum();',
                      '    total', '        * 2', '}'], 7),
    "javascript": ("m.js", ['function scale(a, b) {', '  // a comment', '  /*', '    disabled: legacy(a);',
                            '    legacy(b);', '  */', '  const x = a', '    * b;', '  /** doc */',
                            '  return x;', '}'], 5),
    "typescript": ("m.ts", ['function scale(a: number, b: number) {', '  /*', '    old: a + b;',
                            '  */ const x = a', '    * b;', '  return x;', '}'], 5),
    "go": ("m.go", ['func bump(p *int, limit int) {', '\t// a comment', '\t*p = limit', '\t*p += 1',
                    '\t/* block */', '\tfmt.Println(*p)', '}'], 5),
    "swift": ("m.swift", ['func load() -> Int {', '    var total = 0', '#if DEBUG', '    total += 1',
                          '#endif', '    // a comment', '    return total', '}'], 7),
    "java": ("M.java", ['String query(int limit) {', '    String sql = """', '        SELECT a FROM t',
                        '        """;', '    // a comment', '    return sql + limit;', '}'], 6),
    "powershell": ("m.ps1", ['function Get-Total {', '    <#', '    .SYNOPSIS', '    Adds the items.',
                             '    #>', '    # a comment', '    $total = 0',
                             '    foreach ($item in $items) {', '        $total += $item', '    }',
                             '    $total', '}'], 7),
    "shell": ("m.sh", ['total() {', '  # a comment', '  local sum=0', '  for n in "$@"; do',
                       '    sum=$((sum + n))', '  done', '  echo "$sum"', '}'], 7),
    "zig": ("m.zig", ['fn total(items: []const u8) u32 {', '    // a comment', '    /// doc comment',
                      '    var sum: u32 = 0;', '    for (items) |item| {', '        sum += item;',
                      '    }', '    return sum;', '}'], 7),
}


@pytest.mark.parametrize("language", LANGUAGES)
def test_each_language_leaves_out_its_own_comment_lines_and_no_code_line(language):
    path, lines, kept = LANGUAGES[language]
    rows = [row(path, "f", 1, len(lines))]

    ((_, shingles),) = function_index(rows, {path: NL.join(lines) + NL}, min_lines=1).functions()

    assert shingles == kept - 3


def test_a_line_of_many_closed_block_comments_before_its_code_is_code():
    """Each block comment closed on a line hands what follows it to the next
    read. That read was a nested call per comment, so a minified line of 600
    of them raised RecursionError and ended the whole duplication run."""
    lines = ["function f(a) {", *[f"  x{i} = a;" for i in range(8)], "  " + "/**/" * 600 + "y = 1;", "}"]
    rows = [row("m.js", "f", 1, len(lines))]

    ((_, shingles),) = function_index(rows, {"m.js": NL.join(lines) + NL}, min_lines=1).functions()

    assert shingles == len(lines) - 3


# A block comment the reader opens must be one a later line of the function
# closes. A function's text cannot end inside a comment, so an opener nothing
# closes sat in a string: a `/*` or `<#` line inside a template literal or a
# here-string. Read as a comment, it hid every line after it.
UNCLOSED = {
    "template-literal": ("m.ts", ['function banner(a: number): string {', '  const css = `',
                                  '/* banner', '`;', '  a += 1;', '  a *= 2;', '  return css + a;',
                                  '}'], 8),
    "here-string": ("m.ps1", ['function Get-Banner {', '    $text = @"', '<# banner', '"@',
                              '    $count = 1', '    $count += 2', '    $text', '}'], 8),
    "reopened-after-the-last-closer": ("m.c", ['int bump(int a) {', '    /* note */ /* half',
                                               '    a += 1;', '    a *= 2;', '    return a;', '}'], 6),
}


@pytest.mark.parametrize("shape", UNCLOSED)
def test_a_block_opener_no_later_line_closes_is_code(shape):
    path, lines, kept = UNCLOSED[shape]
    rows = [row(path, "f", 1, len(lines))]

    ((_, shingles),) = function_index(rows, {path: NL.join(lines) + NL}, min_lines=1).functions()

    assert shingles == kept - 3


# A block comment opened after code, past a space or tab, runs on to its
# closer, so its later lines are comment lines: ` * and goes on` and ` */`
# here. The opener must stand clear of a string or a line comment: after a
# quote, or after `//`, it opens nothing.
AFTER_CODE = {
    "c": ("m.c", ['int clamp(int a) {', '    int x = a; /* starts here', '     * and goes on',
                  '     */', '    return x;', '}'], 4),
    "javascript": ("m.js", ['function clamp(a) {', '  const x = a;\t/* starts here',
                            '    and goes on', '  */', '  return x;', '}'], 4),
    "powershell": ("m.ps1", ['function Get-Clamp {', '    $x = 1 <# starts here', '    and goes on',
                             '    #>', '    $x', '}'], 4),
    "then-code-after-the-closer": ("m.c", ['int clamp(int a) {', '    int x = a; /* starts',
                                           '     * more */ x += 1;', '    return x;', '}'], 5),
    "in-a-line-comment": ("m.c", ['int clamp(int a) {', '    int x = a; // see /* here', '    x += 1;',
                                  '    x *= 2;', '    /* note */', '    return x;', '}'], 6),
    "in-a-string": ("m.js", ['function clamp(dir) {', '  const glob = dir + "/*";', '  let n = 1;',
                             '  n += 2;', '  /* note */', '  return glob + n;', '}'], 6),
    "never-closed": ("m.c", ['int clamp(int a) {', '    int x = a; /* never closed', '    x += 1;',
                             '    x *= 2;', '    return x;', '}'], 6),
    "open-template-literal": ("m.js", ['function clamp(a) {', '  const s = `a /* b', '  c`;', '  a += 1;',
                                       '  /* note */', '  return s + a;', '}'], 6),
}

# The line is read from its start: a `//` or `#` inside a closed string or a
# closed block comment is no line comment, a quote of one kind inside a
# string of the other is no quote, a backslash escapes a quote, and in the
# languages where `'` marks one character, a Rust lifetime opens nothing. The
# first opener outside them all is the one that counts.
AFTER_CODE_READ_FROM_THE_START = {
    "line-comment-in-a-string": ("m.c", ['int f(void) {', '    const char *s = "http://x"; /* note',
                                         '     * more', '       plain', '     */', '    return 0;', '}'], 4),
    "powershell-line-comment-in-a-string": ("m.ps1", ['function F {', '    $s = "a#b" <# note', '    more',
                                                      '    #>', '    $s', '}'], 4),
    "line-comment-in-a-closed-block-comment": ("m.c", ['int f(int a) {',
                                                       '    int x = a; /* http://x */ x += 1; /* note',
                                                       '    more', '    */', '    return x;', '}'], 4),
    "other-quote-in-a-string": ("m.js", ['function f(a) {', '  const s = "it\'s"; /* note', '  more', '  */',
                                         '  return s;', '}'], 4),
    "escaped-quote-in-a-string": ("m.c", ['int f(void) {', '    puts("\\""); /* note', '    more', '    */',
                                          '    return 0;', '}'], 4),
    "quote-as-a-character": ("m.c", ['int f(void) {', "    char q = '\"'; /* note", '    more', '    */',
                                     '    return q;', '}'], 4),
    "rust-lifetime": ("m.rs", ['fn f(x: &str) -> usize {', "    let s: &'static str = x; /* note",
                               '    more', '    */', '    s.len()', '}'], 4),
    "first-opener-with-a-quote-after-it": ("m.c", ['int f(int a) {', '    int x = a; /* say " /* more',
                                                   '    more', '    */', '    return x;', '}'], 4),
    "powershell-backslash-ends-a-string": ("m.ps1", ['function F {', '    $d = "C:\\temp\\" <# note',
                                                     '    more', '    #>', '    $d', '}'], 4),
    # The limit the README names: a raw string escapes nothing, but its last
    # backslash reads as escaping the quote, so the string reads as open.
    "raw-string-ending-in-a-backslash": ("m.rs", ['fn f() -> usize {', '    let p = r"C:\\"; /* note',
                                                  '    more', '    */', '    p.len()', '}'], 6),
}


@pytest.mark.parametrize("shape", [*AFTER_CODE, *AFTER_CODE_READ_FROM_THE_START])
def test_a_block_comment_opened_after_code_leaves_its_later_lines_out(shape):
    path, lines, kept = {**AFTER_CODE, **AFTER_CODE_READ_FROM_THE_START}[shape]
    rows = [row(path, "f", 1, len(lines))]

    ((_, shingles),) = function_index(rows, {path: NL.join(lines) + NL}, min_lines=1).functions()

    assert shingles == max(kept - 3, 0)
