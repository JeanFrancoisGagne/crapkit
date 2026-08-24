"""The commit-graph warning, through the real CLI on a real git repo.

The synthetic graph is never handed to git — doctor reads the chunk table and
nothing else — so a header and a table are the whole fixture.
"""
import os
import struct
import subprocess
import sys
from pathlib import Path


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "crapkit", *args],
                          cwd=repo, capture_output=True, text=True, timeout=120,
                          env=dict(os.environ))


def graph_bytes(*chunks: bytes) -> bytes:
    head = b"CGPH" + bytes([1, 1, len(chunks), 0])
    offset = 8 + (len(chunks) + 1) * 12
    toc = b"".join(struct.pack(">4sQ", cid, offset) for cid in chunks)
    return head + toc + struct.pack(">4sQ", b"\0\0\0\0", offset)


def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("export function f() { return 1; }\n",
                                             encoding="utf-8")
    (tmp_path / "crapkit.toml").write_text(
        '[crapkit]\ntarget = 6\n\n'
        '[[scope]]\nname = "src"\npaths = ["src"]\nlanguages = ["typescript"]\n',
        encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True,
                   capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "i"], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def write_graph(root: Path, *chunks: bytes) -> None:
    info = root / ".git" / "objects" / "info"
    info.mkdir(parents=True, exist_ok=True)
    (info / "commit-graph").write_bytes(graph_bytes(*chunks))


def test_doctor_warns_and_still_exits_zero(tmp_path: Path):
    root = repo(tmp_path)
    write_graph(root, b"OIDF", b"OIDL", b"CDAT", b"GDA2")

    res = run_cli(root, "doctor")

    assert res.returncode == 0, res.stdout + res.stderr
    warnings = [ln for ln in res.stdout.splitlines() if ln.startswith("WARN")]
    assert len(warnings) == 1, res.stdout
    assert "git commit-graph write --reachable --changed-paths" in warnings[0]


def test_the_warning_reaches_the_json_report(tmp_path: Path):
    import json

    root = repo(tmp_path)
    write_graph(root, b"OIDF", b"CDAT")

    payload = json.loads(run_cli(root, "doctor", "--json").stdout)

    assert [w for w in payload["warnings"] if "Bloom" in w]
    assert payload["problems"] == []


def test_a_filtered_commit_graph_leaves_doctor_silent(tmp_path: Path):
    root = repo(tmp_path)
    write_graph(root, b"OIDF", b"OIDL", b"CDAT", b"BIDX", b"BDAT")

    res = run_cli(root, "doctor")

    assert res.returncode == 0, res.stdout + res.stderr
    assert [ln for ln in res.stdout.splitlines() if ln.startswith("WARN")] == []
