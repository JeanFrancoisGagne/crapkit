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

_run = cli_runner(timeout=120, encoding="utf-8", errors="replace")

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
        _rpc(2, "tools/call", {"name": "explain",
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


def test_a_utf16_marks_file_names_itself_and_the_fix(scored_repo: Path):
    assert _run(scored_repo, "ratchet", "seed").returncode == 0
    marks = scored_repo / MARKS
    marks.write_bytes(_utf16(marks.read_text(encoding="utf-8")))

    res = _run(scored_repo, "verify", "--reuse-artifacts")

    assert res.returncode == 3, res.stderr
    assert (f"crapkit: {MARKS} is not UTF-8 (first bytes ff fe = UTF-16, the "
            "PowerShell 5.1 Out-File default); save it as UTF-8") in res.stderr, res.stderr


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


def test_a_utf16_merge_side_names_itself_and_the_fix(tmp_path: Path):
    """A bare `Out-File` on OURS died as `UnicodeDecodeError: 'utf-8' codec
    can't decode byte 0xff in position 0`, exit 1: the traceback every other
    reader of the marks file already turns into a sentence."""
    _merge_sides(tmp_path, _utf16(_marks("src/a.ts\tf( )\t30.0000\n")))

    res = _run(tmp_path, "ratchet", "merge", "base.tsv", "ours.tsv", "theirs.tsv")

    assert res.returncode == 3, res.stderr
    assert ("crapkit: ours.tsv is not UTF-8 (first bytes ff fe = UTF-16, the PowerShell 5.1 "
            "Out-File default); save it as UTF-8") in res.stderr, res.stderr
    assert "Traceback" not in res.stderr
    assert (tmp_path / "ours.tsv").read_bytes().startswith(b"\xff\xfe"), \
        "a refused merge must not rewrite ours"
