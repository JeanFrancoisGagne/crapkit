"""Source edits whose effect on crapkit's numbers is known before crapkit runs.

Each variant rewrites one source file and states what must happen to its rows:

- `blank_above`, `comment_above`: lines added above everything shift every
  span by that many lines and move no other number;
- `append`: a function added at the end is listed with the numbers worked by
  hand below (NIST SP 500-235 sec. 4.1 and the Sonar paper: one `if`), and no
  earlier row moves;
- `module_after` (Python): module-level code after the last def moves nothing;
- `comment_in_body` (Python): a comment line as each def's first body line
  moves no count, since comments are not code (lizard README "NLOC": lines of
  code without comments), and each span grows by the comments inside it.

All variants of a file set are written side by side into one repo
(<variant>/<path>), so one crapkit run measures every one. No crapkit import.
"""
from __future__ import annotations

import ast

COMMENT = {".py": "#", ".ts": "//", ".tsx": "//", ".js": "//", ".jsx": "//", ".mjs": "//",
           ".cjs": "//"}
BLANK_LINES, COMMENT_LINES = 3, 2
COUNTS = ("ccn_std", "ccn_mod", "ccn", "cognitive", "nesting", "nloc", "params")

APPENDED = {
    ".py": ("appended_fn", "\n\ndef appended_fn(q):\n    if q:\n        return 1\n    return 0\n"),
    "js": ("appendedFn", "\nexport function appendedFn(q) {\n  if (q) {\n    return 1;\n  }\n"
                         "  return 0;\n}\n"),
}
# One `if`: ccn 1 + 1 (NIST SP 500-235 sec. 4.1); cognitive +1 for the if at
# nesting 0 (Sonar v1.7 B1); nesting 1; one parameter; 4 lines of code in the
# Python def (def, if, return, return), 6 in the braced one (the `}` lines count).
APPENDED_VALUES = {"ccn_std": 2, "ccn_mod": 2, "ccn": 2, "cognitive": 1, "nesting": 1,
                   "params": 1}
APPENDED_NLOC = {".py": 4, "js": 6}
MODULE_AFTER = "\n\nif __name__ == '__main__':\n    for i in range(2):\n        print(i)\n"


def suffix(path: str) -> str:
    return "." + path.rsplit(".", 1)[-1] if "." in path else ""


def _text(source) -> str:
    return source.decode("utf-8") if isinstance(source, bytes) else source


def blank_above(path: str, source: str) -> str:
    return "\n" * BLANK_LINES + source


def comment_above(path: str, source: str) -> str:
    mark = COMMENT[suffix(path)]
    return f"{mark} an added comment\n" * COMMENT_LINES + source


def _family(path: str) -> str:
    return ".py" if suffix(path) == ".py" else "js"


def appended(path: str) -> tuple[str, str]:
    return APPENDED[_family(path)]


def appended_values(path: str) -> dict:
    """The appended function's hand values in this file's language."""
    return {**APPENDED_VALUES, "nloc": APPENDED_NLOC[_family(path)]}


def append(path: str, source: str) -> str:
    return source.rstrip("\n") + "\n" + appended(path)[1]


def module_after(path: str, source: str) -> str:
    return source.rstrip("\n") + "\n" + MODULE_AFTER


def _defs(source: str) -> list:
    return [node for node in ast.walk(ast.parse(source))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _first_body_lines(source: str) -> list[tuple[int, int]]:
    """(line, indent) of each def's first body statement that sits below its
    def line; a body on the colon line has no line of its own to go above."""
    firsts = [(node.body[0], node.lineno) for node in _defs(source)]
    return sorted({(first.lineno, first.col_offset) for first, line in firsts
                   if first.lineno > line})


def comment_in_body(path: str, source: str) -> str:
    lines = source.split("\n")
    for line, indent in reversed(_first_body_lines(source)):
        lines.insert(line - 1, " " * indent + "# a comment in the body")
    return "\n".join(lines)


VARIANTS = {"blank_above": blank_above, "comment_above": comment_above, "append": append,
            "module_after": module_after, "comment_in_body": comment_in_body}
PYTHON_ONLY = {"module_after", "comment_in_body"}


def applies(variant: str, path: str) -> bool:
    return suffix(path) in COMMENT and (variant not in PYTHON_ONLY or suffix(path) == ".py")


def _edited(name: str, texts: dict) -> dict[str, str]:
    edit = VARIANTS[name]
    return {f"{name}/{path}": edit(path, text) for path, text in texts.items()
            if applies(name, path)}


def variant_files(files: dict) -> dict[str, str]:
    """{base/<path>: source} plus {<variant>/<path>: edited source} for every
    variant that applies to the path."""
    texts = {path: _text(source) for path, source in files.items() if suffix(path) in COMMENT}
    out = {f"base/{path}": text for path, text in texts.items()}
    for name in VARIANTS:
        out.update(_edited(name, texts))
    return out
