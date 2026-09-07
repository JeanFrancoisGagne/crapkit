"""A minor release regenerates supported-version guidance before measuring."""
from pathlib import Path
import shutil
import subprocess
import sys

from test_release_guards import capture, git, repo
from test_release_tool import release


ROOT = Path(__file__).resolve().parents[2]


def generate(root):
    subprocess.run([sys.executable, "tools/docs/generate.py"], cwd=root,
                   check=True, capture_output=True, text=True)


def documented_repo(tmp_path):
    root = repo(tmp_path)
    for name in ("SECURITY.md", "CONTRIBUTING.md", "AGENTS.md", "crapkit.schema.json",
                 "tools/docs/generate.py", "tools/testing/run.py"):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (root / ".gitignore").write_text(".crapkit/\ndist/\n__pycache__/\n*.pyc\n")
    changelog = root / "CHANGELOG.md"
    changelog.write_text(changelog.read_text(encoding="utf-8").replace("0.5.2", "0.6.0"), encoding="utf-8")
    generate(root)
    git(root, "add", ".")
    git(root, "commit", "-qm", "generated guidance")
    git(root, "push", "-q", "origin", "main")
    return root


def stage_effect(command, root):
    if command[1:3] == ("tools/release/release.py", "bump"):
        release.bump(root, "0.6.0")
    elif command[1:2] == ("tools/docs/generate.py",):
        generate(root)
    elif command[-2:] == ("crapkit", "coverage"):
        support = (root / "SECURITY.md").read_text(encoding="utf-8")
        assert "| 0.6.x | Yes |" in support, "coverage started before support guidance was regenerated"
        assert "| < 0.6 | No. Upgrade. |" in support


def test_stage1_regenerates_and_stages_the_new_minor_support_policy(tmp_path, monkeypatch):
    root = documented_repo(tmp_path)
    assert "| 0.5.x | Yes |" in (root / "SECURITY.md").read_text(encoding="utf-8")
    commands = capture(monkeypatch, effect=stage_effect)
    release.run("stage1", "0.6.0", root)
    staging = next(command for command in commands if command[:2] == ("git", "add"))
    assert "SECURITY.md" in staging
    assert "tests/unit/test_generated_guidance.py" in release.CONTRACT_FILES
