"""Bytes from outside crapkit, in the encodings the per-reader tests leave open.

Each reader has its own encoding test beside it. What those tests do not reach:
a package.json init reads but does not need, a workspace package.json, a root
package.json init cannot read, a second init after one that stopped before
.gitignore, a character split across the MCP server's 64 KiB read, source bytes
with a declared code page, trailing NULs or a 2 MB line, the digest body
handed to alert_command, and Claude Code's plugin record under non-ASCII
directories.

package.json follows one rule. A UTF-8 BOM is read past, as npm reads it. A
root package.json in UTF-16, holding a byte that is not UTF-8, or not one JSON
object is refused by name, because the js lane comes from it. Any other package.json that cannot be
read is skipped with one warning naming it, because a fixture that init never
needed must not stop init.
"""
from __future__ import annotations

import codecs
import io
import json
import sys
import tomllib
from pathlib import Path

import pytest

from raw_git import checkout, commit, repository

import crapkit
from crapkit.analyze import analyze_one, decode_source
from crapkit.cli import admin
from crapkit.cli.parser import main
from crapkit.hook import staged_records
from crapkit.mcp_server import serve
from crapkit.score import ScoredRow
from crapkit.store import SnapshotStore

JS = b"export function f(x) {\n  if (x) { return 1; }\n  return 2;\n}\n"
RUNNER = {"name": "demo", "scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "^2.0.0"}}
WORKSPACES = {"name": "mono", "scripts": {"test": "npm run --workspaces test"}}
def _json(payload, encoding: str = "utf-8", prefix: bytes = b"") -> bytes:
    return prefix + json.dumps(payload, ensure_ascii=False).encode(encoding)


LATIN1_RUNNER = _json({**RUNNER, "author": "René"}, "latin-1")
UTF16_RUNNER = _json(RUNNER, "utf-16")
BOM_RUNNER = _json(RUNNER, prefix=codecs.BOM_UTF8)


def _init(tmp_path: Path, files: dict[bytes, bytes], capsys) -> tuple[Path, int, str]:
    root = repository(tmp_path / "repo")
    commit(root, files={b"src/app.js": JS, b"web/app.js": JS, **files})
    checkout(root)
    code = main(["init", "--repo", str(root)])
    return root, code, capsys.readouterr().err


def _js_lane_dirs(root: Path) -> list[str]:
    """The cwd of each js lane init wrote live, "" for the root."""
    lanes = tomllib.loads((root / "crapkit.toml").read_text(encoding="utf-8")).get("lane", [])
    return [lane.get("cwd", "") for lane in lanes if lane["name"] == "js"]


# --- package.json init can read ---------------------------------------------------

READABLE = [
    # id, tracked package.json files, the dirs whose js lane init writes
    ("root-utf8-bom", {b"package.json": BOM_RUNNER}, [""]),
    ("workspace-utf8-bom", {b"package.json": _json(WORKSPACES), b"web/package.json": BOM_RUNNER},
     ["web"]),
    ("latin1-fixture-beside-a-root-that-names-vitest",
     {b"package.json": _json(RUNNER), b"tests/fixtures/old/package.json": LATIN1_RUNNER}, [""]),
    ("utf16-fixture-beside-a-root-that-names-vitest",
     {b"package.json": _json(RUNNER), b"tests/fixtures/old/package.json": UTF16_RUNNER}, [""]),
]


@pytest.mark.parametrize("files, lanes", [row[1:] for row in READABLE], ids=[row[0] for row in READABLE])
def test_init_writes_the_js_lane_a_readable_package_json_names(tmp_path, capsys, files, lanes):
    root, code, err = _init(tmp_path, files, capsys)

    assert code == 0, err
    assert _js_lane_dirs(root) == lanes
    assert "Traceback" not in err


# --- a package.json that is not the root's and cannot be read -----------------------

SKIPPED = [
    # the root package.json, the unreadable one's path and bytes
    pytest.param(_json(WORKSPACES), "web/package.json", UTF16_RUNNER, id="workspace-utf16"),
    pytest.param(_json(WORKSPACES), "web/package.json", LATIN1_RUNNER, id="workspace-latin1"),
    pytest.param(_json(RUNNER), "tests/fixtures/old/package.json", LATIN1_RUNNER,
                 id="latin1-fixture-beside-a-good-root"),
    pytest.param(_json(RUNNER), "tests/fixtures/old/package.json", b"{ not json",
                 id="not-json-fixture-beside-a-good-root"),
    pytest.param(_json(WORKSPACES), "web/package.json", b"[]", id="workspace-top-level-list"),
    pytest.param(_json(WORKSPACES), "web/package.json", b"", id="workspace-empty-file"),
]


@pytest.mark.parametrize("root_package, skipped, body", SKIPPED)
def test_init_skips_a_non_root_package_json_it_cannot_read_with_one_line_naming_it(
        tmp_path, capsys, root_package, skipped, body):
    """Skipped means init writes what it writes when the file is not there."""
    absent, _, _ = _init(tmp_path / "absent", {b"package.json": root_package}, capsys)
    root, code, err = _init(tmp_path, {b"package.json": root_package, skipped.encode(): body}, capsys)

    assert code == 0, err
    assert _js_lane_dirs(root) == _js_lane_dirs(absent)
    naming = [line for line in err.splitlines() if skipped in line]
    assert len(naming) == 1 and naming[0].startswith("crapkit: "), err


# --- a root package.json init cannot read ----------------------------------------------

@pytest.mark.parametrize("body", [LATIN1_RUNNER, UTF16_RUNNER], ids=["root-latin1", "root-utf16"])
def test_init_refuses_a_root_package_json_it_cannot_read_by_name(tmp_path, capsys, body):
    """The js lane comes from this file, so reading one é as U+FFFD writes a
    lane from bytes npm would reject, and a UTF-16 file silently gives none."""
    root, code, err = _init(tmp_path, {b"package.json": body}, capsys)

    assert code == 3, err
    assert not (root / "crapkit.toml").exists()
    assert "package.json" in err and err.startswith("crapkit: ")


NOT_ONE_OBJECT = [
    # id, root package.json bytes, what the refusal says after "package.json "
    ("not-json", b"{ not json", "is not valid JSON (Expecting property name enclosed in double "
                                "quotes at line 1 column 3); fix that line"),
    ("empty-file", b"", "is not valid JSON (Expecting value at line 1 column 1); fix that line"),
    ("top-level-list", b"[]", "holds an array, not a JSON object; save one object there"),
    ("null", b"null", "holds null, not a JSON object; save one object there"),
]


@pytest.mark.parametrize("body, said", [row[1:] for row in NOT_ONE_OBJECT],
                         ids=[row[0] for row in NOT_ONE_OBJECT])
def test_init_refuses_a_root_package_json_that_is_not_one_json_object(tmp_path, capsys, body,
                                                                       said):
    """None of these holds a test script npm could run. init read each as an
    empty object and wrote a config with no js lane and no word about why."""
    root, code, err = _init(tmp_path, {b"package.json": body, b".gitignore": b"build/\n"},
                            capsys)

    assert code == 3, err
    assert err == f"crapkit: init wrote no file: package.json {said}\n"
    assert not (root / "crapkit.toml").exists()
    assert (root / ".gitignore").read_bytes() == b"build/\n"


# --- .gitignore ------------------------------------------------------------------------

GITIGNORE_E9 = b"# fichiers g\xe9n\xe9r\xe9s\r\nbuild/\r\n"


def test_init_appends_to_a_gitignore_holding_byte_e9_and_keeps_every_byte(tmp_path, capsys):
    root, code, err = _init(tmp_path, {b".gitignore": GITIGNORE_E9}, capsys)

    assert code == 0, err
    assert (root / ".gitignore").read_bytes() == GITIGNORE_E9 + b"\r\n# crapkit\r\n.crapkit/\r\n"


def test_a_second_init_after_one_that_stopped_before_gitignore_finishes(tmp_path, capsys):
    """0.8.0 wrote crapkit.toml, then died reading a .gitignore holding 0xe9:
    the repo keeps a config and a .gitignore without .crapkit/, and every
    later init refused. That state is rebuilt here by putting the .gitignore
    back the way the dead run left it."""
    root, _, _ = _init(tmp_path, {b".gitignore": GITIGNORE_E9}, capsys)
    toml = (root / "crapkit.toml").read_bytes()
    (root / ".gitignore").write_bytes(GITIGNORE_E9)

    code = main(["init", "--repo", str(root)])
    err = capsys.readouterr().err

    assert code == 0, err
    assert (root / ".gitignore").read_bytes() == GITIGNORE_E9 + b"\r\n# crapkit\r\n.crapkit/\r\n"
    assert (root / "crapkit.toml").read_bytes() == toml


# --- an MCP frame whose character straddles the 64 KiB read ----------------------------

_READ = 65536
_CALL = b'{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"'
SPLITS = [(char, cut) for char in ("é", "渡", "\U0001f680") for cut in range(1, len(char.encode()))]


@pytest.mark.parametrize("char, cut", SPLITS,
                         ids=[f"{len(c.encode())}-byte-char-cut-after-{n}" for c, n in SPLITS])
def test_a_character_split_across_the_64k_read_reaches_the_tool_whole(monkeypatch, tmp_path, char, cut):
    """The frame comes from a file, so the loop's os.read returns exactly 64 KiB
    and the character's first `cut` bytes end the first read."""
    name = "x" * (_READ - cut - len(_CALL)) + char + "y"
    frames = tmp_path / "frames.bin"
    frames.write_bytes(_CALL + name.encode() + b'"}}\n{"jsonrpc":"2.0","id":9,"method":"ping"}\n')
    (tmp_path / "crapkit.toml").write_text("[crapkit]\ntarget = 6\n", encoding="utf-8")
    assert (len(_CALL) + name.encode().index(char.encode())) == _READ - cut
    out = io.StringIO()
    with frames.open("rb") as source:
        monkeypatch.setattr(sys, "stdin", source)
        monkeypatch.setattr(sys, "stdout", out)
        assert serve(tmp_path) == 0

    replies = {m["id"]: m for m in map(json.loads, out.getvalue().splitlines())}
    assert replies[2]["result"]["content"][0]["text"] == f"unknown tool {name!r}"
    assert replies[9]["result"] == {}


# --- source bytes the scorer decodes --------------------------------------------------

PICK = ("def pick(x):\n" + "".join(f"    if x == {i}:\n        return 'café {i}'\n" for i in range(8))
        + "    return 'résumé'\n")
SOURCES = [
    # id, the file's bytes, the lines above the function
    ("declared-latin1", ("# -*- coding: latin-1 -*-\n" + PICK).encode("latin-1"), 1),
    ("trailing-nul-bytes", PICK.encode() + b"\x00\x00", 0),
    ("a-2-mb-line", PICK.encode() + b"X = '" + b"y" * 2_000_000 + b"'\n", 0),
]


def _picks(records) -> list[tuple[str, int, int]]:
    return [(r.long_name, r.ccn, r.start) for r in records if r.long_name.startswith("pick")]


@pytest.mark.parametrize("raw, above", [row[1:] for row in SOURCES], ids=[row[0] for row in SOURCES])
def test_decode_source_scores_the_function_in_the_bytes_a_file_holds(tmp_path, raw, above):
    path = tmp_path / "extra.py"
    path.write_bytes(raw)

    records = analyze_one((str(path), "extra.py"))[1]

    assert "return 'café 0'" in decode_source(raw)
    assert _picks(records) == [("pick( x )", 9, 1 + above)]
    assert staged_records({"extra.py": raw}) == {"extra.py": records}


# --- the digest body alert_command reads ------------------------------------------------

def _digest_repo(root: Path, long_name: str, alert_command: str) -> None:
    """Two scored runs of one function, the second worse, so digest has a body."""
    (root / "crapkit.toml").write_text(
        f"[crapkit]\ntarget = 6\nalert_command = '{alert_command}'\n\n"
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["python"]\n', encoding="utf-8")
    (root / ".crapkit").mkdir()
    store = SnapshotStore(root / ".crapkit" / "crap.sqlite")
    for i, crap in enumerate((40.0, 55.0), start=1):
        row = ScoredRow("src", "src/m.py", long_name, 1, 9, 7, 7, 7, 5, 1, 1, 0.25, "measured", crap,
                        "add-tests")
        store.write_run(commit=f"c{i}", tool_versions={}, rows=[row], lanes={"unit": {}},
                        kind="coverage")


def _sink(root: Path, exit_code: int = 0, noise: bytes = b"") -> tuple[str, Path]:
    """An alert command that keeps the bytes it reads and answers with `noise`."""
    script, log = root / "sink.py", root / "alert.bin"
    script.write_text(
        "import sys, pathlib\n"
        f"pathlib.Path(r'{log}').write_bytes(sys.stdin.buffer.read())\n"
        f"sys.stdout.buffer.write({noise!r})\nsys.stderr.buffer.write({noise!r})\n"
        f"sys.exit({exit_code})\n", encoding="utf-8")
    return f'"{sys.executable}" "{script}"', log


@pytest.mark.parametrize("long_name", ["grade( score )", "gräde_世( score )"], ids=["ascii", "non-ascii"])
def test_digest_alert_hands_the_function_name_to_the_alert_command_as_utf8(tmp_path, capsys, long_name):
    command, log = _sink(tmp_path)
    _digest_repo(tmp_path, long_name, command)

    code = main(["digest", "--alert", "--repo", str(tmp_path)])
    out = capsys.readouterr()

    assert code == 0, out.err
    assert f"regressed +15.0: src/m.py {long_name} (crap 55.0)" in log.read_bytes().decode("utf-8")
    assert f"src/m.py {long_name}" in out.out


# What a failed alert's line says after the command's own words.
NOT_ALERTED = (" - the digest above was not alerted; rerun once [crapkit] alert_command in "
               "crapkit.toml exits 0\n")
ANSWERS = [
    # id, the alert command's exit code and output, digest's exit, digest's own line on stderr
    ("exits-3", 3, b"", 5, "crapkit: digest alert command failed (exit 3) and printed nothing" + NOT_ALERTED),
    ("writes-bytes-that-are-not-utf8", 0, b"caf\xe9 \xff\xfe\n", 0, ""),
    ("writes-bytes-that-are-not-utf8-and-exits-3", 3, b"caf\xe9 \xff\xfe\n", 5,
     "crapkit: digest alert command failed (exit 3): caf\ufffd \ufffd\ufffd" + NOT_ALERTED),
]


@pytest.mark.parametrize("exit_code, noise, code, line", [row[1:] for row in ANSWERS],
                         ids=[row[0] for row in ANSWERS])
def test_digest_alert_ends_in_its_own_line_whatever_the_alert_command_answers(
        tmp_path, capsys, exit_code, noise, code, line):
    command, log = _sink(tmp_path, exit_code, noise)
    _digest_repo(tmp_path, "gräde_世( score )", command)

    assert main(["digest", "--alert", "--repo", str(tmp_path)]) == code
    assert capsys.readouterr().err == line
    assert "gräde_世" in log.read_bytes().decode("utf-8")


# --- Claude Code's plugin record under non-ASCII directories ------------------------------

@pytest.fixture()
def _agreeing_cli(monkeypatch):
    """The crapkit on PATH, stubbed to this CLI's version so the handshake
    answers from the plugin files alone."""
    admin._spawned_cli.cache_clear()
    monkeypatch.setattr(admin, "_spawned_cli", lambda: ("/crapkit-test-absent/bin/crapkit", crapkit.__version__))


def _plugin(root: Path, version: str) -> Path:
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "crapkit", "version": version}), encoding="utf-8")
    (root / "hooks").mkdir()
    handler = {"type": "command", "command": "crapkit", "args": ["claude-hook", "--protocol", "1"]}
    (root / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": [handler]}]}}), encoding="utf-8")
    return root


PLUGIN_DIRS = [
    # id, config dir name, install dir name, installed_plugins.json writes non-ASCII raw
    ("ascii-control", "claude", "crapkit-plugin", False),
    ("non-ascii-config-dir", "clauéde 渡", "crapkit-plugin", True),
    ("non-ascii-install-path-raw-utf8", "claude", "plugïn 渡 \U0001f680", True),
    ("non-ascii-install-path-escaped", "claude", "plugïn 渡 \U0001f680", False),
    ("both-non-ascii", "clauéde 渡", "plugïn 渡", True),
]


@pytest.mark.usefixtures("_agreeing_cli")
@pytest.mark.parametrize("config_name, install_name, raw", [row[1:] for row in PLUGIN_DIRS],
                         ids=[row[0] for row in PLUGIN_DIRS])
def test_doctor_plugin_root_reads_the_installer_s_record_under_any_directory_name(
        tmp_path, capsys, monkeypatch, config_name, install_name, raw):
    """The record points outside the cache and the cache holds an older
    version, so the first line says the record was read."""
    config = tmp_path / config_name
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    recorded = _plugin(tmp_path / install_name, crapkit.__version__)
    _plugin(config / "plugins" / "cache" / "crapkit" / "crapkit" / "0.0.1", "0.0.1")
    record = {"version": 2, "plugins": {"crapkit@crapkit": [
        {"scope": "user", "installPath": str(recorded), "version": crapkit.__version__}]}}
    (config / "plugins" / "installed_plugins.json").write_bytes(
        json.dumps(record, ensure_ascii=not raw).encode("utf-8"))

    code = main(["doctor", "--plugin-root"])
    out = capsys.readouterr()

    assert (code, out.err) == (0, "")
    assert out.out.splitlines() == [f"crapkit doctor: checking {recorded}"]


@pytest.mark.usefixtures("_agreeing_cli")
def test_doctor_plugin_root_finds_the_cached_install_under_a_non_ascii_config_dir(tmp_path, capsys,
                                                                                  monkeypatch):
    config = tmp_path / "clauéde 渡"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    cached = _plugin(config / "plugins" / "cache" / "crapkit" / "crapkit" / crapkit.__version__,
                     crapkit.__version__)

    code = main(["doctor", "--plugin-root"])
    out = capsys.readouterr()

    assert (code, out.err) == (0, "")
    assert out.out.splitlines() == [f"crapkit doctor: checking {cached}"]
