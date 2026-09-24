"""No reader in src/ decodes bytes from outside crapkit with a strict codec.

git's free text, a runner's report, an MCP frame, a source file and a name the
OS hands over can each hold a byte that is not UTF-8. A strict decode of one
ended a command with a UnicodeDecodeError, or on Windows with an
AttributeError after the decode failed in subprocess's reader thread; the
utf8-author hunt found such a read in 30 places. Each kind of source now has
one rule in one module: textcodec (git's free text, a runner's output, OS
text, source files), repotext (a file the repository owns and crapkit must
read exactly) and gitpaths (a path git names). This scan fails on any other
strict read, so a new one has to choose a rule or say here why its bytes are
crapkit's own, git's own, or ASCII by construction.

The shapes: a subprocess pipe opened as text (text=True, universal_newlines,
or an encoding) with no errors policy; `.decode()` with a codec and no errors
policy; a strict incremental decoder; `read_text()` with no errors policy;
and `open()` in a text read mode with no errors policy.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "crapkit"
RULE_HOMES = frozenset({"textcodec.py", "repotext.py", "gitpaths.py"})
SUBPROCESS_CALLS = frozenset({"run", "Popen", "check_output", "call", "check_call"})

# (file under src/crapkit, enclosing function, shape): why the strict read is right.
ALLOWED = {
    ("_process_owner.py", "_external_owner", "text pipe"):
        "the owner process crapkit starts writes json.dumps output, which is ASCII",
    ("_process_owner.py", "_group_active", "text pipe"):
        "`ps -o pgid= -o stat=` prints group ids and state letters, ASCII in every locale",
    ("analyze.py", "_characters", "decode"):
        "a probe: the UnicodeDecodeError it raises picks the cp1252 reading",
    ("analyze.py", "load_cache", "open"):
        "crapkit's own analysis cache; a torn or foreign byte is a ValueError that reads as a miss",
    ("analyze.py", "_load_stamps", "open"):
        "crapkit's own stamp index; a torn or foreign byte is a ValueError that reads as no index",
    ("churn_cache.py", "_read_doc", "read_text"):
        "crapkit's own churn cache; a ValueError reads as a miss",
    ("churn_log.py", "_read_key", "read_text"):
        "crapkit's own churn log key; a ValueError reads as a miss",
    ("coupling_cache.py", "_read_doc", "read_text"):
        "crapkit's own coupling cache; a ValueError reads as a miss",
    ("lanes.py", "read_stamps", "read_text"):
        "crapkit's own stamp file; a ValueError reads as no stamps",
    ("mutate_pool.py", "_temporary_trees", "read_text"):
        "the pool receipt crapkit wrote with json.dumps",
    ("store.py", "_inflate", "decode"):
        "a lane record crapkit encoded as UTF-8 and deflated itself",
    ("cli/admin.py", "_graph_files", "read_text"):
        "git's commit-graph-chain: one hex hash per line",
    ("gitio.py", "_file_text", "read_text"):
        "a ref file git wrote; a UnicodeDecodeError falls back to asking git",
    ("gitio.py", "_patch_sides", "decode"):
        "hex object ids a regular expression matched",
    ("packet.py", "_windows_encoded", "decode"):
        "base64 output, ASCII by construction",
    ("covstream.py", "__init__", "incremental decoder"):
        "a coverage report crapkit cannot decode is a lane refusal naming it (covstream._guarded)",
    ("cli/admin.py", "_plugin_json", "read_text"):
        "a plugin manifest; a byte that is not UTF-8 is a ValueError that names the file",
    ("cli/admin.py", "_probed_cli_version", "text pipe"):
        "a launcher's --version answer; it goes once doctor reads that answer as bytes",
}


def _name(call: ast.Call) -> str:
    func = call.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def _keyword(call: ast.Call, name: str):
    return next((k.value for k in call.keywords if k.arg == name), None)


def _has_errors(call: ast.Call) -> bool:
    """An errors policy, by keyword or as the second positional argument of
    decode or read_text."""
    return _keyword(call, "errors") is not None or (_name(call) in ("decode", "read_text") and len(call.args) > 1)


def _is_true(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _opens_text(call: ast.Call) -> bool:
    return any(_is_true(_keyword(call, flag)) for flag in ("text", "universal_newlines"))


def _encodes(call: ast.Call) -> bool:
    return _name(call) in SUBPROCESS_CALLS and _keyword(call, "encoding") is not None


def _text_pipe(call: ast.Call) -> str | None:
    return "text pipe" if _opens_text(call) or _encodes(call) else None


def _codec_decode(call: ast.Call) -> str | None:
    """`.decode()` or `.decode("utf-8")`; an incremental decoder's
    `.decode(data)` takes bytes, and its policy was set when it was made."""
    named = not call.args or isinstance(call.args[0], ast.Constant)
    return "decode" if isinstance(call.func, ast.Attribute) and _name(call) == "decode" and named else None


def _strict_decoder(call: ast.Call) -> str | None:
    """`codecs.getincrementaldecoder("utf-8")()` with no errors policy."""
    maker = call.func
    made = isinstance(maker, ast.Call) and _name(maker) == "getincrementaldecoder"
    return "incremental decoder" if made and not call.args else None


def _mode_argument(call: ast.Call):
    """The mode of `open(path, mode)` or `Path.open(mode)` as written."""
    at = 0 if isinstance(call.func, ast.Attribute) else 1
    return call.args[at] if len(call.args) > at else _keyword(call, "mode")


def _mode(call: ast.Call) -> str | None:
    """The open mode; None when it is not a constant, as `os.open(path, flags)` is not."""
    given = _mode_argument(call)
    if given is None:
        return "r"
    return given.value if isinstance(given, ast.Constant) and isinstance(given.value, str) else None


def _reads_text(mode: str | None) -> bool:
    return mode is not None and "b" not in mode and ("r" in mode or "+" in mode)


def _text_file(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute) and _name(call) == "read_text":
        return "read_text"
    return "open" if _name(call) == "open" and _reads_text(_mode(call)) else None


SHAPES = (_text_pipe, _codec_decode, _strict_decoder, _text_file)


def strict_shape(call: ast.Call) -> str | None:
    """The strict read this call makes, or None."""
    if _has_errors(call):
        return None
    return next(filter(None, (shape(call) for shape in SHAPES)), None)


def _enclosing(node: ast.AST, parents: dict) -> str:
    while node in parents and not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        node = parents[node]
    return getattr(node, "name", "<module>")


def _parents(tree: ast.AST) -> dict:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _shaped_calls(tree: ast.AST):
    return ((node, strict_shape(node)) for node in ast.walk(tree) if isinstance(node, ast.Call))


def reads_in(rel: str, source: bytes) -> list[tuple[str, str, str]]:
    """Every strict read in one module, as (file, enclosing function, shape)."""
    tree = ast.parse(source)
    parents = _parents(tree)
    return [(rel, _enclosing(call, parents), shape) for call, shape in _shaped_calls(tree) if shape]


def strict_reads(root: Path) -> list[tuple[str, str, str]]:
    found = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel not in RULE_HOMES:
            found += reads_in(rel, path.read_bytes())
    return found


def test_no_reader_in_src_decodes_outside_bytes_strictly():
    unlisted = sorted(set(strict_reads(SRC)) - set(ALLOWED))

    assert not unlisted, (
        "a strict read of bytes crapkit may not have written; read it through textcodec, repotext or "
        "gitpaths, or add it to ALLOWED with the reason its bytes are crapkit's, git's or ASCII:\n"
        + "\n".join(f"  {site}" for site in unlisted))


def test_each_rule_home_the_scan_skips_is_there():
    assert all((SRC / home).is_file() for home in RULE_HOMES)


STRICT = [
    ('subprocess.run(["git"], capture_output=True, text=True)', "text pipe"),
    ('subprocess.Popen(["git"], universal_newlines=True)', "text pipe"),
    ('subprocess.run(["git"], capture_output=True, encoding="utf-8")', "text pipe"),
    ('raw.decode("utf-8")', "decode"),
    ("raw.decode()", "decode"),
    ('codecs.getincrementaldecoder("utf-8")()', "incremental decoder"),
    ('path.read_text(encoding="utf-8")', "read_text"),
    ("path.read_text()", "read_text"),
    ('open(path, encoding="utf-8")', "open"),
    ("path.open()", "open"),
    ('path.open("r+", encoding="utf-8")', "open"),
]
LENIENT = [
    'subprocess.run(["git"], capture_output=True, text=True, errors="replace")',
    'subprocess.run(["git"], capture_output=True)',
    'raw.decode("utf-8", "replace")',
    'raw.decode("utf-8", errors="surrogateescape")',
    "decoder.decode(chunk)",
    'codecs.getincrementaldecoder("utf-8")("replace")',
    'path.read_text(encoding="utf-8", errors="replace")',
    'path.read_text("utf-8", "replace")',
    'path.open("rb")',
    'open(path, "wb")',
    'path.open("w", encoding="utf-8")',
    "os.open(path, os.O_RDONLY)",
    "read_text(path)",
]


@pytest.mark.parametrize("line, shape", STRICT, ids=[shape + ": " + line for line, shape in STRICT])
def test_the_scan_finds_a_new_strict_reader(line, shape):
    source = f"def reader(path, raw):\n    return {line}\n".encode()

    assert reads_in("new.py", source) == [("new.py", "reader", shape)]


@pytest.mark.parametrize("line", LENIENT)
def test_the_scan_passes_a_read_with_a_policy_or_in_bytes(line):
    assert reads_in("new.py", f"def reader(path, raw, chunk, decoder):\n    return {line}\n".encode()) == []
