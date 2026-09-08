"""Copyable CLI templates finish without an automatic Git maintenance writer."""
import json
import shutil

from cli_inproc_repo import _build, git


def test_template_creation_does_not_start_automatic_maintenance(tmp_path, monkeypatch):
    config = tmp_path / "global.gitconfig"
    config.write_text("[maintenance]\nauto = true\nautoDetach = false\n"
                      "[gc]\nauto = 1\nautoDetach = false\n", encoding="utf-8")
    trace = tmp_path / "git-trace.jsonl"
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TRACE2_EVENT", str(trace))
    template = _build(tmp_path / "template")
    events = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    children = [row["argv"] for row in events if row.get("event") == "child_start"]
    assert not any("maintenance" in argv or "gc" in argv for argv in children), children
    copied = tmp_path / "copied"
    shutil.copytree(template, copied)
    assert git(copied, "rev-parse", "HEAD") == git(template, "rev-parse", "HEAD")
    assert git(copied, "status", "--porcelain") == ""
