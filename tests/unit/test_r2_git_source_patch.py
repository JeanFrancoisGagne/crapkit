"""Git display attributes cannot remove source from changed-line attribution."""
import json
import os
import subprocess
import sys

import pytest

from crapkit.config import load_config_text
from crapkit.diffparse import changed_ranges
from crapkit.gitio import diff_since, file_log_patches, staged_reads
from crapkit.hook import gate_staged
from crapkit.ratchet_report import mark_events
from crapkit.records import encode_record


CONFIG_TEXT = '''[crapkit]
target=6
[[scope]]
name="src"
paths=["src"]
languages=["python"]
coverage_optional=true
'''
CONFIG = load_config_text(CONFIG_TEXT)


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root)


def source(changed, encoding):
    body = "".join(f"    if x == {i}: return {i}\n" for i in range(7)) if changed else ""
    suffix = " # café" if changed else ""
    return (f"def café(x):\n{body}    return x{suffix}\n").encode(encoding)


@pytest.fixture
def source_repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Git protocol test")
    git(tmp_path, "config", "user.email", "git@example.invalid")
    git(tmp_path, "config", "core.autocrlf", "false")
    root = tmp_path / "member"
    (root / "src").mkdir(parents=True)
    (root / "crapkit.toml").write_text(CONFIG_TEXT, encoding="utf-8")
    return root


def staged_change(root, *, encoding="utf-8", rel="src/bêta[1].py", attribute="*.py -diff\n"):
    (root.parent / ".gitattributes").write_text(attribute, encoding="utf-8")
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_bytes(source(False, encoding))
    (root / "image.png").write_bytes(b"\x89PNG\x00\xff" * 20000)
    (root / "src/bêta1.py").write_bytes(source(False, "utf-8"))
    (root.parent / "outside.py").write_bytes(source(False, "utf-8"))
    git(root.parent, "add", ".")
    git(root.parent, "commit", "-qm", "base")
    (root / rel).write_bytes(source(True, encoding))
    (root / "image.png").write_bytes(b"\x89PNG\x00\xfe" * 20000)
    (root.parent / "outside.py").write_bytes(source(True, "utf-8"))
    git(root.parent, "add", ".")
    return rel


@pytest.mark.parametrize("encoding", ["utf-8", "cp1252"])
@pytest.mark.parametrize("prestarted", [False, True])
def test_binary_marked_source_reaches_staged_gate(source_repo, encoding, prestarted):
    rel = staged_change(source_repo, encoding=encoding)
    if prestarted:
        with staged_reads(source_repo) as reads:
            verdict = gate_staged(source_repo, CONFIG, reads)
    else:
        verdict = gate_staged(source_repo, CONFIG)
    assert [(v.path, v.long_name, v.ccn) for v in verdict.violations] == [(rel, "café( x )", 8)]
    assert verdict.unscoped == []


def test_binary_marked_unscoped_source_is_reported(source_repo):
    rel = staged_change(source_repo, rel="loose/unclaimed.py")
    assert gate_staged(source_repo, CONFIG).unscoped == [rel]


def test_binary_marked_source_has_worktree_and_explicit_base_ranges(source_repo):
    rel = staged_change(source_repo)
    git(source_repo, "branch", "comparison")
    git(source_repo, "commit", "-qm", "changed source")
    assert changed_ranges(diff_since(source_repo, "comparison")) == {rel: [(2, 9)]}
    with staged_reads(source_repo, base="comparison") as reads:
        verdict = gate_staged(source_repo, CONFIG, reads)
    assert [(v.path, v.ccn) for v in verdict.violations] == [(rel, 8)]


