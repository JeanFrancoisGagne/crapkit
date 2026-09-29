"""rust-code-analysis-cli 0.0.25 and cargo-crap 0.5.0, read per function.

rust-code-analysis-cli -m -O json prints one JSON document per file: a tree
of spaces (unit, impl, trait, function, closure), each with start_line and
metrics.cyclomatic.sum and metrics.cognitive.sum. rca_metrics() starts one
process for the whole file list and answers {(path, start): {"ccn": ...,
"cognitive": ...}} for every function space.

cargo-crap --format json lists each function as {file, line, cyclomatic}
with file relative to --path (`./src/a.rs`). It starts one process per root.
--no-default-excludes keeps tests/, benches/ and examples/ in the walk.

The transforms from each tool's count to crapkit's documented one live in
test_rust_oracles.py, one rulings row each. No crapkit import.
"""
from __future__ import annotations

import json
from pathlib import Path

import hang_guard


def _documents(text: str) -> list:
    """The JSON documents rust-code-analysis prints back to back."""
    decoder, found, at = json.JSONDecoder(), [], 0
    while at < len(text):
        if text[at].isspace():
            at += 1
            continue
        document, at = decoder.raw_decode(text, at)
        found.append(document)
    return found


def _functions(space: dict):
    if space["kind"] == "function":
        yield space
    for inner in space.get("spaces", []):
        yield from _functions(inner)


def rca_metrics(root: Path, paths: list) -> dict:
    argv = ["rust-code-analysis-cli", "-m", "-O", "json"]
    for path in paths:
        argv += ["-p", path]
    done = hang_guard.run(argv, cwd=root, text=True, encoding="utf-8", errors="replace")
    found = {}
    for document in _documents(done.stdout):
        path = Path(document["name"]).as_posix()
        for space in _functions(document):
            metrics = space["metrics"]
            found[(path, space["start_line"])] = {
                "ccn": int(metrics["cyclomatic"]["sum"]),
                "cognitive": int(metrics["cognitive"]["sum"])}
    assert found, done.stdout[-2000:] + done.stderr
    return found


def cargo_crap(root: Path) -> dict:
    """{(path, line): cyclomatic} for every function under root."""
    done = hang_guard.run(["cargo-crap", "--path", ".", "--format", "json",
                           "--no-default-excludes"], cwd=root, text=True, encoding="utf-8",
                          errors="replace")
    assert done.returncode == 0, done.stdout[-2000:] + done.stderr
    entries = json.loads(done.stdout)["entries"]
    return {(entry["file"].removeprefix("./"), entry["line"]): int(entry["cyclomatic"])
            for entry in entries}
