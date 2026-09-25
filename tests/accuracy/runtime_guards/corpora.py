"""The corpora the runtime-guards checks measure: the kit's seed corpus and the full corpus.

The full corpus is a directory of member directories: CRAPKIT_ACCURACY_CORPUS
when set, else %LOCALAPPDATA%/crapkit-accuracy/corpus when it exists, else
/corpus, where the accuracy image bakes it. A member is a directory holding the
crapkit.toml tools/accuracy/corpus.py generates. Each member is copied into a
fresh one-commit repo and scored with one root scope that asks for no coverage,
so every function it holds reaches the store, the worklist, the queue, the
digest, the marks and verify without a suite to run.

A one-commit repo weighs every commit 1.0, so the churn bound's other half (a
commit weighs at most 0.5 once the log has a range) needs real history: each
history/<member>.bundle is cloned and scored the same way over its own log.
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
    """The member directories, each holding its generated crapkit.toml."""
    return sorted(path.parent for path in root.glob("*/crapkit.toml"))


def histories(root: Path) -> list[Path]:
    """The members' history bundles."""
    return sorted(root.glob("history/*.bundle"))


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


def history_repo(bundle: Path, work: Path) -> Path:
    """The bundle's history cloned at `work`, scored under the cc-only config.
    The config stays out of the log, so every commit keeps the member's own date.
    tools/accuracy/corpus.py bundles one branch, main, and no HEAD to follow."""
    work.parent.mkdir(parents=True, exist_ok=True)
    repos.git(work.parent, "clone", "-q", "--branch", "main", str(bundle), work.name)
    (work / "crapkit.toml").write_text(cc_only_config(), encoding="utf-8", newline="\n")
    with open(work / ".git" / "info" / "exclude", "a", encoding="utf-8") as exclude:
        exclude.write("/crapkit.toml\n")
    return work


def day_after_head(root: Path) -> int:
    """A clock one day past the repo's newest commit: the churn window then
    holds the member's recent history whatever day the run happens on."""
    return int(repos.git(root, "log", "-1", "--format=%ct").strip()) + 86_400
