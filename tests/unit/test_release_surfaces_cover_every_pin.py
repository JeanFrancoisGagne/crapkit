"""Every pin of the tree's own version has a row in release.py's SURFACES.

`release.py bump` rewrites only the strings SURFACES names. README's Route 4
CI job pinned `pip install "crapkit==0.8.0"`, the handbook's CI case pinned
`uses: JeanFrancoisGagne/crapkit@v0.8.0`, and neither had a row, so a bump to
0.8.1 left both at 0.8.0 and CI copied from either page installed a release
that refuses the marks 0.8.1 stamps. A pin here is any of SURFACES's own
patterns, or `crapkit=={v}`, at the version pyproject declares: once one file
pins a shape, every file that spells it is checked.

Not every version string is a pin. tests/ holds fixtures and recorded logs. A
lock file records what a resolver found, such as the crapkit wheel already on
PyPI. CHANGELOG.md, docs/releases/ and the upgrade guide's Downgrading section
name past releases on purpose and keep naming them after a bump.
"""
import shutil
import sys
from pathlib import Path

import hang_guard

ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(ROOT / "tools" / "release"))

import release  # noqa: E402

DASH = chr(0x2014)
SHAPES = tuple(dict.fromkeys([surface.pattern for surface in release.SURFACES] + [release.PACKAGE + "=={v}"]))
NOT_SHIPPED = ("tests/", "CHANGELOG.md", "docs/releases/")
LOCK_FILES = (".lock", "/package-lock.json")
# Sections that name the release before this one: a downgrade installs it by number.
HISTORY = {"docs/upgrading.md": ("## Downgrading",)}


def _shipped(root: Path) -> list[str]:
    listed = hang_guard.run(["git", "ls-files", "-z"], cwd=root)
    assert listed.returncode == 0, listed.stderr.decode("utf-8", "replace")
    names = (name.decode("utf-8") for name in listed.stdout.split(b"\0") if name)
    return [name for name in names if not name.startswith(NOT_SHIPPED) and not name.endswith(LOCK_FILES)]


def _level(line: str) -> int:
    return len(line) - len(line.lstrip("#")) if line.startswith("#") and " " in line else 0


def _history(path: str, text: str) -> list[range]:
    """The character ranges of HISTORY's sections in `path`: a heading to the next
    heading at its level or above. A `#` line inside a fence is code."""
    spans, start, level, fenced, offset = [], None, 0, False, 0
    for line in text.splitlines(keepends=True) if path in HISTORY else ():
        fenced = fenced != line.startswith("```")
        found = 0 if fenced else _level(line)
        if start is not None and 0 < found <= level:
            spans.append(range(start, offset))
            start = None
        if line.rstrip("\n") in HISTORY.get(path, ()):
            start, level = offset, found
        offset += len(line)
    return spans + ([range(start, offset)] if start is not None else [])


def _lines(text: str, needle: str, skipped: list[range]) -> list[int]:
    found, index = [], text.find(needle)
    while index >= 0:
        if not any(index in span for span in skipped):
            found.append(text.count("\n", 0, index) + 1)
        index = text.find(needle, index + 1)
    return found


def pins(root: Path, version: str, paths: list[str]) -> dict[tuple[str, str], list[int]]:
    """(file, shape) -> the lines where that shape spells `version`."""
    found = {}
    for path in paths:
        try:
            text = (root / path).read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        skipped = _history(path, text)
        for shape in SHAPES:
            lines = _lines(text, shape.format(v=version), skipped)
            if lines:
                found[(path, shape)] = lines
    return found


def _uncovered(found: dict, version: str) -> list[str]:
    rows = {(surface.path, surface.pattern): surface.count for surface in release.SURFACES}
    return [f"{path}:{','.join(map(str, lines))}: {shape.format(v=version)!r} x{len(lines)}, "
            f"SURFACES covers {rows.get((path, shape), 0)}"
            for (path, shape), lines in sorted(found.items()) if len(lines) > rows.get((path, shape), 0)]


def _next(version: str) -> str:
    major, minor, patch = version.split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


def test_every_pin_of_the_current_version_has_a_surfaces_row():
    version = release.current_version(ROOT)

    assert _uncovered(pins(ROOT, version, _shipped(ROOT)), version) == []


def test_bump_leaves_no_pin_at_the_old_version(tmp_path, capsys):
    """The release command on a copy of every file that pins the current
    version, with a changelog that carries the next one's unreleased heading."""
    version = release.current_version(ROOT)
    files = sorted({path for path, _ in pins(ROOT, version, _shipped(ROOT))}
                   | {surface.path for surface in release.SURFACES})
    for path in files:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / path, tmp_path / path)
    (tmp_path / "CHANGELOG.md").write_bytes(f"# Changelog\n\n## {_next(version)} {DASH} unreleased\n\nText.\n"
                                            .encode("utf-8"))

    assert release.main(["bump", _next(version), "--repo", str(tmp_path), "--date", "2026-10-01"]) == 0, \
        capsys.readouterr().err
    assert sorted(f"{path}:{lines}" for (path, _), lines in pins(tmp_path, version, files).items()) == []