def test_advisory_uses_same_binary_source_and_display_protocol(source_repo):
    rel = staged_change(source_repo)
    git(source_repo, "config", "color.ui", "always")
    git(source_repo, "config", "diff.noprefix", "true")
    payload = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Edit",
                          "tool_input": {"file_path": str(source_repo / rel)}})
    run = subprocess.run([sys.executable, "-m", "crapkit", "claude-hook", "--protocol", "1"],
                         cwd=source_repo, input=payload, capture_output=True, text=True, encoding="utf-8")
    assert run.returncode == 2, run.stderr
    assert "café" in run.stderr and "ccn 8" in run.stderr.lower()


def test_ordinary_source_patch_needs_only_one_git_process(source_repo, monkeypatch):
    rel = staged_change(source_repo, attribute="")
    git(source_repo, "checkout", "HEAD", "--", "image.png")
    real_popen = subprocess.Popen
    commands = []

    def capture(args, *positional, **kwargs):
        commands.append(args)
        return real_popen(args, *positional, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", capture)
    assert changed_ranges(diff_since(source_repo, "HEAD")) == {rel: [(2, 9)]}
    assert len(commands) == 1


@pytest.mark.parametrize("marked_path", ["src/app.py", "src/line\x01break.py", "#src/old.py"])
def test_binary_marked_ratchet_history_is_text_at_its_exact_path(source_repo, marked_path):
    rel = "marks[1].tsv"
    (source_repo.parent / ".gitattributes").write_text("*.tsv -diff\n", encoding="utf-8")
    (source_repo / rel).write_text(f"{marked_path}\twork()\t20\n", encoding="utf-8")
    (source_repo / "marks1.tsv").write_text("src/other.py\tother()\t99\n", encoding="utf-8")
    git(source_repo.parent, "add", ".")
    git(source_repo.parent, "commit", "-qm", "first mark")
    (source_repo / rel).write_text(f"{marked_path}\twork()\t10\n", encoding="utf-8")
    git(source_repo, "add", ".")
    git(source_repo, "commit", "-qm", "tighten")
    events = mark_events(file_log_patches(source_repo, rel))
    assert [(key, kind, value) for _, key, kind, value in events] == [
        ((marked_path, "work()"), "added", 20), ((marked_path, "work()"), "updated", 10)]


def test_codec_produced_nul_name_is_not_a_git_history_record(source_repo):
    name = "work\0name()"
    row = encode_record(("src/app.py", name, 20))
    assert "\0" in row
    (source_repo / "marks.tsv").write_text(row + "\n", encoding="utf-8")
    git(source_repo.parent, "add", ".")
    git(source_repo.parent, "commit", "-qm", "NUL name")
    events = mark_events(file_log_patches(source_repo, "marks.tsv"))
    assert [(key, kind, value) for _, key, kind, value in events] == [
        (("src/app.py", name), "added", 20)]


@pytest.mark.parametrize("path", ["bad\udcff.py", '"bad\\377.py"'])
def test_opaque_patch_bodies_do_not_relax_path_identity(path):
    with pytest.raises(UnicodeError):
        changed_ranges(f"+++ b/{path}\n@@ -1 +1 @@\n-old\n+new\n" if not path.startswith('"')
                       else f"+++ {path}\n@@ -1 +1 @@\n-old\n+new\n")


def test_opaque_source_body_cannot_introduce_a_header(source_repo):
    rel = staged_change(source_repo, encoding="cp1252")
    (source_repo / rel).write_bytes(source(True, "cp1252") + b"# \x81\n+++ fake\xff.py\n")
    git(source_repo, "add", ".")
    patch = diff_since(source_repo, "HEAD")
    assert b"\x81" in patch.encode("utf-8", "surrogateescape")
    assert changed_ranges(patch) == {rel: [(2, 11)]}


@pytest.mark.skipif(os.name == "nt", reason="colon filenames require a POSIX filesystem")
def test_source_fallback_treats_leading_pathspec_magic_as_a_literal(source_repo):
    rel = staged_change(source_repo, rel=":(glob)src*.py")
    assert changed_ranges(diff_since(source_repo, "HEAD")) == {rel: [(2, 9)]}
