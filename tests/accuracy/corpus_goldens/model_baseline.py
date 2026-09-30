"""Which stored run `verify` compares against, written from the README, never from crapkit's code.

doc: README.md:1360-1389 sha256=7f5557010eafb657cd12fe9309214b790a9754819cb173db18d7282a8c4f958d
doc: docs/agent-json.md:1440-1440 sha256=7126e96cfb925301765ba840cdf2aa02149184e27bcda7e45955446d71e36f0e

The input is the store's runs table read with sqlite3: id, kind and
verdict_ok (1 passed, 0 failed, NULL for a run that renders no verdict).

- A candidate is a `coverage` or `legacy` run, or a `verify` that passed. A
  failed verify, a `partial` run, an `inventory` run and a `hook` record never
  qualify.
- The taint rule: after a failed verify, until some verify passes, runs made
  after that failure do not become the baseline, and the pick falls back to
  the newest candidate in front of it. The failure that opens the taint is
  the first one after the newest passing verify.
- Otherwise the baseline is the newest candidate. None when nothing qualifies.
- Only runs at or behind HEAD compete: a run on another branch never serves.
  The input carries no commit, because the upgrade scenes and the README
  example this model judges keep every run on HEAD's own line.
"""
from __future__ import annotations

from collections.abc import Iterable


def _verified(run: dict, ok: int) -> bool:
    return run["kind"] == "verify" and run["verdict_ok"] == ok


def candidate(run: dict) -> bool:
    return run["kind"] in ("coverage", "legacy") or _verified(run, 1)


def _verifies(runs: list[dict], ok: int, after: int = 0) -> list[int]:
    return [run["id"] for run in runs if _verified(run, ok) and run["id"] > after]


def taint(runs: list[dict]) -> int | None:
    """The id of the failed verify no passing verify has cleared, or None."""
    cleared = max(_verifies(runs, 1), default=0)
    return min(_verifies(runs, 0, after=cleared), default=None)


def baseline(rows: Iterable[dict]) -> int | None:
    runs = list(rows)
    limit = taint(runs)
    pool = [run["id"] for run in runs if candidate(run) and (limit is None or run["id"] < limit)]
    return max(pool, default=None)
