"""Control: what 0.8.0 made of a lane input spelled in another case on a
case-sensitive disk.

test_repopath.py pins this row through crapkit.repopath.declared, which 0.8.1
introduced, so on 0.8.0 it stops before its verdict. This test loads the
config the way a person's crapkit.toml reaches crapkit, so it passes on 0.8.0
and on every later tree: on ext4 `Backend` is not backend/, and the input
stays as typed.
"""
from __future__ import annotations

from crapkit.config import load_config_text

from path_spellings import need_case_sensitive

CONFIG = """
[[scope]]
name = "backend"
paths = ["backend"]
languages = ["python"]

[[lane]]
name = "py"
command = "python -m pytest"
parser = "coveragepy"
scopes = ["backend"]
full_suite = false
artifact = ".crapkit/cov.json"
inputs = ["Backend"]
"""


def test_a_lane_input_in_another_case_stays_as_typed_on_a_case_sensitive_disk(tmp_path):
    need_case_sensitive(tmp_path)
    (tmp_path / "backend" / "pkg").mkdir(parents=True)
    (tmp_path / "backend" / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")

    lane = load_config_text(CONFIG, root=tmp_path).lanes[0]

    assert lane.inputs == ("Backend",)
