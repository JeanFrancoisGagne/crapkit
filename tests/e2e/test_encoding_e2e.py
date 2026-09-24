"""A hostile console encoding must never crash crapkit, eat its exit code, or
turn a non-ASCII path into silence.

Git hooks and CI runners pipe output through whatever codec the host picked
(cp1252 on Windows, sometimes ascii). Python's stderr is backslashreplace by
default, but STDOUT under PYTHONIOENCODING=ascii is strict: a report line with
typographic punctuation would turn a clean run into a traceback. The same
codepage sits on STDIN, where Claude Code and MCP clients write UTF-8, so a
payload naming `pkg/café.py` used to reach the hook as a path that does not
exist. And PowerShell 5.1 writes crapkit.toml, the marks file and a hook script
with a BOM (`Out-File -Encoding utf8`) or as UTF-16 (`Out-File` bare), which the
strict readers turned into tracebacks and `does not parse`.

Every case here runs the real process under the codepage a Windows shell would
hand it (PYTHONIOENCODING sets all three streams before crapkit starts), so a
Linux CI runner exercises the Windows failure too.
"""
import json
import subprocess
from pathlib import Path

import pytest

from conftest import cli_runner, git_commit_all, git_init_repo

# The child's own stdio encoding is what this file tests.
_run = cli_runner(timeout=120, encoding="utf-8", errors="replace", spawn=True)

CP1252 = {"PYTHONIOENCODING": "cp1252"}

_BRANCHES = "".join(f"    if n == {i}:\n        n += {i}\n" for i in range(1, 8))
BREACH = f"def sprawl(n):\n{_BRANCHES}    return n\n"  # ccn 8, over the ceiling of 6
PLAIN = "def f(x):\n    return x + 1\n"

PY_TOML = ('[crapkit]\ntarget = 6\n\n'
           '[[scope]]\nname = "pkg"\npaths = ["pkg"]\nlanguages = ["python"]\n')

TS_TOML = (
    '[crapkit]\ntarget = 6\n\n'
    '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n\n'
    '[[lane]]\nname = "unit"\ncommand = "python make_cov.py"\n'
    'artifact = "cov.json"\nparser = "istanbul"\nscopes = ["src"]\n'
)

MAKE_COV = (
    "import json, os\n"
    "app = os.path.join(os.getcwd(), 'src', 'app.ts')\n"
    "cov = {app: {'path': app,\n"
    "             'fnMap': {'0': {'name': 'tiny', 'decl': {'start': {'line': 1}},\n"
    "                             'loc': {'start': {'line': 1}, 'end': {'line': 1}}}},\n"
    "             'f': {'0': 1}, 'branchMap': {}, 'b': {}}}\n"
    "json.dump(cov, open('cov.json', 'w'))\n"
)

APP = "export function tiny(a: number) { return a; }\n"
TANGLED = (
    "export function tangled(a: number, b: number): number {\n"
    "  let r = 0;\n"
    "  if (a > 0) { if (b > 0) { r = 1; } else if (b < -5) { r = 2; } }\n"
    "  if (a > 10 && b > 10) { r += 3; }\n"
    "  if (a < -1) { r -= 1; } else if (b === 0) { r -= 2; }\n"
    "  return r;\n}\n"
)

MARKS = "crapkit-ratchet.tsv"
NOT_UTF8 = ("crapkit: crapkit.toml is not UTF-8 (first bytes ff fe = UTF-16, the "
            "PowerShell 5.1 Out-File default); save it as UTF-8")


def _utf16(text: str) -> bytes:
    """What a bare `Out-File` writes: UTF-16 LE behind its byte-order mark."""
    return b"\xff\xfe" + text.encode("utf-16-le")


def _py_repo(tmp_path: Path, toml_bytes: bytes = PY_TOML.encode("utf-8")) -> Path:
    """A committed Python repo whose one tracked source file has a non-ASCII name."""
    (tmp_path / "crapkit.toml").write_bytes(toml_bytes)
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "café.py").write_text(PLAIN, encoding="utf-8")
    git_init_repo(tmp_path)
    git_commit_all(tmp_path, "init")
    return tmp_path


