"""What the tree-sitter differential tests share: the languages, their probe
files and corpus members, and one assertion.

LANGUAGES maps each language the tree-sitter counters read to its suffixes and
its full-corpus member (tests/accuracy/corpus_goldens/corpus.toml). check()
runs analysis_treesitter.compare for a set of columns and asserts that
nothing is missing, extra or differing, and writes what it set aside to the
run log. No crapkit import.
"""
from __future__ import annotations

from accuracy.analysis_oracles import analysis_treesitter
from accuracy.kit import runlog

LANGUAGES = {
    "go": ((".go",), "cobra"),
    "rust": ((".rs",), "ripgrep"),
    "java": ((".java",), "gson"),
    "c": ((".c",), "cjson"),
    "cpp": ((".cpp", ".cc", ".cxx", ".h", ".hpp"), "fmt"),
    "objc": ((".m", ".mm"), "afnetworking"),
    "swift": ((".swift",), "alamofire"),
    "zig": ((".zig",), "zig-std"),
    "shell": ((".sh", ".bash"), "nvm"),
}
# The languages whose full-corpus differential has every difference triaged into a
# rulings row; the rest are added as their members are.
CORPUS_LANGUAGES = ("c", "go", "java", "rust", "shell", "swift", "zig")
GROUPS = {"spans": analysis_treesitter.SPAN, "ccn": analysis_treesitter.COUNTS,
          "cognitive": analysis_treesitter.COGNITIVE, "nesting": analysis_treesitter.NESTING,
          "sizes": analysis_treesitter.SIZES}


def of_language(files: dict, language: str) -> dict:
    suffixes = LANGUAGES[language][0]
    return {path: data for path, data in files.items() if path.lower().endswith(suffixes)}


def check(files: dict, measured, group: str, name: str) -> analysis_treesitter.Outcome:
    outcome = analysis_treesitter.compare(files, measured, GROUPS[group])
    runlog.note("skipped_files", oracle=f"tree-sitter {group} {name}: set aside",
                count=sum(outcome.set_aside.values()))
    assert outcome.problems == []
    return outcome
