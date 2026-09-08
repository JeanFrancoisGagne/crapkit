"""Paths keep their identity at public command and serialization boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote, urlsplit

import pytest

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
                   env=dict(os.environ, PYTHONUTF8="0"), timeout=30,
                   encoding="utf-8", errors="strict")
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
                          env=environment, timeout=30)
    assert done.returncode == 1
    assert os.path.normcase(str(shim)) in os.path.normcase(done.stdout) and "FAIL" in done.stdout


def test_doctor_refuses_unreadable_launcher_output(monkeypatch, capsys):
    from crapkit.cli import admin

    def unreadable(*args, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid byte")

    monkeypatch.setattr(subprocess, "run", unreadable)
    monkeypatch.setattr(admin, "_spawned_cli", lambda: ("broken-cli", admin._probed_cli_version("broken-cli")))
    assert main(["doctor", "--plugin-root", str(ROOT / "plugin")]) == 1
    assert "FAIL broken-cli" in capsys.readouterr().out


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
                           "--top", "10", "--out", str(out)], capture_output=True, timeout=30)
    assert done.returncode == 0, done.stderr
    text = out.read_text(encoding="utf-8")
    assert all(f"selected_{i}" in text for i in range(len(names)))
    assert "selected_5" not in text
    assert len([line for line in text.splitlines() if line.startswith("| ")]) == len(names) + 1
    assert "a\\rb.py" in text and "a\\nb.py" in text and "line\\u2028break.py" in text