@pytest.fixture()
def scored_repo(tmp_path: Path) -> Path:
    """A TypeScript repo with one istanbul lane and one trusted coverage run."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text(APP, encoding="utf-8")
    (tmp_path / "src" / "tangled.ts").write_text(TANGLED, encoding="utf-8")
    (tmp_path / "crapkit.toml").write_text(TS_TOML, encoding="utf-8")
    (tmp_path / "make_cov.py").write_text(MAKE_COV, encoding="utf-8")
    (tmp_path / ".gitignore").write_text(".crapkit/\ncov.json\n", encoding="utf-8")
    git_init_repo(tmp_path)
    git_commit_all(tmp_path, "init")
    res = _run(tmp_path, "coverage")
    assert res.returncode == 0, res.stdout + res.stderr
    return tmp_path


def test_stdout_reports_survive_an_ascii_only_console(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("export function f(a: number) { return a ? 1 : 2; }\n",
                                             encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"],
                   cwd=tmp_path, check=True, capture_output=True)
    assert _run(tmp_path, "init").returncode == 0

    # doctor's no-lane note carries an em dash; strict ascii stdout would explode
    res = _run(tmp_path, "doctor", env_extra={"PYTHONIOENCODING": "ascii"})
    assert res.returncode == 0, res.stdout + res.stderr
    assert "note" in res.stdout
    assert "Traceback" not in res.stderr


# --- stdin is UTF-8 whatever the console's codepage ------------------------------

def test_the_advisory_hook_reads_a_utf8_payload_under_a_cp1252_stdin(tmp_path: Path):
    """Windows dev, code page 437: a ccn-8 edit in `pkg/café.py` exited 0 with
    no output, and the same payload under PYTHONIOENCODING=utf-8 exited 2 with
    the advisory. The path decoded in the locale codepage named no file."""
    repo = _py_repo(tmp_path)
    edited = repo / "pkg" / "café.py"
    edited.write_text(BREACH, encoding="utf-8")  # an untracked-content change: judged in full
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Edit",
               "tool_input": {"file_path": str(edited)}, "cwd": str(repo)}

    res = _run(repo, "claude-hook", "--protocol", "1", env_extra=CP1252,
               stdin=json.dumps(payload, ensure_ascii=False))

    assert res.returncode == 2, res.stderr
    assert "pkg/café.py" in res.stderr, res.stderr
    assert res.stdout == ""


def _rpc(msg_id: int, method: str, params: dict | None = None) -> str:
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        msg["params"] = params
    return json.dumps(msg, ensure_ascii=False)  # raw UTF-8 on the wire, as clients send it


def test_the_mcp_server_reads_a_utf8_request_under_a_cp1252_stdin(tmp_path: Path):
    """`brief` over MCP answered `no function named 'f' in pkg/cafÃ©.py ... it
    holds: nothing`: the server iterated stdin in the locale codepage."""
    repo = _py_repo(tmp_path)
    assert _run(repo, "inventory").returncode == 0
    requests = [
        _rpc(1, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}),
        _rpc(2, "tools/call", {"name": "get_function_history",
                               "arguments": {"path": "pkg/café.py", "name": "f"}}),
    ]

    res = _run(repo, "mcp", "--repo", str(repo), env_extra=CP1252,
               stdin="\n".join(requests) + "\n")

    assert res.returncode == 0, res.stderr
    replies = {m["id"]: m for m in map(json.loads, res.stdout.strip().splitlines())}
    call = replies[2]["result"]
    assert call["isError"] is False, call
    assert "pkg/café.py" in json.dumps(call["structuredContent"], ensure_ascii=False)


# --- repository text tolerates a BOM and names UTF-16 --------------------------------

def test_a_configuration_saved_with_a_bom_is_the_same_configuration(tmp_path: Path):
    """`Out-File -Encoding utf8` under PowerShell 5.1 writes a BOM, which tomllib
    read as `Invalid statement (at line 1, column 1)`."""
    repo = _py_repo(tmp_path, PY_TOML.encode("utf-8-sig"))

    doctor = _run(repo, "doctor")
    inventory = _run(repo, "inventory", "--json")

    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert "does not parse" not in doctor.stderr
    assert inventory.returncode == 0, inventory.stderr
    assert json.loads(inventory.stdout)["functions"] == 1


def test_a_utf16_configuration_is_a_configuration_error_that_names_the_fix(tmp_path: Path):
    """A bare `Out-File` writes UTF-16 LE; the strict read died with a raw
    UnicodeDecodeError traceback at exit 1."""
    repo = _py_repo(tmp_path, _utf16(PY_TOML))

    res = _run(repo, "inventory")

    assert res.returncode == 3, res.stderr
    assert NOT_UTF8 in res.stderr, res.stderr
    assert "Traceback" not in res.stderr


def test_a_marks_file_saved_with_a_bom_holds_the_same_marks(scored_repo: Path):
    """The BOM made the stamp line unreadable (`carries no metric stamp`) and
    then the header a data row (`line 1 has 1 fields, expected 3`), so verify
    failed and `ratchet seed` refused the file it had written itself."""
    assert _run(scored_repo, "ratchet", "seed").returncode == 0
    marks = scored_repo / MARKS
    marks.write_bytes(b"\xef\xbb\xbf" + marks.read_bytes())

    verify = _run(scored_repo, "verify", "--reuse-artifacts")
    seed = _run(scored_repo, "ratchet", "seed")

    assert (verify.returncode, verify.stderr) == (0, ""), verify.stdout + verify.stderr
    assert seed.returncode == 0, seed.stderr
    assert "added 0, tightened 0 - 1 mark(s) vs run " in seed.stdout, seed.stdout


# --- the marks file reads by its own mark, else UTF-8 with U+FFFD (Q20) ---------------
#
# A rewrite of a file whose read replaced a byte would save U+FFFD in place of the
# name that held it, or drop the mark as a function that is gone, so every writer
# refuses it by the byte. A UTF-16 file is written back as UTF-16, behind its mark
# and in its own line ending, which is how PowerShell 5.1 saved it.

CP1252_ROW = b"src/app.ts\tcaf\xe9( )\t50.0000\n"


def _with_cp1252_mark(data: bytes) -> bytes:
    """A second mark, its name saved by a cp1252 editor, in the row order a
    rewrite would give it: `src/app.ts` sorts before `src/tangled.ts`."""
    head, _, rows = data.partition(b"crap\n")
    return head + b"crap\n" + CP1252_ROW + rows


def _raised(data: bytes) -> bytes:
    """The last mark's number raised, so the next seed or tighten rewrites the file."""
    lines = data.split(b"\n")
    path, name, _ = lines[-2].split(b"\t")
    lines[-2] = b"\t".join((path, name, b"999.0000"))
    return b"\n".join(lines)


