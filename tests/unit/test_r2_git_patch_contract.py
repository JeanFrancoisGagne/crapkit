"""Parsed Git patches retain their format under user presentation settings."""
import os
import subprocess
import sys

import pytest

from crapkit.config import load_config_text
from crapkit.diffparse import changed_ranges
from crapkit.gitio import config_value, diff_since, file_log_patches, staged_reads
from crapkit.hook import gate_staged


SETTINGS = [
    {"color.ui": "always"},
    {"diff.mnemonicPrefix": "true"},
    {"diff.noprefix": "true"},
    {"diff.srcPrefix": "old/", "diff.dstPrefix": "new/"},
    {"diff.outputIndicatorNew": ">", "diff.outputIndicatorOld": "<"},
]
CONFIG = load_config_text('''[crapkit]
target=6
[[scope]]
name="src"
paths=["b"]
languages=["python"]
coverage_optional=true
''')


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root).decode("utf-8").strip()


@pytest.fixture
def changed_repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.name", "Audit")
    git(tmp_path, "config", "user.email", "audit@example.invalid")
    (tmp_path / "b").mkdir()
    path = tmp_path / "b/app.py"
    path.write_text("def bad(x):\n    return x\n", encoding="utf-8")
    git(tmp_path, "add", "b/app.py")
    git(tmp_path, "commit", "-qm", "initial")
    path.write_text("def bad(x):\n" + "".join(
        f"    if x == {i}: return {i}\n" for i in range(7)) + "    return 0\n", encoding="utf-8")
    git(tmp_path, "add", "b/app.py")
    return tmp_path


def configure(root, settings):
    for key, value in settings.items():
        git(root, "config", key, value)


@pytest.mark.parametrize("settings", SETTINGS)
@pytest.mark.parametrize("prestarted", [False, True])
def test_staged_gate_ignores_presentation(changed_repo, settings, prestarted):
    configure(changed_repo, settings)
    if prestarted:
        with staged_reads(changed_repo) as reads:
            verdict = gate_staged(changed_repo, CONFIG, reads)
    else:
        verdict = gate_staged(changed_repo, CONFIG)
    assert [(row.path, row.ccn) for row in verdict.violations] == [("b/app.py", 8)]
    assert verdict.unscoped == []


@pytest.mark.parametrize("settings", SETTINGS)
def test_changed_ranges_and_history_ignore_presentation(changed_repo, settings):
    configure(changed_repo, settings)
    assert changed_ranges(diff_since(changed_repo, "HEAD")) == {"b/app.py": [(2, 9)]}
    git(changed_repo, "commit", "-qm", "change")
    assert changed_ranges(file_log_patches(changed_repo, "b/app.py")[-1][1]) == {
        "b/app.py": [(2, 9)]}
    for key, value in settings.items():
        assert config_value(changed_repo, key) == value


@pytest.mark.parametrize("kind", ["external", "textconv"])
def test_staged_gate_reads_source_without_diff_programs(changed_repo, kind):
    converter = changed_repo / "convert.py"
    converter.write_text("print('def fine(): pass')\n", encoding="utf-8")
    command = f'"{sys.executable}" "{converter}"'
    if kind == "external":
        git(changed_repo, "config", "diff.external", command)
    else:
        (changed_repo / ".gitattributes").write_text("b/app.py diff=audit\n", encoding="utf-8")
        git(changed_repo, "config", "diff.audit.textconv", command)
    assert [row.ccn for row in gate_staged(changed_repo, CONFIG).violations] == [8]


@pytest.mark.parametrize("prestarted", [False, True])
def test_inherited_context_cannot_make_untouched_debt_a_gate_violation(changed_repo, monkeypatch, prestarted):
    path = changed_repo / "b/app.py"
    source = path.read_text(encoding="utf-8") + "\ndef clean():\n    return 1\n"
    path.write_text(source, encoding="utf-8")
    git(changed_repo, "add", "b/app.py")
    git(changed_repo, "commit", "-qm", "standing debt and clean function")
    path.write_text(source.replace("    return 1\n", "    return 2\n"), encoding="utf-8")
    git(changed_repo, "add", "b/app.py")
    monkeypatch.setenv("GIT_DIFF_OPTS", "--unified=20")
    if prestarted:
        with staged_reads(changed_repo) as reads:
            verdict = gate_staged(changed_repo, CONFIG, reads)
    else:
        verdict = gate_staged(changed_repo, CONFIG)
    assert verdict.violations == []
    assert changed_ranges(diff_since(changed_repo, "HEAD")) == {"b/app.py": [(12, 12)]}
    assert os.environ["GIT_DIFF_OPTS"] == "--unified=20"
