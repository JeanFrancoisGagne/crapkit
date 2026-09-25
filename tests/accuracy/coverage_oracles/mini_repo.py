"""Small scored repos for hand cases: sources, recorded artifacts and a crapkit.toml.

A lane copies its recorded artifact into place with kit.repos.copy_command, so a
coverage run starts no test runner. build() writes one commit and hands back a
kit.drive.Driver at the repo, the clock frozen one day after the commit.
"""
from __future__ import annotations

import json
from pathlib import Path

from accuracy.coverage_oracles import probe_repo
from accuracy.kit import drive, repos

HEADER = '[crapkit]\ntarget = {target}\nanalysis_workers = 1\n\n[exclude]\nglobs = ["recorded/**"]\n'


def scope(name: str, paths: list[str], languages: list[str], optional: bool = False) -> str:
    extra = "coverage_optional = true\n" if optional else ""
    return (f'[[scope]]\nname = "{name}"\npaths = {json.dumps(paths)}\n'
            f"languages = {json.dumps(languages)}\n{extra}")


def lane(name: str, parser: str, scopes: list[str], path_prefix: str = "") -> str:
    """A lane copying recorded/<name>.json to .crapkit/cov/<name>.json."""
    table = repos.lane_toml(name, f".crapkit/cov/{name}.json", parser, scopes,
                            f"recorded/{name}.json")
    return table + (f'path_prefix = "{path_prefix}"\n' if path_prefix else "")


def config(scopes: list[str], lanes: list[str], target: int = 6) -> str:
    return HEADER.format(target=target) + "\n".join(scopes) + "\n" + "\n".join(lanes)


def coveragepy_report(files: dict) -> bytes:
    """A coverage.py JSON report around {path: file member}, branch data on."""
    return json.dumps({"meta": {"format": 3, "version": "7.16.1", "branch_coverage": True},
                       "files": files}).encode()


def build(top: Path, tree: dict) -> drive.Driver:
    """One commit of `tree` ({path: str or bytes}) at `top`, and a driver there."""
    encoded = {path: data.encode() if isinstance(data, str) else data for path, data in tree.items()}
    probe_repo.build(top, encoded)
    return drive.Driver(top, date_now=repos.EPOCH + 86_400)