def _utf16_crlf(data: bytes) -> bytes:
    """What `Get-Content` piped to a bare `Out-File` leaves in PowerShell 5.1."""
    return _utf16(data.decode("utf-8").replace("\n", "\r\n"))


MARKS_SHAPES = {
    "cp1252-name": _with_cp1252_mark,
    "cp1252-name-raised": lambda seeded: _raised(_with_cp1252_mark(seeded)),
    "utf16-le-crlf": _utf16_crlf,
    "utf16-le-crlf-raised": lambda seeded: _utf16_crlf(_raised(seeded)),
    "utf16-be": lambda seeded: b"\xfe\xff" + seeded.decode("utf-8").encode("utf-16-be"),
    "utf8-bom": lambda seeded: b"\xef\xbb\xbf" + seeded,
}


def _marks_as(scored_repo: Path, shape: str) -> bytes:
    """Seed, then leave the marks file in `shape`; returns its bytes."""
    assert _run(scored_repo, "ratchet", "seed").returncode == 0
    marks = scored_repo / MARKS
    marks.write_bytes(MARKS_SHAPES[shape](marks.read_bytes()))
    return marks.read_bytes()


READS = [
    # id, shape, command, a line stdout carries
    ("verify-cp1252", "cp1252-name", ("verify", "--reuse-artifacts"), "verify"),
    ("report-cp1252", "cp1252-name", ("ratchet", "report"), "2 open mark(s)"),
    ("report-json-cp1252", "cp1252-name", ("ratchet", "report", "--json"), "caf\\ufffd( )"),
    ("brief-cp1252", "cp1252-name", ("brief", "src/tangled.ts", "tangled", "--json"), "ratchet_mark"),
    ("verify-utf16", "utf16-le-crlf", ("verify", "--reuse-artifacts"), "verify"),
    ("report-utf16", "utf16-le-crlf", ("ratchet", "report"), "1 open mark(s)"),
    ("report-utf16-be", "utf16-be", ("ratchet", "report"), "1 open mark(s)"),
    ("brief-utf16", "utf16-le-crlf", ("brief", "src/tangled.ts", "tangled", "--json"), "ratchet_mark"),
    ("verify-bom", "utf8-bom", ("verify", "--reuse-artifacts"), "verify"),
]


