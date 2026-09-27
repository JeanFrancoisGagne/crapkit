"""Paths keep their identity at public command and serialization boundaries, and
the bytes crapkit reads back from outside programs read as those programs wrote
them."""
import codecs
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit

import pytest

from hang_guard import HANG_SECONDS
from mcp_stdio import run as run_mcp

from crapkit.cli import main
from crapkit.config import Config, Scope
from crapkit.sarif import diff_uncovered_results, github_annotation
from crapkit.store import SnapshotStore
from crapkit.universe import assign_files

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("path", ["src/a#b.py", "src/a%20b.py", "src/a?b.py", "src/a b.py",
                                      "src/ā.py", "src/a\\b.py"])
def test_sarif_uri_and_annotation_name_the_original_file(path):
    result = diff_uncovered_results([(path, 7)])[0]
    uri = result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"]
    parsed = urlsplit(uri)
    assert (unquote(parsed.path), parsed.query, parsed.fragment) == (path, "", "")
    annotation_path = github_annotation(result).split(" file=", 1)[1].split(",line=", 1)[0]
    assert annotation_path.replace("%25", "%") == path


def test_universe_keeps_git_backslashes_in_the_filename():
    cfg = Config(scopes=(Scope("src", ("src",), ("python",)),))
    assert assign_files(["src/a\\b.py"], cfg) == {"src": ["src/a\\b.py"]}


@pytest.mark.parametrize("explicit", [False, True])
def test_claim_release_resolves_paths_from_the_command_root(tmp_path, monkeypatch, capsys, explicit):
    (tmp_path / "crapkit.toml").write_text("[crapkit]\ntarget=6\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / ".crapkit").mkdir()
    store = SnapshotStore(tmp_path / ".crapkit/crap.sqlite")
    store.record_claim(path="src/app.py", long_name="f( )", commit="abc")
    monkeypatch.chdir(tmp_path / "src")
    args = ["--repo", str(tmp_path)] if explicit else []
    path = "./src/app.py" if explicit else "app.py"
    assert main(["claims", "release", path, "f", "--json", *args]) == 0
    assert json.loads(capsys.readouterr().out)["released"] == 1
    assert store.open_claims() == []


def test_mcp_child_output_is_utf8_even_with_a_legacy_host_locale(tmp_path):
    repo = tmp_path / "repo-ā"
    repo.mkdir()
    (repo / "crapkit.toml").write_text(
        '[crapkit]\ntarget=6\n[[scope]]\nname="src"\npaths=["src"]\n'
        'languages=["python"]\ncoverage_optional=true\n', encoding="utf-8")
    frames = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "get_next_item", "arguments": {}}},
              {"jsonrpc": "2.0", "id": 2, "method": "ping"}]
    done = run_mcp([sys.executable, "-m", "crapkit", "mcp", "--repo", str(repo)],
                   cwd=repo, frames="".join(json.dumps(x) + "\n" for x in frames),
                   env=dict(os.environ, PYTHONUTF8="0"), encoding="utf-8", errors="strict")
    answers = {answer["id"]: answer for answer in map(json.loads, done.stdout.splitlines())}
    assert done.returncode == 0, done.stderr
    assert set(answers) == {1, 2}
    assert "repo-ā" in answers[1]["result"]["content"][0]["text"]
    assert answers[2] == {"jsonrpc": "2.0", "id": 2, "result": {}}


def test_doctor_refuses_a_broken_path_launcher(tmp_path):
    shim = tmp_path / ("crapkit.cmd" if os.name == "nt" else "crapkit")
    shim.write_text("@exit /b 7\n" if os.name == "nt" else "#!/bin/sh\nexit 7\n", encoding="utf-8")
    shim.chmod(0o755)
    environment = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ["PATH"])
    done = subprocess.run([sys.executable, "-m", "crapkit", "doctor", "--plugin-root",
                           str(ROOT / "plugin")], capture_output=True, encoding="utf-8",
                          env=environment, timeout=HANG_SECONDS)
    assert done.returncode == 1
    assert os.path.normcase(str(shim)) in os.path.normcase(done.stdout) and "FAIL" in done.stdout


