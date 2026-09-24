"""kit.closure: the static import closure of a file through tests/ and tools/."""
from pathlib import Path

from accuracy.kit import closure

KIT = Path(__file__).resolve().parent


def _tree(tmp_path: Path, files: dict) -> Path:
    for name, text in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path


def test_the_closure_follows_absolute_relative_and_bare_imports(tmp_path):
    root = _tree(tmp_path, {
        "pkg/__init__.py": "",
        "pkg/a.py": "from . import b\nfrom .c import thing\nimport helper\n",
        "pkg/b.py": "from pkg.sub import d\n",
        "pkg/c.py": "thing = 1\n",
        "pkg/sub/__init__.py": "",
        "pkg/sub/d.py": "import os\nimport json\n",
        "helper.py": "",
    })

    reached = closure.closure(root / "pkg" / "a.py", roots=(root,))

    assert sorted(path.relative_to(root).as_posix() for path in reached) == [
        "helper.py", "pkg/__init__.py", "pkg/a.py", "pkg/b.py", "pkg/c.py",
        "pkg/sub/__init__.py", "pkg/sub/d.py"]


def test_crapkit_anywhere_in_the_closure_is_named_with_its_importer(tmp_path):
    root = _tree(tmp_path, {
        "oracle.py": "import middle\n",
        "middle.py": "import os\nfrom crapkit.score import crap\n",
        "clean.py": "import os\n",
        "typed.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import crapkit\n",
    })

    assert closure.crapkit_reach(root / "oracle.py", roots=(root,)) == [
        "middle.py imports crapkit.score"]
    assert closure.crapkit_reach(root / "clean.py", roots=(root,)) == []
    assert closure.crapkit_reach(root / "typed.py", roots=(root,)) == ["typed.py imports crapkit"]


def test_a_cycle_ends(tmp_path):
    root = _tree(tmp_path, {"a.py": "import b\n", "b.py": "import a\n"})

    assert len(closure.closure(root / "a.py", roots=(root,))) == 2


def test_the_kit_s_expected_value_modules_reach_no_crapkit():
    for name in ("exact.py", "strategies.py"):
        assert closure.crapkit_reach(KIT / name) == [], name


def test_drive_reaches_crapkit_only_at_run_time():
    reached = {path.name for path in closure.closure(KIT / "drive.py")}

    assert {"drive.py", "tiers.py", "hang_guard.py", "mcp_stdio.py"} <= reached
    assert closure.crapkit_reach(KIT / "drive.py") == []