@pytest.mark.parametrize("shape, command, said", [row[1:] for row in READS],
                         ids=[row[0] for row in READS])
def test_a_marks_file_in_any_encoding_reads_as_its_rows(scored_repo: Path, shape, command, said):
    """A cp1252 file used to refuse at exit 3 in every command and a UTF-16 one
    too; with nothing to rewrite, both now read, and the file keeps its bytes."""
    before = _marks_as(scored_repo, shape)

    res = _run(scored_repo, *command)

    assert res.returncode == 0, res.stdout + res.stderr
    assert said in res.stdout, res.stdout
    assert "is not UTF-8" not in res.stderr, res.stderr
    assert (scored_repo / MARKS).read_bytes() == before, "a read must not rewrite the marks"


REFUSED_WRITES = [
    # id, a command that would rewrite a marks file whose read replaced byte e9
    ("seed", ("ratchet", "seed")),
    ("verify-tighten", ("verify", "--reuse-artifacts")),
    ("prune", ("ratchet", "prune")),
    ("move", ("ratchet", "move", "src/app.ts", "src/moved.ts")),
]


@pytest.mark.parametrize("command", [row[1] for row in REFUSED_WRITES],
                         ids=[row[0] for row in REFUSED_WRITES])
def test_a_rewrite_that_would_save_u_fffd_refuses_by_the_byte(scored_repo: Path, command):
    """seed and the tighten keep the `caf\\ufffd( )` mark and would save it in
    place of `café( )`; prune would drop it as a function that is gone, and
    move would carry it. The file is left as it was."""
    before = _marks_as(scored_repo, "cp1252-name-raised")
    offset = before.index(b"\xe9")

    res = _run(scored_repo, *command)

    assert res.returncode == 3, res.stdout + res.stderr
    assert (f"crapkit: {MARKS} holds byte e9 at offset {offset}, which reads as U+FFFD, and "
            "rewriting the file would save U+FFFD in its place; save it as UTF-8 and rerun; "
            "file left unchanged") in res.stderr, res.stderr
    assert (scored_repo / MARKS).read_bytes() == before


@pytest.mark.parametrize("command", [("ratchet", "seed"), ("verify", "--reuse-artifacts")],
                         ids=["seed", "verify-tighten"])
def test_a_utf16_marks_file_is_rewritten_as_utf16_in_its_own_line_ending(scored_repo: Path, command):
    """A UTF-8 rewrite would hand PowerShell 5.1 a file its next `Get-Content`
    reads as cp1252."""
    _marks_as(scored_repo, "utf16-le-crlf-raised")

    res = _run(scored_repo, *command)

    assert res.returncode == 0, res.stdout + res.stderr
    written = (scored_repo / MARKS).read_bytes()
    assert written.startswith(b"\xff\xfe"), written[:8]
    text = written[2:].decode("utf-16-le")
    assert text.count("\n") == text.count("\r\n") > 0, "CRLF kept on every line"
    assert "999.0000" not in text, "the tighten reached the file"


@pytest.mark.parametrize("body", [_utf16(PY_TOML), (PY_TOML + "# caf\xe9\n").encode("cp1252")],
                         ids=["utf16", "cp1252"])