def test_mcp_brief_carries_a_cp1252_function_name_and_its_source_whole(tmp_path):
    """The MCP server reads its CLI child's answer as bytes and the child
    writes UTF-8, so a latin-1 module's name and lines come back as the file
    holds them, under a host locale that is not UTF-8."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "crapkit.toml").write_text(
        '[crapkit]\ntarget=6\n[[scope]]\nname="src"\npaths=["src"]\n'
        'languages=["python"]\ncoverage_optional=true\n', encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_bytes(b"# -*- coding: latin-1 -*-\ndef caf\xe9(x):\n"
                                          b"    # \xe9t\xe9\n    if x:\n        return 1\n"
                                          b"    return 2\n")
    for step in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "m"]):
        subprocess.run(["git", *step], cwd=repo, check=True, capture_output=True)
    assert main(["coverage", "--repo", str(repo)]) == 0
    frames = [{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "get_function_brief",
                          "arguments": {"path": "src/app.py", "name": "café"}}}]

    done = run_mcp([sys.executable, "-m", "crapkit", "mcp", "--repo", str(repo)], cwd=repo,
                   frames="".join(json.dumps(x) + "\n" for x in frames),
                   env=dict(os.environ, PYTHONUTF8="0"), encoding="utf-8", errors="strict")

    answer = json.loads(done.stdout.splitlines()[0])
    brief = json.loads(answer["result"]["content"][0]["text"])
    assert done.returncode == 0, done.stderr
    assert brief["function"] == "café( x )"
    assert "# été" in brief["source"]


def _latin1_description(manifest: bytes) -> bytes:
    return manifest.replace(b'"description": "', b'"description": "caf\xe9 ', 1)


MANIFESTS = [
    # id, plugin.json bytes from crapkit's own, whether Claude Code loads it
    ("plain", lambda manifest: manifest, True),
    ("latin1-byte-in-description", _latin1_description, True),
    ("utf8-bom", lambda manifest: codecs.BOM_UTF8 + manifest, False),
    ("utf16", lambda manifest: manifest.decode("utf-8").encode("utf-16"), False),
]
CLAUDE = shutil.which("claude")


def _plugin_copy(tmp_path: Path, edit) -> Path:
    root = tmp_path / "plugin"
    shutil.copytree(ROOT / "plugin", root)
    manifest = root / ".claude-plugin" / "plugin.json"
    manifest.write_bytes(edit(manifest.read_bytes()))
    return root


@pytest.mark.parametrize("edit, loads", [row[1:] for row in MANIFESTS], ids=[row[0] for row in MANIFESTS])
def test_doctor_reads_a_plugin_manifest_the_way_claude_code_does(tmp_path, edit, loads):
    """Claude Code is the reader doctor --plugin-root answers for. Checked with
    `claude plugin validate` 2.1.238: it reads a byte that is not UTF-8 as
    U+FFFD and refuses a byte-order mark. doctor read the first as no manifest
    at all, and reading past the second would pass a plugin that never loads."""
    from crapkit.cli import admin

    root = _plugin_copy(tmp_path, edit)

    assert (admin._manifest_version(root) == _plugin_version()) is loads


REQUIRE_CLAUDE = os.environ.get("CRAPKIT_REQUIRE_CLAUDE") == "1"


@pytest.mark.skipif(CLAUDE is None and not REQUIRE_CLAUDE,
                    reason="Claude Code is not on PATH; MANIFESTS' loads column is what `claude plugin "
                           "validate` 2.1.238 answered, and CI's plugin job runs this beside Claude Code")
@pytest.mark.parametrize("edit, loads", [row[1:] for row in MANIFESTS], ids=[row[0] for row in MANIFESTS])
def test_claude_code_loads_the_manifests_doctor_reads(tmp_path, edit, loads):
    """The oracle for MANIFESTS' loads column. CI's plugin job installs Claude
    Code and runs this with CRAPKIT_REQUIRE_CLAUDE=1, so a Claude Code release
    that reads a manifest another way fails there, and a job that lost the
    binary fails instead of skipping. Elsewhere it runs where a developer has
    Claude Code, and test_doctor_reads_a_plugin_manifest_the_way_claude_code_does
    holds the recorded answers everywhere."""
    assert CLAUDE, "CRAPKIT_REQUIRE_CLAUDE=1 and no `claude` on PATH: install @anthropic-ai/claude-code first"
    done = subprocess.run([CLAUDE, "plugin", "validate", str(_plugin_copy(tmp_path, edit))],
                          capture_output=True, timeout=HANG_SECONDS)

    assert (done.returncode == 0) is loads, done.stdout


def _launcher(directory: Path, answer: bytes) -> Path:
    """A `crapkit` on PATH that answers `--version` with exactly `answer`."""
    directory.mkdir()
    say = directory / "say.py"
    say.write_text(f"import sys\nsys.stdout.buffer.write({answer!r})\n", encoding="utf-8")
    if os.name == "nt":
        shim = directory / "crapkit.cmd"
        shim.write_text(f'@"{sys.executable}" "{say}"\r\n', encoding="utf-8")
    else:
        shim = directory / "crapkit"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{say}"\n', encoding="utf-8")
        shim.chmod(0o755)
    return shim


def _plugin_version() -> str:
    manifest = ROOT / "plugin" / ".claude-plugin" / "plugin.json"
    return json.loads(manifest.read_text(encoding="utf-8"))["version"]


LAUNCHER_ANSWERS = [
    # id, what the launcher prints, whether doctor must FAIL it as unreadable
    ("version-0xff", b"crapkit \xff.9.9\n", True),
    ("version-latin1-tail", b"crapkit 0.8.0\xe9\xff\n", True),
    ("plugin-version", None, False),
]


@pytest.mark.parametrize("answer, unreadable", [row[1:] for row in LAUNCHER_ANSWERS],
                         ids=[row[0] for row in LAUNCHER_ANSWERS])
def test_doctor_refuses_unreadable_launcher_output(tmp_path, answer, unreadable):
    """On Windows a text-mode read decodes in subprocess's reader thread, so a
    byte that is not UTF-8 printed that thread's traceback into doctor's
    stderr, and the except around the call never saw it."""
    answer = answer or f"crapkit {_plugin_version()}\n".encode()
    shim = _launcher(tmp_path / "bin", answer)
    environment = dict(os.environ, PATH=str(shim.parent) + os.pathsep + os.environ["PATH"])

    done = subprocess.run([sys.executable, "-m", "crapkit", "doctor", "--plugin-root",
                           str(ROOT / "plugin")], capture_output=True, encoding="utf-8",
                          errors="replace", env=environment, timeout=HANG_SECONDS)

    fail = (f"crapkit doctor: FAIL {shim} gave no readable answer to `crapkit --version`. "
            "Reinstall the crapkit it belongs to with `")
    assert "Traceback" not in done.stderr and "Exception in thread" not in done.stderr, done.stderr
    assert done.returncode == int(unreadable), done.stdout
    assert (os.path.normcase(fail) in os.path.normcase(done.stdout)) is unreadable, done.stdout


def test_action_comment_selects_exact_nul_framed_paths(tmp_path):
    names = [" leading.py", "line\u2028break.py", "src/a\rb.py", "src/a\nb.py", "src/a\\b.py"]
    changed = tmp_path / "changed"
    changed.write_bytes(("\0".join(names) + "\0").encode("utf-8"))
    rows = [{"path": path, "function": f"selected_{i}", "ccn": 7, "risk": 7,
             "remedy": "decompose"} for i, path in enumerate([*names, "unrelated.py"])]
    worklist = tmp_path / "worklist.json"
    worklist.write_text(json.dumps({"active": rows}), encoding="utf-8")
    out = tmp_path / "comment.md"
    done = subprocess.run([sys.executable, str(ROOT / "tools/action/comment.py"),
                           "--changed-z", str(changed), "--worklist", str(worklist),
                           "--top", "10", "--out", str(out)], capture_output=True, timeout=HANG_SECONDS)
    assert done.returncode == 0, done.stderr
    text = out.read_text(encoding="utf-8")
    assert all(f"selected_{i}" in text for i in range(len(names)))
    assert "selected_5" not in text
    assert len(_table_rows(text)) == len(names) + 1
    assert "a\\rb.py" in text and "a\\nb.py" in text and "line\\u2028break.py" in text


def _table_rows(text):
    return [line for line in text.splitlines() if line.startswith("| ")]
