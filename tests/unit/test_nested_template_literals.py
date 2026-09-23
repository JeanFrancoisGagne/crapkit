"""A template literal ends at its own closing backtick, whatever its `${...}` holds.

lizard 1.24.0 reads a JavaScript-family template literal with the regex `.*?`
between backticks, so the first backtick inside it closed it: the opening one of
a template nested in `${...}`, or an escaped one in the text. A brace inside an
expression's string or nested text threw lizard's own brace count off the same
way. The reader then sat inside a template until the next backtick in the file,
and every function after it was folded into the enclosing function or dropped.
On a large consumer repo a ccn-10 function appended after one passed
`rescore --gate` with 0 functions judged.
"""
import pytest

from cli_inproc_repo import add_knotty, commit_all, repo, seed_artifacts, template_repo  # noqa: F401

from crapkit.analyze import analyze_one, analyze_source
from crapkit.cli import main
from crapkit.lizardtypescript import mask_templates

AFTER = ("export function after(x: number): number {\n  if (x === 1) {\n    return 1;\n  }\n"
         "  if (x === 2) {\n    return 2;\n  }\n  return 0;\n}\n")

# Each one line of a four-line function `a` at ccn 1, ahead of AFTER.
BODIES = {
    "nested template": "log(`x ${e ?? `y`}.`);",
    "nested template with an expression": "log(`x ${e ?? `y ${e}`}.`);",
    "three deep": "log(`x ${e ?? `y ${`z ${e}`}`}.`);",
    "brace in nested text": "log(`x ${e ? `{` : ``}.`);",
    "brace in a string": 'log(`x ${e ?? "{"}.`);',
    "backtick in a string": 'log(`x ${e ?? "`"}.`);',
    "backtick in a comment": "log(`x ${e /* ` */}.`);",
    "escaped backtick": "log(`x \\` ${e}.`);",
    "escaped placeholder": "log(`x \\${ ${e}.`);",
    "object in an expression": "log(`x ${fmt({ e })}.`);",
    "regex literal in an expression": 'log(`x ${e.replace(/[`{]/g, "")}.`);',
    "division in an expression": "log(`x ${n / 2} ${m / 3} ${e ?? `y`}.`);",
}


def shape(path: str, source: str) -> list:
    return [(r.long_name.split(" (")[0], r.start, r.end, r.ccn)
            for r in analyze_source(path, source)]


@pytest.mark.parametrize("body", BODIES.values(), ids=BODIES.keys())
def test_the_function_after_a_template_keeps_its_own_lines_and_branches(body):
    source = f"export function a(e?: string) {{\n  {body}\n  return 1;\n}}\n{AFTER}"

    assert shape("probe.ts", source) == [("a", 1, 4, 1), ("after", 5, 13, 3)]


@pytest.mark.parametrize("path", ["probe.tsx", "probe.js", "probe.jsx", "probe.mjs"])
def test_every_reader_that_shares_the_template_regex_reads_it_whole(path):
    head = "function a(e) {\n  log(`x ${e ?? `y ${e}`}.`);\n  return 1;\n}\n"
    after = "function after(x) {\n  if (x === 1) {\n    return 1;\n  }\n  return 0;\n}\n"

    assert shape(path, head + after) == [("a", 1, 4, 1), ("after", 5, 10, 2)]


FLAT = [
    "const s = `a ${b} c`;\n",
    "const s = `{ ${b} }`;\n",
    "const s = '`' + \"`\" + x;\n// `\n/* ` */\n",
    "const n = 1;\n",
]


@pytest.mark.parametrize("source", FLAT)
def test_a_file_lizard_already_reads_right_is_left_byte_identical(source):
    assert mask_templates(source) == source


def test_the_mask_keeps_every_line_and_column_where_it_was():
    source = "x(`a ${b ?? `c ${d}\n e`}\n f`);\ny();\n"
    masked = mask_templates(source)

    assert len(masked) == len(source)
    assert [i for i, c in enumerate(masked) if c == "\n"] == \
           [i for i, c in enumerate(source) if c == "\n"]
    assert masked.count("`") == 2


@pytest.mark.parametrize(("source", "masked"), [
    ("x(`a ${b // `{\n}`);\n", "x(`a ${b //   \n}`);\n"),
    ("x(`a ${b.split(/[`}]/)}`);\n", "x(`a ${b.split(/[  ]/)}`);\n"),
    ("x(`a ${b / 2 / c} ${d ?? `e`}`);\n", "x(`a ${b / 2 / c} ${d ??  e }`);\n"),
])
def test_only_the_characters_inside_an_expression_that_mislead_lizard_are_blanked(source, masked):
    assert mask_templates(source) == masked


def test_an_unterminated_template_is_left_as_lizard_reads_it():
    source = "x(`a ${b ?? `c`);\n"

    assert mask_templates(source) == source


def test_a_file_read_from_disk_is_masked_before_lizard_reads_it(tmp_path):
    path = tmp_path / "probe.ts"
    path.write_text(f"export function a(e?: string) {{\n  {BODIES['three deep']}\n  return 1;\n}}\n"
                    f"{AFTER}", encoding="utf-8", newline="\n")

    _, records = analyze_one((str(path), "probe.ts"))

    assert [(r.long_name.split(" (")[0], r.start, r.end, r.ccn) for r in records] == \
           [("a", 1, 4, 1), ("after", 5, 13, 3)]


def test_the_gate_judges_a_function_appended_after_a_nested_template(repo, capsys):
    with open(repo / "src" / "app.ts", "a", encoding="utf-8", newline="\n") as fh:
        fh.write("\nexport function label(e?: string, v?: string): string {\n"
                 "  return `x ${e ?? `y ${v}`}.`;\n}\n")
    commit_all(repo, "a nested template")
    seed_artifacts(repo)
    assert main(["coverage", "--reuse-artifacts", "--repo", str(repo)]) == 0
    add_knotty(repo)
    capsys.readouterr()

    code = main(["rescore", "src/app.ts", "--gate", "--repo", str(repo)])

    err = capsys.readouterr().err
    assert code == 6, err
    assert "knotty" in err, err


def test_a_cache_written_before_the_template_reader_reads_cold(tmp_path):
    """cache=5 records came from the reader that ended a template at its first inner
    backtick; read warm, they would keep hiding the functions that reader hid."""
    from crapkit.analyze import analyze_files, fingerprint
    source = "export function a(): void {\n  " + BODIES["nested template"] + "\n}\n" + AFTER
    (tmp_path / "mod.ts").write_text(source, encoding="utf-8")
    _, _, old = analyze_files(tmp_path, ["mod.ts"], cache={})
    old["fp"] = fingerprint().rsplit(";cache=", 1)[0] + ";cache=5"

    _, hits, _ = analyze_files(tmp_path, ["mod.ts"], cache=old)

    assert hits == 0
