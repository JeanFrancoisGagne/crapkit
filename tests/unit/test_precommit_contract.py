"""The pre-commit framework contract: .pre-commit-hooks.yaml must exist at the
repo root and point at the installed console script, or `repo: ...crapkit`
entries in consumer configs break silently."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def test_hooks_manifest_declares_the_gate():
    manifest = (ROOT / ".pre-commit-hooks.yaml").read_text(encoding="utf-8")
    assert "id: crapkit-gate" in manifest
    assert "entry: crapkit hook-precommit" in manifest
    assert "pass_filenames: false" in manifest, "the gate reads the git index, not filename args"
    assert "always_run: true" in manifest, "staged-blob gating must not depend on matched types"


def test_manifest_entry_matches_the_console_script():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'crapkit = "crapkit.cli:main"' in pyproject
