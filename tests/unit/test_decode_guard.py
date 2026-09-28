"""No reader in src/ or in the Action's comment builder decodes bytes from
outside crapkit with a strict codec, and none but repotext picks the policy a
lenient decode uses. tools/action/comment.py imports crapkit and reads what the
Action's steps wrote (git's error, the changed names), so the rule covers it.

git's free text, a runner's report, an MCP frame, a source file and a name the
OS hands over can each hold a byte that is not UTF-8. A strict decode of one
ended a command with a UnicodeDecodeError, or on Windows with an
AttributeError after the decode failed in subprocess's reader thread, and
30 places held such a read. Each kind of source now has
one rule, and every rule lives in repotext: a file the repository owns and
crapkit must read exactly, JSON, the marks file, git's free text and a
runner's output, a patch or name kept byte for byte, source files and OS text.
gitpaths frames a path git names and decodes it through repotext. The first
scan fails on any other strict read, so a new one has to name a kind or say
here why its bytes are crapkit's own, git's own, or ASCII by construction. The
second fails on a read that spells its own policy (`errors="replace"` and the
like) outside repotext: 23 such reads in 11 modules each chose a rule by hand,
so two readers of one kind of text could drift apart.

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
ACTION = Path(__file__).resolve().parents[2] / "tools" / "action"
RULE_HOMES = frozenset({"repotext.py"})
SUBPROCESS_CALLS = frozenset({"run", "Popen", "check_output", "call", "check_call"})

# (file under src/crapkit, enclosing function, shape): why the strict read is right.
ALLOWED = {
    ("_process_owner.py", "_external_owner", "text pipe"):
        "the owner process crapkit starts writes json.dumps output, which is ASCII",
    ("_process_owner.py", "_ps_group_active", "text pipe"):
        "`ps -o pgid= -o stat=` prints group ids and state letters, ASCII in every locale",
    ("_package.py", "installed_version", "read_text"):
        "crapkit's own __init__.py, whose version line is ASCII; a UnicodeError reads as no "
        "evidence of an upgrade",
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
    ("lane_stamps.py", "read", "read_text"):
        "crapkit's own stamp file; a ValueError reads as an unreadable one",
    ("mutate_pool.py", "_temporary_trees", "read_text"):
        "the pool receipt crapkit wrote with json.dumps",
    ("store.py", "_inflate", "decode"):
        "a lane record crapkit encoded as UTF-8 and deflated itself",
    ("cli/admin.py", "_graph_files", "read_text"):
        "git's commit-graph-chain: one hex hash per line",
    ("cli/claude_hook.py", "judged", "read_text"):
        "the hex digest claude-hook's session memory wrote; a ValueError reads as not judged",
    ("cli/claude_hook.py", "_named_git_dir", "read_text"):
        "the gitdir line git wrote in a .git file; a UnicodeDecodeError turns the session "
        "memory off, which changes no verdict",
    ("gitio.py", "_hashed", "decode"):
        "the hex object ids git hash-object prints",
    ("gitio.py", "_hashed_alone", "decode"):
        "the hex object id git hash-object prints",
    ("gitio.py", "_file_text", "read_text"):
        "a ref file git wrote; a UnicodeDecodeError falls back to asking git",
    ("gitio.py", "_patch_sides", "decode"):
        "hex object ids a regular expression matched",
    ("gitio.py", "_log_entry", "decode"):
        "a hex commit id a regular expression matched",
    ("gitpaths.py", "split_record", "decode"):
        "the mode, object id and stage fields of an ls-files record, ASCII by construction",
    ("packet.py", "_windows_encoded", "decode"):
        "base64 output, ASCII by construction",
}

# The reads no input reaches, each held by the scan or named here:
# lanes._still_failed: nothing called it, and it is deleted.
# procs._captured_text under the MCP server's _run_cli: the child is
#   crapkit, which writes a pipe in UTF-8 (the MCP frame rows in
#   tests/e2e/test_outside_files_and_frames_e2e.py run it).
# The owner's JSON channel and `ps`: the two _process_owner entries.
# commit-graph-chain: the cli/admin.py _graph_files entry.
# churn_log._encoded: an encode, not a read, of lines _git_lines
#   decoded with errors="replace", so no line holds a lone surrogate.
# crapkit's own caches: the analyze, churn, coupling, stamp and pool
#   receipt entries.
# gitpaths.unquote_path: an encode back to the bytes git quoted, not
#   a read; tests/unit/test_git_path_bytes.py holds its rule.
# The shell steps (action.yml, git-hooks/pre-commit, the plugin's
#   hooks.json and .mcp.json, the Dockerfile): each passes bytes on without a
#   codec, and the python:3.12-slim image sets LANG=C.UTF-8.


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
    `.decode(data)` takes bytes, `b""` included, and its policy was set when
    it was made."""
    named = not call.args or isinstance(getattr(call.args[0], "value", None), str)
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
        "a strict read of bytes crapkit may not have written; read it through a repotext kind, "
        "or add it to ALLOWED with the reason its bytes are crapkit's, git's or ASCII:\n"
        + "\n".join(f"  {site}" for site in unlisted))


