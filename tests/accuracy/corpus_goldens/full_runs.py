"""The full corpus, measured one member at a time, and the digests its golden pins.

The corpus is the one kit.corpus_dir finds: CRAPKIT_ACCURACY_CORPUS, else
/corpus (the accuracy image bakes it in when built with the release asset),
else the per-user cache that `corpus.py fetch` fills. Its DIGEST file must
equal corpus.toml's digest: a corpus built from other pins cannot stand for
this one.

measure_member() commits a member to a fresh repo with fixed dates, as
wheel_diff.py does, freezes git's clock one day later and runs `inventory
--export` and `coverage --export`. Every member scope is coverage-optional, so
coverage scores each function's CRAP as its ccn and no test runner runs.

goldens/full.tsv pins the row count and the sha256 of each member's exports:
the exports are too large to commit, and at release `wheel_diff.py diff
--corpus full` names the rows when one moves.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from accuracy.kit import corpus_dir, corpus_run, drive, repos

PACKET = Path(__file__).resolve().parent
GOLDEN = PACKET / "goldens" / "full.tsv"
COMMANDS = (("inventory.tsv", ("inventory", "--export", "{out}/inventory.tsv")),
            ("scored.tsv", ("coverage", "--export", "{out}/scored.tsv")))
COLUMNS = ("member", "export", "rows", "sha256")
corpus_tool = corpus_dir.corpus_tool


def table() -> dict:
    return corpus_tool().load()


def member_names() -> list[str]:
    return sorted(table().get("member", {}))


def measure_member(corpus: Path, name: str, work: Path) -> dict[str, str]:
    """{export name: text} for one member."""
    built = repos.build(repos.tree_spec(corpus / name), work / "repo")
    out = work / "out"
    out.mkdir(parents=True)
    driver = drive.Driver(built.root, date_now=corpus_run.date_now(), spawn=True)
    for export, argv in COMMANDS:
        result = driver.run(*[part.replace("{out}", out.as_posix()) for part in argv])
        assert result.code == 0, f"{name}: crapkit {' '.join(argv)} exited {result.code}: " \
                                 f"{result.stderr[-800:]}"
    return {export: (out / export).read_bytes().decode("utf-8") for export, _ in COMMANDS}


def digest(text: str) -> tuple[str, str]:
    """(data rows, sha256) of an export: its lines after the header."""
    rows = len([line for line in text.split("\n")[1:] if line])
    return str(rows), hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_golden(path: Path = GOLDEN) -> dict[tuple[str, str], tuple[str, str]]:
    if not path.is_file():
        return {}
    lines = path.read_bytes().decode("utf-8").split("\n")[1:]
    rows = [line.split("\t") for line in lines if line]
    return {(member, export): (count, sha) for member, export, count, sha in rows}


def write_golden(found: dict[tuple[str, str], tuple[str, str]], path: Path = GOLDEN) -> None:
    lines = ["\t".join(COLUMNS)] + ["\t".join((*key, *found[key])) for key in sorted(found)]
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
