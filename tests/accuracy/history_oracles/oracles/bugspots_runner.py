"""Run bugspots_adapter.rb under libfaketime at a repo's newest commit. No crapkit.

bugspots measures every fix against "now"; frozen at the newest commit's
committer time (faketime -f with an absolute date freezes the clock), its
range runs from the oldest commit to the newest, as crapkit's does. The
transform that turns its scores into crapkit's weights is the regex (every
commit counts) and this clock; ruling H4 pins the one difference left: bugspots
dates a commit by its committer, crapkit by its author.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path

import hang_guard
from accuracy.kit import tiers
from .history_git import text

ADAPTER = Path(__file__).resolve().parent / "bugspots_adapter.rb"


def _frozen_at(stamp: int) -> str:
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def scores(root: Path, branch: str = "main") -> dict[str, Decimal]:
    """{top-relative path: bugspots score} with the clock at the newest commit."""
    tiers.require_process("bugspots")
    newest = int(text(root, "log", "-1", "--format=%ct"))
    env = {**os.environ, "TZ": "UTC"}
    argv = ["faketime", "-f", _frozen_at(newest), "ruby", str(ADAPTER), str(root), branch]
    done = hang_guard.run(argv, env=env, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return {path: Decimal(score) for path, score in json.loads(done.stdout)["spots"]}
