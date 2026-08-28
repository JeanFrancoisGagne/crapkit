"""Four constants that each name a language vocabulary crapkit had half-learned.

The istanbul file-filter guard, the definition-line pattern discovery reads, the
mutation operator guard, and the README sentence that tells a user which
languages exist. Each one was written for the ts/py pair and never widened.
"""
import re
from pathlib import Path

import pytest

from crapkit import discover, mutate
from crapkit.config import (SUPPORTED_LANGUAGES, ConfigError, _SOURCE_SUFFIXES,
                            load_config_text)
from crapkit.discover import callers
from crapkit.mutate import file_mutants
from crapkit.universe import LANGUAGE_EXTENSIONS

ROOT = Path(__file__).resolve().parent.parent.parent


def _doc(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def _lane_toml(command: str) -> str:
    return ('[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["javascript"]\n'
            '[[lane]]\nname = "unit"\n'
            f'command = "{command}"\n'
            'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n')


# --- _SOURCE_SUFFIXES: the vitest file-filter trap ----------------------------

def test_a_cjs_filter_beside_coverage_is_rejected_like_a_js_one():
    """universe.py already claims .cjs for javascript, so a .cjs positional
    filter narrows the include set exactly as a .js one does."""
    with pytest.raises(ConfigError, match="file filter"):
        load_config_text(_lane_toml("vitest run src/legacy.cjs --coverage"))


def test_the_filter_guard_knows_every_extension_an_istanbul_lane_can_run():
    """The guard reads vitest command strings, so it needs the js/ts family and
    nothing else: a .swift or .go filter never appears in one."""
    js_family = {ext for lang in ("typescript", "tsx", "javascript")
                 for ext in LANGUAGE_EXTENSIONS[lang]}

    assert js_family <= set(_SOURCE_SUFFIXES)


# --- discover._def_pattern: func and fun --------------------------------------

GO = ("package main\n"
      "\n"
      "func send(x int) int {\n"
      "\tif x == 0 {\n"
      "\t\treturn 0\n"
      "\t}\n"
      "\treturn send(x - 1)\n"
      "}\n"
      "\n"
      "func relay(x int) int {\n"
      "\treturn send(x)\n"
      "}\n")

SWIFT = ("func send(_ x: Int) -> Int {\n"
         "    if x == 0 {\n"
         "        return 0\n"
         "    }\n"
         "    return send(x - 1)\n"
         "}\n"
         "\n"
         "func relay(_ x: Int) -> Int {\n"
         "    return send(x)\n"
         "}\n")

KOTLIN = ("fun send(x: Int): Int {\n"
          "    if (x == 0) {\n"
          "        return 0\n"
          "    }\n"
          "    return send(x - 1)\n"
          "}\n"
          "\n"
          "fun relay(x: Int): Int {\n"
          "    return send(x)\n"
          "}\n")


@pytest.fixture()
def grep(monkeypatch):
    """`git grep`, recorded instead of spawned."""
    def stub(root, args):
        return stub.output

    stub.output = ""
    monkeypatch.setattr(discover, "_grep_output", stub)
    return stub


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


@pytest.mark.parametrize(("name", "source", "own_call", "outside_call"), [
    ("cmd/a.go", GO, 7, 11),
    ("cmd/a.swift", SWIFT, 5, 9),
    ("cmd/a.kt", KOTLIN, 5, 9),
])
def test_a_func_or_fun_body_does_not_count_as_its_own_caller(
        tmp_path: Path, grep, name: str, source: str, own_call: int, outside_call: int):
    """`_def_pattern` matched def/class/function only, so a Swift, Kotlin or Go
    definition had no span and its own recursive call read as a caller."""
    _write(tmp_path / name, source)
    grep.output = f"{name}:{own_call}:    return send(x - 1)\n{name}:{outside_call}:    return send(x)"

    found = callers(tmp_path, ["cmd"], name, "send")

    assert found["callers"] == [{"path": name, "line": outside_call}]
    assert found["count"] == 1


def test_the_function_keyword_still_wins_over_the_shorter_forms(tmp_path: Path, grep):
    """`fun` and `func` are prefixes of `function`; adding them must not stop a
    JavaScript definition from matching."""
    _write(tmp_path / "src" / "a.js", "function send(x) {\n  return send(x - 1);\n}\n")
    grep.output = "src/a.js:2:  return send(x - 1);\nsrc/b.js:4:  send(1);"

    found = callers(tmp_path, ["src"], "src/a.js", "send")

    assert found["callers"] == [{"path": "src/b.js", "line": 4}]


# --- mutate._PROTECT: the Swift range operators -------------------------------

SWIFT_HALF_OPEN = ("func total(_ b: Int) -> Int {\n"
                   "    for i in 0..<b { print(i) }\n"
                   "    return b\n"
                   "}\n")


def test_a_swift_half_open_range_is_not_a_comparison():
    """`0..<=b` and `0..>=b` do not compile, so both mutants died on the
    compiler rather than on a test and read as killed for the wrong reason."""
    mutants = file_mutants(SWIFT_HALF_OPEN, changed_lines={2}, language="swift")

    assert [m.mutated for m in mutants] == []


def test_a_real_comparison_beside_a_range_still_mutates():
    src = ("func total(_ b: Int) -> Int {\n"
           "    for i in 0..<b where i > 2 { print(i) }\n"
           "    return b\n"
           "}\n")

    mutated = {m.mutated.strip() for m in file_mutants(src, changed_lines={2}, language="swift")}

    assert mutated == {"for i in 0..<b where i >= 2 { print(i) }",
                       "for i in 0..<b where i <= 2 { print(i) }"}


def test_the_closed_range_operator_shields_the_tokens_inside_it():
    """No operator in `_OPS` sits inside `...` today, so `file_mutants` cannot
    show this. The guard is the pair: `..<` and `...` are one vocabulary, and a
    dotted operator added to either set must not split a Swift range."""
    assert mutate._covered_by_longer("for i in 0...b", 10, "..") is True


# --- the language set a user actually reads -----------------------------------

DISPLAY = {"typescript": "TypeScript", "tsx": "TSX", "javascript": "JavaScript",
           "python": "Python", "swift": "Swift", "go": "Go"}


def test_every_supported_language_has_a_display_name():
    assert set(DISPLAY) == SUPPORTED_LANGUAGES


def test_the_readme_intro_names_every_supported_language():
    """README.md's opening paragraph is the only place a user learns the set."""
    intro = _doc("README.md").split("```", 1)[0]
    missing = [name for name in DISPLAY.values() if not re.search(rf"\b{name}\b", intro)]

    assert missing == []


def test_the_handbook_standfirst_names_every_supported_language():
    standfirst = _doc("docs/handbook.html").split('class="standfirst"', 1)[1].split("</p>", 1)[0]
    missing = [name for name in DISPLAY.values() if not re.search(rf"\b{name}\b", standfirst)]

    assert missing == []
