"""What a lane says about an artifact, cell by cell, through `run_lane`.

The wrong-tree check reads what the coverage reader recorded while it read: each
absolute key it did not place, as the runner wrote it, with the placing step's
reason. Before that record it took each format's own inverse of its key rule
and resolved every escaped key a second time. The table below is that check's
output on the merge base, committed before the rewrite, and every
cell holds after it: the verdict, the exit code, the sample paths the message
prints (the runner's spelling) and the advice it ends on.

Cells: (format) x (path_prefix) x (whether the scope declares the prefix's
directory) x (the key the runner wrote). The blind cell, a coverage.py lane with
path_prefix `backend`, a `backend` scope and another checkout's report, scored
every function untested with exit 0 until 0.8.1; it is a plain row here, exit 5
with the wrong-tree message.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from crapkit import coverage_istanbul, coverage_py, lanes, repopath
from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.lanes import run_lane

_ADAPTER = {"istanbul": coverage_istanbul, "coveragepy": coverage_py}
_EXT = {"istanbul": "ts", "coveragepy": "py"}
_SCOPE = {"declared": "backend", "other": "src"}
KINDS = ("in-scope", "outside", "absolute-here", "absolute-elsewhere", "climbs", "unopenable")


def _istanbul_file() -> dict:
    return {"fnMap": {"0": {"name": "f", "decl": {"start": {"line": 1}},
                            "loc": {"start": {"line": 1}, "end": {"line": 3}}}},
            "f": {"0": 1}, "statementMap": {"0": {"start": {"line": 2}}}, "s": {"0": 1},
            "branchMap": {}, "b": {}}


def _coveragepy_file() -> dict:
    return {"missing_lines": [], "functions": {"f": {
        "start_line": 1, "executed_lines": [1, 2], "missing_lines": [],
        "summary": {"covered_lines": 2, "num_statements": 2,
                    "num_branches": 0, "covered_branches": 0}}}}


def _artifact(fmt: str, key: str) -> dict:
    if fmt == "istanbul":
        return {key: _istanbul_file()}
    return {"meta": {"branch_coverage": True}, "files": {key: _coveragepy_file()}}


def _tree(tmp_path: Path, fmt: str) -> Path:
    root = (tmp_path / "repo").resolve()
    for rel in ("backend/src/a", "src/a", "tools/b"):
        path = root / f"{rel}.{_EXT[fmt]}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    return root


def _in_scope(fmt: str, prefix: str, scope: str) -> str:
    """The key that names a.<ext> in the scope's directory, less the prefix the
    reader glues on when its format takes one."""
    file = f"{scope}/src/a" if scope == "backend" else "src/a"
    glued = prefix.rstrip("/") + "/"
    if _ADAPTER[fmt] is coverage_py and prefix and file.startswith(glued):
        file = file[len(glued):]
    return f"{file}.{_EXT[fmt]}"


def _written(fmt: str, prefix: str, scope: str, kind: str, root: Path) -> str:
    ext = _EXT[fmt]
    return {
        "in-scope": lambda: _in_scope(fmt, prefix, scope),
        "outside": lambda: f"tools/b.{ext}",
        "absolute-here": lambda: str(root / "backend" / "src" / f"a.{ext}"),
        "absolute-elsewhere": lambda: str(root.parent / "other" / "backend" / "src" / f"a.{ext}"),
        "climbs": lambda: f"../sibling/src/a.{ext}",
        "unopenable": lambda: f"{root.parent.as_posix()}/o\0ther/src/a.{ext}",
    }[kind]()


_SAMPLES = (re.compile(r"it reports paths like (.*)\. "),
            re.compile(r"; it measured (.*) - either nothing"))


def _samples(message: str) -> str:
    return next(found.group(1) for found in (p.search(message) for p in _SAMPLES) if found)


def _advice(fmt: str, message: str) -> str:
    adapter = _ADAPTER[fmt]
    for name in ("WRONG_TREE_FIX", "ABSOLUTE_FIX"):
        if message.endswith(getattr(adapter, name)):
            return name
    return message.split(" - either nothing in them is exercised yet, ", 1)[1]


def _verdict(message: str) -> str:
    if "describes a different tree" in message:
        return "wrong tree"
    return "absolute in-tree" if "DO sit under this checkout" in message else "warns"


def _cell(tmp_path: Path, capsys, fmt: str, prefix: str, scopes: str, kind: str) -> tuple:
    """(verdict, exit code, sample paths, advice) as the lane prints them, the
    temporary directory spelled <tmp>."""
    root = _tree(tmp_path, fmt)
    key = _written(fmt, prefix, _SCOPE[scopes], kind, root)
    (root / "cov.json").write_text(json.dumps(_artifact(fmt, key)), encoding="utf-8")
    lane = Lane(name="l", command="true", artifact="cov.json", parser=fmt, scopes=("s",),
                path_prefix=prefix)
    capsys.readouterr()
    try:
        run_lane(root, lane, reuse_artifact=True, scope_paths={"s": (_SCOPE[scopes],)})
    except ToolError as refusal:
        message, code = str(refusal), refusal.exit_code
    else:
        message, code = capsys.readouterr().err.strip(), 0
    if not message:
        return ("scores", code, "", "")
    message = message.replace(root.parent.as_posix(), "<tmp>")
    return (_verdict(message), code, _samples(message), _advice(fmt, message))


_WARN_BARE = "or the suite measured a part of the tree these scopes do not name"
_WARN_PY = "or the runner reports paths this lane needs path_prefix to rebase"
_NOTHING = ("or path_prefix {p!r}, which crapkit.toml sets for this lane, does not rebase the "
            "runner's paths onto those scopes; no file the runner named is under those scopes "
            "with or without it, so set path_prefix to the directory the runner's paths are "
            "relative to")
_DROP = ("or path_prefix {p!r}, which crapkit.toml sets for this lane, does not rebase the "
         "runner's paths onto those scopes; without path_prefix the runner's {k} is a file "
         "those scopes claim, so drop path_prefix from this lane")


def _expected(fmt: str, prefix: str, scopes: str, kind: str) -> tuple:
    """The merge base's output for one cell, before the record replaced the
    inverse and the second resolve."""
    ext = _EXT[fmt]
    here = f"<tmp>/repo/backend/src/a.{ext}"
    elsewhere = f"<tmp>/other/backend/src/a.{ext}"
    climbs = f"../sibling/src/a.{ext}"
    unopenable = f"<tmp>/o\0ther/src/a.{ext}"
    glues = fmt == "coveragepy" and bool(prefix)
    if kind == "in-scope":
        if glues and scopes == "other":
            return ("warns", 0, f"backend/src/a.{ext}", _DROP.format(p=prefix, k=f"src/a.{ext}"))
        return ("scores", 0, "", "")
    if kind == "outside":
        if glues and scopes == "declared":
            return ("scores", 0, "", "")
        measured = f"backend/tools/b.{ext}" if glues else f"tools/b.{ext}"
        return ("warns", 0, measured, _reading(fmt, prefix))
    if kind == "absolute-here":
        if fmt == "istanbul":
            return _here_istanbul(prefix, scopes, ext)
        return ("absolute in-tree", 5, here, "ABSOLUTE_FIX")
    sample = {"absolute-elsewhere": elsewhere, "climbs": climbs, "unopenable": unopenable}[kind]
    return ("wrong tree", 5, sample, "WRONG_TREE_FIX")


def _reading(fmt: str, prefix: str) -> str:
    """The warning's other reading: the lane's own path_prefix when it sets one."""
    if prefix:
        return _NOTHING.format(p=prefix)
    return _WARN_PY if fmt == "coveragepy" else _WARN_BARE


def _here_istanbul(prefix: str, scopes: str, ext: str) -> tuple:
    """istanbul rebases a key under this checkout, so it is the in-tree key."""
    if scopes == "declared":
        return ("scores", 0, "", "")
    return ("warns", 0, f"backend/src/a.{ext}", _reading("istanbul", prefix))


CELLS = [(fmt, prefix, scopes, kind)
         for fmt in ("istanbul", "coveragepy")
         for prefix in ("", "backend", "backend/")
         for scopes in ("declared", "other")
         for kind in KINDS]


@pytest.mark.parametrize("fmt, prefix, scopes, kind", CELLS)
def test_each_cell_keeps_the_merge_base_verdict(tmp_path, capsys, fmt, prefix, scopes, kind):
    assert _cell(tmp_path, capsys, fmt, prefix, scopes, kind) == _expected(fmt, prefix, scopes,
                                                                           kind)


@pytest.mark.parametrize("prefix", ["backend", "backend/"])
def test_the_blind_cell_is_refused_with_the_wrong_tree_message(tmp_path, capsys, prefix):
    """The cell 0.8.1 fixed: every function scored untested with exit 0."""
    verdict = _cell(tmp_path, capsys, "coveragepy", prefix, "declared", "absolute-elsewhere")

    assert verdict[:2] == ("wrong tree", 5)
    assert verdict[3] == "WRONG_TREE_FIX"


@pytest.mark.parametrize("fmt", ["istanbul", "coveragepy"])
def test_the_wrong_tree_check_resolves_nothing_itself(tmp_path, monkeypatch, fmt):
    """The reader asks the placing step while it reads; the check reads the
    reasons it recorded and asks the placing step nothing."""
    root = _tree(tmp_path, fmt)
    keys = [_written(fmt, "backend", "backend", kind, root)
            for kind in ("absolute-here", "absolute-elsewhere", "unopenable")]
    report = {}
    for key in keys:
        more = _artifact(fmt, key)
        report = {**more, "files": {**report.get("files", {}), **more["files"]}}             if "files" in more else {**report, **more}
    (root / "cov.json").write_text(json.dumps(report), encoding="utf-8")
    lane = Lane(name="l", command="true", artifact="cov.json", parser=fmt, scopes=("s",),
                path_prefix="backend")
    coverage, _, unplaced = lanes._read_and_parse(lane, root, root / "cov.json")
    calls: list[str] = []
    real = repopath.place
    monkeypatch.setattr(repopath, "place", lambda path, top: calls.append(path) or real(path, top))

    with pytest.raises(ToolError, match="describes a different tree"):
        lanes._judge_artifact_scope(lane, coverage, {"s": ("src",)}, root, unplaced)

    assert calls == []
