"""The corpora the runtime-guards checks measure: the kit's seed corpus and the full corpus.

The full corpus is a directory of member directories: CRAPKIT_ACCURACY_CORPUS
when set, else %LOCALAPPDATA%/crapkit-accuracy/corpus when it exists, else
/corpus, where the accuracy image bakes it. Each member is copied into a fresh
one-commit repo and scored with one root scope that asks for no coverage, so
every function it holds reaches the store, the worklist, the queue, the digest,
the marks and verify without a suite to run.
"""
from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil

from accuracy.kit import repos

STOP = "internal check failed"
CORPUS_ENV = "CRAPKIT_ACCURACY_CORPUS"
# Every command that reaches a check, in an order where each one has what it
# reads: a second scored run for the digest, marks before verify and prune.
# A repo that holds no scored run yet takes FIRST_RUN before these.
COMMANDS = (("coverage", "--json"), ("digest",), ("worklist", "--json"), ("trend", "--json"),
            ("next-item", "--top", "5"), ("ratchet", "seed"), ("verify", "--json"),
            ("ratchet", "prune"), ("report", "--out", ".crapkit/report.html"))
FIRST_RUN = (("inventory",), ("coverage",))


def stops(done) -> list[str]:
    """The internal-check lines one command printed, each naming the command."""
    return [f"{' '.join(done.argv)}: {line}" for line in done.stderr.splitlines() if STOP in line]


def full_corpus() -> Path:
    named = os.environ.get(CORPUS_ENV)
    if named:
        return Path(named)
    local = Path(os.environ.get("LOCALAPPDATA", "~")).expanduser() / "crapkit-accuracy" / "corpus"
    return local if local.is_dir() else Path("/corpus")


def members(root: Path) -> list[Path]:
    return sorted(path for path in root.iterdir() if path.is_dir()) if root.is_dir() else []


def _languages() -> list[str]:
    """Every language crapkit reads, so the root scope claims every member file.
    Read at run time: the names configure the repo and are no expected value."""
    return sorted(importlib.import_module("crapkit.universe").LANGUAGE_EXTENSIONS)


def cc_only_config() -> str:
    names = ", ".join(f'"{name}"' for name in _languages())
    return ('[crapkit]\ntarget = 6\n\n[[scope]]\nname = "all"\npaths = ["."]\n'
            f"languages = [{names}]\ncoverage_optional = true\n")


def member_repo(member: Path, work: Path) -> Path:
    """The member's files in a fresh repo at `work`, one commit at the kit's epoch."""
    shutil.copytree(member, work, ignore=shutil.ignore_patterns(".git", ".crapkit"))
    (work / "crapkit.toml").write_text(cc_only_config(), encoding="utf-8", newline="\n")
    repos.git(work, "init", "-q", "-b", "main")
    for key, value in repos.CONFIG:
        repos.git(work, "config", key, value)
    repos.git(work, "add", "-A")
    repos.git(work, "commit", "-q", "-m", "corpus", date=repos.EPOCH)
    return work