def test_crapkit_toml_keeps_its_refusal_whatever_the_marks_rule(tmp_path: Path, body):
    """Q20 is the marks file's rule alone: crapkit.toml is parsed as TOML, and a
    byte read as U+FFFD there would be a setting nobody wrote."""
    res = _run(_py_repo(tmp_path, body), "inventory")

    assert res.returncode == 3, res.stderr
    assert "crapkit: crapkit.toml is not UTF-8 (" in res.stderr, res.stderr


def _hook_marks(shape: str) -> bytes:
    """A mark on `sprawl( n )` in pkg/café.py, beside a cp1252 mark on another
    function when the shape asks for one."""
    from crapkit.ratchet import RatchetEntry, dump_ratchet, metric_version

    rows = dump_ratchet([RatchetEntry("pkg/café.py", "sprawl( n )", 72.0)], key_version=1,
                        stamp=metric_version()).encode("utf-8")
    return {"none": b"", "utf8": rows, "cp1252-neighbour": rows + b"pkg/caf\xe9.py\tcaf\xe9( )\t9.0\n",
            "utf16-le-crlf": _utf16_crlf(rows),
            "utf16-be": b"\xfe\xff" + rows.decode("utf-8").encode("utf-16-be")}[shape]


@pytest.mark.parametrize("shape", ["none", "utf8", "cp1252-neighbour", "utf16-le-crlf", "utf16-be"])
def test_the_advisory_reads_its_marks_by_the_marks_rule(tmp_path: Path, shape):
    """A UTF-16 or cp1252 marks file stopped the advisory at its strict read,
    and the catch-all turned that into silence about every function in the
    edit, the unmarked one included. The advisory now reads the file as every
    other command does: the marked function stays quiet, the other is named."""
    repo = _py_repo(tmp_path)
    edited = repo / "pkg" / "café.py"
    edited.write_text(BREACH + "\n\n" + BREACH.replace("sprawl", "unmarked"), encoding="utf-8")
    if shape != "none":
        (repo / MARKS).write_bytes(_hook_marks(shape))
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Edit",
               "tool_input": {"file_path": str(edited)}, "cwd": str(repo)}

    res = _run(repo, "claude-hook", "--protocol", "1", stdin=json.dumps(payload))

    assert res.returncode == 2, res.stderr
    assert "unmarked( n )" in res.stderr, res.stderr
    assert ("sprawl( n )" in res.stderr) == (shape == "none"), res.stderr


# --- doctor names a hook file git cannot spawn ---------------------------------------

HOOK = b"#!/bin/sh\nexec python -m crapkit hook-precommit\n"


def _doctor_lines(repo: Path) -> list[str]:
    res = _run(repo, "doctor")
    assert res.returncode == 0, res.stdout + res.stderr
    return [ln for ln in res.stdout.splitlines() if "cannot spawn" in ln]


def test_doctor_warns_on_a_hook_that_starts_with_a_bom(tmp_path: Path):
    """git answers `cannot spawn .git/hooks/pre-commit` and the commit goes
    through ungated; doctor passed that file without a word."""
    repo = _py_repo(tmp_path)
    (repo / ".git" / "hooks" / "pre-commit").write_bytes(b"\xef\xbb\xbf" + HOOK)

    lines = _doctor_lines(repo)

    assert lines == ["WARN .git/hooks/pre-commit starts with a UTF-8 byte-order mark "
                     "(ef bb bf), which git cannot spawn; rewrite it as ASCII "
                     "(PowerShell: Set-Content -Encoding ascii)"], lines


def test_doctor_warns_on_a_utf16_hook(tmp_path: Path):
    repo = _py_repo(tmp_path)
    (repo / ".git" / "hooks" / "pre-commit").write_bytes(_utf16(HOOK.decode()))

    lines = _doctor_lines(repo)

    assert lines == ["WARN .git/hooks/pre-commit starts with a UTF-16 byte-order mark "
                     "(ff fe, the PowerShell 5.1 Out-File default), which git cannot spawn; "
                     "rewrite it as ASCII (PowerShell: Set-Content -Encoding ascii)"], lines


