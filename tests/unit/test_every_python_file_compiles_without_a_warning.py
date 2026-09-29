"""Every Python file in the repo compiles with warnings as errors.

A docstring in tools/deploy/toolchain.py named the Windows path C:\\dt with one
backslash. Python 3.12 and later print `SyntaxWarning: invalid escape sequence
'\\d'` on every run of such a file and on every import of it (3.10 and 3.11
raise a DeprecationWarning, which pytest only counts), and a later Python makes
it a SyntaxError. A doubled backslash or a raw string is the fix.
"""
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TREES = ("src", "tools", "tests", "docs")
NOT_OURS = {".venv", "venv", "node_modules", "__pycache__", ".git"}
# Sources the accuracy suite hands the analysis and its oracles as input and
# never runs: the small corpus, scored from recorded coverage, and the analysis
# probes. Each is written for the Python its syntax needs (PEP 695, 701, 750,
# 758), so an older interpreter cannot compile it, and no run prints its warning.
READ_ONLY = ("tests/accuracy/corpus_goldens/small/", "tests/accuracy/analysis_oracles/probes/")


def _measured(relative: str) -> bool:
    return relative.startswith(READ_ONLY)


def _ours(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    return not NOT_OURS.intersection(relative.parts) and not _measured(relative.as_posix())


def sources() -> list[Path]:
    found = (path for tree in TREES for path in sorted((ROOT / tree).rglob("*.py")))
    return [path for path in found if _ours(path)]


def complaint(path: Path, root: Path = ROOT) -> str:
    """Empty when path compiles with every warning raised as an error, else
    `<path>:<line>: <what Python said>`."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        try:
            compile(path.read_bytes(), str(path), "exec")
        except SyntaxError as error:
            return f"{path.relative_to(root).as_posix()}:{error.lineno}: {error.msg}"
    return ""


def test_every_python_file_compiles_with_warnings_as_errors():
    found = sources()
    complaints = [said for said in map(complaint, found) if said]

    assert len(found) > 100, f"found only {len(found)} Python files under {TREES}"
    assert not complaints, ("these files print a warning each time Python compiles them; double the "
                            "backslash or use a raw string:\n" + "\n".join(complaints))


def test_the_check_names_the_file_and_line_of_an_invalid_escape(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text('"""ok"""\nWHERE = "C:\\dt"\n', encoding="utf-8")

    said = complaint(bad, tmp_path)

    assert said.startswith("bad.py:2: ") and "\\d" in said