def test_each_allowed_read_is_one_src_still_makes():
    """An entry left behind once its read turns lenient would excuse the next
    strict read someone writes in that function."""
    stale = sorted(set(ALLOWED) - set(strict_reads(SRC)))

    assert not stale, (
        "ALLOWED names a strict read src/ no longer makes; drop the entry:\n"
        + "\n".join(f"  {site}" for site in stale))


def test_the_action_builder_reads_through_repotext_kinds():
    """The builder spelled its own policies: `replace` for changed names and
    `ignore` for the cut request body, and a strict read of git's error."""
    found = strict_reads(ACTION) + policy_sites(ACTION)

    assert (ACTION / "comment.py").is_file()
    assert not found, (
        "the Action's builder reads bytes without a repotext kind; call the kind for its source:\n"
        + "\n".join(f"  {site}" for site in found))


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


# --- the policy scan: repotext is the one module that names a decode policy ---


def _decoder_policy(call: ast.Call) -> str | None:
    """`codecs.getincrementaldecoder("utf-8")("replace")`."""
    maker = call.func
    made = isinstance(maker, ast.Call) and _name(maker) == "getincrementaldecoder"
    return "incremental decoder" if made and call.args else None


def _stream_policy(call: ast.Call) -> str | None:
    """`io.TextIOWrapper(stream, errors=...)` over a pipe or a file."""
    return "text stream" if _name(call) == "TextIOWrapper" and _keyword(call, "errors") is not None else None


def _subprocess_pipe(call: ast.Call) -> str | None:
    return "text pipe" if _name(call) in SUBPROCESS_CALLS else None


def _read_policy(call: ast.Call) -> str | None:
    """A read the strict scan knows, spelled with an errors policy of its own."""
    if not _has_errors(call):
        return None
    return next(filter(None, (shape(call) for shape in (_subprocess_pipe, _codec_decode, _text_file))), None)


POLICIES = (_decoder_policy, _stream_policy, _read_policy)


def policy_shape(call: ast.Call) -> str | None:
    """The decode policy this call spells for itself, or None."""
    return next(filter(None, (policy(call) for policy in POLICIES)), None)


def policies_in(rel: str, source: bytes) -> list[tuple[str, str, str]]:
    """Every read in one module that names its own policy, as (file, enclosing function, shape)."""
    tree = ast.parse(source)
    parents = _parents(tree)
    return [(rel, _enclosing(node, parents), shape) for node in ast.walk(tree)
            if isinstance(node, ast.Call) and (shape := policy_shape(node))]


def policy_sites(root: Path) -> list[tuple[str, str, str]]:
    found = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel not in RULE_HOMES:
            found += policies_in(rel, path.read_bytes())
    return found


def test_no_module_but_repotext_spells_a_decode_policy():
    spelled = policy_sites(SRC)

    assert not spelled, (
        "a read that picks its own decode policy; call the repotext kind for its source "
        "(lenient, escaped, plain_utf8, ...) or add a kind there:\n"
        + "\n".join(f"  {site}" for site in spelled))


POLICY = [
    ('raw.decode("utf-8", "replace")', "decode"),
    ('raw.decode(errors="surrogateescape")', "decode"),
    ('path.read_text(encoding="utf-8", errors="replace")', "read_text"),
    ('path.read_text("utf-8", "replace")', "read_text"),
    ('open(path, encoding="utf-8", errors="replace")', "open"),
    ('subprocess.run(["git"], capture_output=True, text=True, errors="replace")', "text pipe"),
    ('subprocess.run(["git"], capture_output=True, errors="replace")', "text pipe"),
    ('io.TextIOWrapper(stream, encoding="utf-8", errors="replace")', "text stream"),
    ('codecs.getincrementaldecoder("utf-8")("replace")', "incremental decoder"),
]
NO_POLICY = [
    "lenient(raw)",
    "decoder.decode(chunk, True)",
    'decoder.decode(b"", True)',
    'path.open("w", encoding="utf-8", errors="replace")',
    'sys.stdout.reconfigure(encoding="utf-8", errors="replace")',
    'text.encode("utf-8", "replace")',
    'raw.decode("utf-8")',
    'io.TextIOWrapper(stream, encoding="utf-8")',
]


@pytest.mark.parametrize("line, shape", POLICY, ids=[shape + ": " + line for line, shape in POLICY])
def test_the_policy_scan_finds_a_read_that_names_its_own_policy(line, shape):
    source = f"def reader(path, raw, stream):\n    return {line}\n".encode()

    assert policies_in("new.py", source) == [("new.py", "reader", shape)]


@pytest.mark.parametrize("line", NO_POLICY)
def test_the_policy_scan_passes_a_kind_a_write_and_a_strict_read(line):
    source = f"def reader(path, raw, chunk, decoder, stream, text):\n    return {line}\n".encode()

    assert policies_in("new.py", source) == []
