"""The file types crapkit measures, one tuple of suffixes per language.

Imports nothing. `crapkit claude-hook` screens every edit against this map
before it imports anything else of crapkit's, so an edit to a file crapkit
never measures costs a stat and no config parse.
"""

LANGUAGE_EXTENSIONS = {
    "typescript": (".ts",),
    "tsx": (".tsx",),
    "javascript": (".js", ".jsx", ".mjs", ".cjs"),
    "python": (".py",),
    "swift": (".swift",),
    "go": (".go",),
    "rust": (".rs",),
    "shell": (".sh", ".bash"),
    # Exactly lizard's CLikeReader.ext, and deliberately not one suffix more.
    # `.hh`, `.hxx` and `.ipp` are real C++ headers that no lizard reader
    # declares, and lizard answers an undeclared suffix with `get_reader_for(f)
    # or CLikeReader` — a silent fallback, right for those three by luck and
    # wrong for anything else. Every claim here rests on a declared mapping;
    # those three are an opt-in for whoever needs them.
    "cpp": (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"),
    "objectivec": (".m", ".mm"),
    "vue": (".vue",),
    "java": (".java",),
    "zig": (".zig",),
    "powershell": (".ps1", ".psm1"),
}