def test_doctor_reads_the_hook_under_core_hooks_path(tmp_path: Path):
    repo = _py_repo(tmp_path)
    (repo / "githooks").mkdir()
    (repo / "githooks" / "pre-commit").write_bytes(b"\xef\xbb\xbf" + HOOK)
    subprocess.run(["git", "config", "core.hooksPath", "githooks"], cwd=repo, check=True,
                   capture_output=True)

    lines = _doctor_lines(repo)

    assert len(lines) == 1 and lines[0].startswith("WARN githooks/pre-commit starts with"), lines


def test_a_plain_hook_and_a_missing_one_draw_no_encoding_warning(tmp_path: Path):
    repo = _py_repo(tmp_path)
    assert _doctor_lines(repo) == []
    (repo / ".git" / "hooks" / "pre-commit").write_bytes(HOOK)
    assert _doctor_lines(repo) == []


# --- the one-liners a shell captures are ASCII -------------------------------------------

def _non_ascii(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if not ln.isascii()]


def test_the_lines_a_shell_captures_are_ascii(scored_repo: Path, tmp_path: Path):
    """`$x = crapkit worklist` under code page 437 captured `ΓÇö` where the
    header's separator was; the same for the coverage line, init's next step,
    the seed line, verify's OK line and the watch banner."""
    seeded = _run(scored_repo, "ratchet", "seed")
    captured = {
        "coverage": _run(scored_repo, "coverage"),
        "worklist": _run(scored_repo, "worklist"),
        "ratchet seed": seeded,
        "verify": _run(scored_repo, "verify", "--reuse-artifacts"),
        "watch": _run(scored_repo, "watch", "--cycles", "0"),
    }
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    (fresh / "pkg").mkdir()
    (fresh / "pkg" / "mod.py").write_text(PLAIN, encoding="utf-8")
    (fresh / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")  # so init detects a lane
    git_init_repo(fresh)
    git_commit_all(fresh, "init")
    captured["init"] = _run(fresh, "init")

    for command, res in captured.items():
        assert res.returncode == 0, (command, res.stdout, res.stderr)
        assert _non_ascii(res.stdout) == [], (command, res.stdout)
    assert " - 1 of 1 active (worklist_top 50), 0 dormant" in captured["worklist"].stdout
    assert f"{MARKS}: added 1, tightened 0 - 1 mark(s) vs run 1 (" in seeded.stdout
    assert " - next: run `" in captured["init"].stdout
    assert captured["watch"].stdout.startswith("watching 2 tracked files every 2.0s - 0 poll(s)")


# --- the merge driver reads its three sides the way every other reader does ------------

STAMP = "# crapkit-analysis=9 lizard=1.24.0\n"
HEADER = "path\tlong_name\tcrap\n"


def _marks(row: str) -> str:
    return STAMP + HEADER + row


def _merge_sides(tmp_path: Path, ours: bytes) -> None:
    """BASE, OURS and THEIRS as git hands them to the driver, OURS as given."""
    (tmp_path / "base.tsv").write_bytes(_marks("src/a.ts\tf( )\t50.0000\n").encode("utf-8"))
    (tmp_path / "ours.tsv").write_bytes(ours)
    (tmp_path / "theirs.tsv").write_bytes(_marks("src/a.ts\tf( )\t20.0000\n").encode("utf-8"))


def test_a_merge_side_saved_with_a_bom_merges_as_the_same_marks(tmp_path: Path):
    """OURS is the working copy PowerShell saved. Read strictly, the BOM hid its
    stamp and the driver refused two files carrying the same stamp as `ours is
    [unstamped] and theirs is [crapkit-analysis=9 ...]`, exit 3."""
    _merge_sides(tmp_path, b"\xef\xbb\xbf" + _marks("src/a.ts\tf( )\t30.0000\n").encode("utf-8"))

    res = _run(tmp_path, "ratchet", "merge", "base.tsv", "ours.tsv", "theirs.tsv")

    assert (res.returncode, res.stderr) == (0, ""), res.stdout + res.stderr
    assert res.stdout.strip() == "ratchet merge: 1 mark(s)"
    merged = (tmp_path / "ours.tsv").read_bytes()
    assert merged.startswith(STAMP.encode("utf-8")), "the merged file is written without the mark"
    assert merged.endswith(b"src/a.ts\tf( )\t20.0000\n"), "both changed: the merge keeps the min"


@pytest.mark.parametrize("mark, codec, newline", [
    (b"\xff\xfe", "utf-16-le", "\r\n"), (b"\xff\xfe", "utf-16-le", "\n"), (b"\xfe\xff", "utf-16-be", "\n"),
], ids=["utf16-le-crlf", "utf16-le-lf", "utf16-be"])
def test_a_utf16_merge_side_merges_and_stays_utf16(tmp_path: Path, mark, codec, newline):
    """A bare `Out-File` on OURS died as `UnicodeDecodeError`, exit 1, and then
    as a refusal to save the file as UTF-8, exit 3. OURS now reads as its rows
    and the merge writes it back as it was saved."""
    ours = _marks("src/a.ts\tf( )\t30.0000\n").replace("\n", newline)
    _merge_sides(tmp_path, mark + ours.encode(codec))

    res = _run(tmp_path, "ratchet", "merge", "base.tsv", "ours.tsv", "theirs.tsv")

    assert (res.returncode, res.stderr) == (0, ""), res.stdout + res.stderr
    merged = (tmp_path / "ours.tsv").read_bytes()
    assert merged == mark + _marks("src/a.ts\tf( )\t20.0000\n").replace("\n", newline).encode(codec)


CAFE_CP1252 = b"src/a.ts\tcaf\xe9( )\t70.0000\n"
CAFE_UTF8 = "src/a.ts\tcafé( )\t70.0000\n".encode()
F50, F30, F20 = (_marks(f"src/a.ts\tf( )\t{n}.0000\n").encode() for n in (50, 30, 20))


def _plus(marks: bytes, row: bytes) -> bytes:
    """`row` placed where a rewrite sorts it: `café( )` and `caf\\ufffd( )`
    both sort before `f( )`."""
    head, _, rows = marks.partition(b"crap\n")
    return head + b"crap\n" + row + rows


MERGES = [
    # id, base, ours, theirs, exit, the side a refusal names
    ("base-cp1252", _plus(F50, CAFE_CP1252), _plus(F30, CAFE_UTF8), _plus(F20, CAFE_UTF8), 0, None),
    ("all-sides-cp1252-merge-keeps-ours", _plus(F20, CAFE_CP1252), _plus(F20, CAFE_CP1252),
     _plus(F20, CAFE_CP1252), 0, None),
    ("ours-cp1252-name", F50, _plus(F30, CAFE_CP1252), F20, 3, "ours.tsv"),
    ("theirs-cp1252-name", F50, F30, _plus(F20, CAFE_CP1252), 3, "theirs.tsv"),
]


@pytest.mark.parametrize("base, ours, theirs, code, named", [row[1:] for row in MERGES],
                         ids=[row[0] for row in MERGES])
def test_a_cp1252_merge_side_merges_unless_the_merge_would_save_u_fffd(
        tmp_path: Path, base, ours, theirs, code, named):
    """BASE only tells each side's change apart, so a name it read as U+FFFD
    keys nothing ours or theirs holds and costs no mark. A merge that keeps
    OURS as it is writes nothing. A side whose name would reach the rewritten
    OURS as U+FFFD refuses, and git leaves the conflict for the rerun."""
    for side, data in (("base.tsv", base), ("ours.tsv", ours), ("theirs.tsv", theirs)):
        (tmp_path / side).write_bytes(data)

    res = _run(tmp_path, "ratchet", "merge", "base.tsv", "ours.tsv", "theirs.tsv")

    assert res.returncode == code, res.stdout + res.stderr
    written = (tmp_path / "ours.tsv").read_bytes()
    if named:
        offset = (base, ours, theirs)[["base.tsv", "ours.tsv", "theirs.tsv"].index(named)].index(b"\xe9")
        assert f"crapkit: {named} holds byte e9 at offset {offset}, which reads as U+FFFD" in res.stderr
        assert written == ours, "a refused merge must not rewrite ours"
    else:
        assert b"\xef\xbf\xbd" not in written, "U+FFFD never reaches the merged file"
        assert b"f( )\t20.0000" in written
