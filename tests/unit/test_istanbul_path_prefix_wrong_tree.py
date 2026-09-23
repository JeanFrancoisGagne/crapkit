"""An istanbul lane's wrong-tree check reads the path the runner wrote.

path_prefix is a coverage.py key: that reader glues it onto every measured path,
so the wrong-tree check takes it back off to see what the runner wrote. The
istanbul reader never adds it. Taking it off an istanbul key anyway turned
`/ci/other/checkout/a.ts`, a file from another tree, into `other/checkout/a.ts`,
which reads as in-tree, and the refusal went silent.
"""
import json

import pytest

from crapkit.config import Lane
from crapkit.errors import ToolError
from crapkit.lanes import run_lane

ELSEWHERE = "/ci/other/checkout/a.ts"


def _istanbul_file() -> dict:
    return {"fnMap": {"0": {"name": "f", "decl": {"start": {"line": 1}},
                            "loc": {"start": {"line": 1}, "end": {"line": 3}}}},
            "f": {"0": 1},
            "statementMap": {"0": {"start": {"line": 2}}}, "s": {"0": 1},
            "branchMap": {}, "b": {}}


def _lane(path_prefix: str) -> Lane:
    return Lane(name="js", command="npx vitest run --coverage", artifact="cov.json",
                parser="istanbul", scopes=("web",), path_prefix=path_prefix)


def _refusal(tmp_path, path_prefix: str) -> str:
    (tmp_path / "cov.json").write_text(json.dumps({ELSEWHERE: _istanbul_file()}),
                                       encoding="utf-8")
    with pytest.raises(ToolError) as raised:
        run_lane(tmp_path, _lane(path_prefix), reuse_artifact=True,
                 scope_paths={"web": ("web/src",)})
    return str(raised.value)


@pytest.mark.parametrize("path_prefix", ["/ci/", "/ci"])
def test_an_istanbul_lane_with_path_prefix_still_refuses_another_tree(tmp_path, path_prefix):
    message = _refusal(tmp_path, path_prefix)

    assert "describes a different tree" in message
    assert ELSEWHERE in message, "the path is quoted the way the artifact spells it"


def test_the_same_artifact_refuses_the_same_way_without_the_key(tmp_path):
    assert "describes a different tree" in _refusal(tmp_path, "")
