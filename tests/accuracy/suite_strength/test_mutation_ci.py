"""accuracy.yml runs the mutation stages the way tools/accuracy/mutation.py reads them.

A run carries a function's stored verdicts only from the receipts under
.crapkit/accuracy/mutation/, and every CI job started from a fresh checkout
that held none: each weekly shard and each nightly diff judged every function
again. The receipts now travel between runs in the actions cache, keyed by the
environment key they hold. The nightly diff passed --since-weekly, which no
longer did anything, under a 30-minute cap that the stats pass (about 24
minutes) nearly filled; the label run still ran `crapkit mutate --base
origin/main`, which carries nothing; and no mutation container had /tmp on
tmpfs (docs/accuracy.md, Weekly, says why each needs it).

Each command is read the way the runner runs it: the words after the image,
parsed by mutation.py's own parser, with `${{ matrix.shard }}` filled.
"""
from __future__ import annotations

import fnmatch
import importlib.util
from pathlib import Path
import shlex
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location("accuracy_mutation_ci",
                                                  ROOT / "tools" / "accuracy" / "mutation.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mutation = _load()

JOBS = yaml.safe_load((ROOT / ".github/workflows/accuracy.yml").read_text(encoding="utf-8"))["jobs"]
RECEIPT_FILES = f"{mutation.RECEIPTS.as_posix()}/*.json"
# The stats pass over the calc stage took about 24 minutes at c3fa1d42; the cap
# leaves the diff run the 30 minutes of judging the old cap meant on top of it.
STATS_MINUTES = 24
JUDGING_MINUTES = 30
MUTATION_JOBS = ("mutation-diff", "mutation-full")


def _commands(job: dict) -> list[str]:
    """Each `docker run` in the job's steps, continuation lines joined."""
    found = []
    for item in job["steps"]:
        text = str(item.get("run", "")).replace("\\\n", " ")
        found += [line[line.index("docker run"):].rstrip(")") for line in text.splitlines()
                  if "docker run" in line]
    return found


def _in_image(command: str, shard: int = 1) -> list[str]:
    """The words a `docker run` hands the image, matrix filled."""
    words = shlex.split(command.replace("${{ matrix.shard }}", str(shard)))
    return words[words.index("$REF") + 1:]


def _mutation_args(command: str):
    words = _in_image(command)
    assert words[:2] == ["python", "tools/accuracy/mutation.py"], words
    return mutation._parser().parse_args(words[2:])


def _calls(job: dict, name: str) -> list:
    return [_mutation_args(command) for command in _commands(job)
            if mutation._parser().parse_args(_in_image(command)[2:]).command == name]


def _index(job: dict, test) -> int:
    (found,) = [index for index, item in enumerate(job["steps"]) if test(item)]
    return found


def _uses(job: dict, action: str) -> int:
    return _index(job, lambda item: item.get("uses", "").startswith(action))


def _runs(job: dict, words: str) -> int:
    return _index(job, lambda item: words in str(item.get("run", "")))


@pytest.mark.parametrize("name", MUTATION_JOBS)
def test_each_docker_run_breaks_its_lines_with_a_continuation(name):
    """7ac0088c's docker runs lost their backslash continuation and kept a run of
    13 spaces inside one line: the shell still ran them, but `_commands` joined
    nothing and the step read as one 150-character line."""
    lines = [line for item in JOBS[name]["steps"] for line in str(item.get("run", "")).splitlines()]
    gaps = [line for line in lines if "  " in line.strip()]

    assert gaps == []
    assert any(line.rstrip().endswith("\\") for line in lines if "docker run" in line)


def test_the_nightly_and_the_label_run_judge_with_mutation_py_diff_under_a_cap_that_fits():
    job = JOBS["mutation-diff"]
    (args,) = _calls(job, "diff")

    assert "label" in job["if"] and "nightly" in job["if"]
    assert not any("crapkit mutate" in str(item.get("run", "")) for item in job["steps"])
    assert args.cap_minutes >= STATS_MINUTES + JUDGING_MINUTES
    assert job["timeout-minutes"] >= args.cap_minutes + 15
    assert not args.cold


def test_the_diff_command_takes_no_since_weekly_flag():
    """--since-weekly did nothing once every run judged what does not carry."""
    with pytest.raises(SystemExit):
        mutation._parser().parse_args(["diff", "--since-weekly"])


@pytest.mark.parametrize("name", MUTATION_JOBS)
def test_every_mutation_container_has_tmp_on_tmpfs_and_no_network(name):
    commands = _commands(JOBS[name])

    assert commands
    for command in commands:
        assert "--tmpfs /tmp:exec" in command, command
        assert "--network none" in command, command


def _key_prefix(expression: str) -> str:
    return f"mutation-receipts-${{{{ {expression} }}}}-"


def _cache_step(job: dict, action: str) -> dict:
    return job["steps"][_uses(job, action)]


def _assert_keyed(found: dict, expression: str) -> None:
    prefix = _key_prefix(expression)
    assert found["with"]["path"] == RECEIPT_FILES
    assert found["with"]["key"] == f"{prefix}${{{{ github.run_id }}}}-${{{{ github.run_attempt }}}}"


def _assert_restores(job: dict, expression: str) -> dict:
    found = _cache_step(job, "actions/cache/restore@")
    _assert_keyed(found, expression)
    assert found["with"]["restore-keys"] == _key_prefix(expression)
    return found


def _assert_env_step(job: dict) -> int:
    """The step that prints the environment key the receipts hold, as `env`."""
    at = _index(job, lambda item: item.get("id") == "env")
    run = job["steps"][at]["run"]
    (command,) = _commands({"steps": [job["steps"][at]]})
    assert _mutation_args(command).command == "env"
    assert 'echo "key=$key" >> "$GITHUB_OUTPUT"' in run
    return at


def test_the_diff_run_restores_the_receipts_and_saves_what_it_wrote():
    job = JOBS["mutation-diff"]
    env = _assert_env_step(job)
    _assert_restores(job, "steps.env.outputs.key")
    saved = _cache_step(job, "actions/cache/save@")
    _assert_keyed(saved, "steps.env.outputs.key")

    assert env < _uses(job, "actions/cache/restore@") < _runs(job, "mutation.py diff")
    assert _runs(job, "mutation.py diff") < _uses(job, "actions/cache/save@")
    assert saved["if"].startswith("always()")


def test_each_weekly_shard_restores_the_receipts_and_hands_its_own_to_the_store():
    job = JOBS["mutation-full"]
    env = _assert_env_step(job)
    _assert_restores(job, "steps.env.outputs.key")
    upload = job["steps"][_uses(job, "actions/upload-artifact@")]
    (args,) = _calls(job, "weekly")

    assert env < _uses(job, "actions/cache/restore@") < _runs(job, "mutation.py weekly")
    assert _runs(job, "mutation.py weekly") < _uses(job, "actions/upload-artifact@")
    assert (args.shard, args.of) == (1, len(job["strategy"]["matrix"]["shard"]))
    assert fnmatch.fnmatchcase(upload["with"]["name"].replace("${{ matrix.shard }}", "3"),
                               mutation.RECEIPT_ARTIFACTS)
    assert upload["with"]["path"] == f"{mutation.RECEIPTS.as_posix()}/weekly-${{{{ matrix.shard }}}}.json"
    assert upload["if"] == "always()"
    assert job["outputs"]["key"] == "${{ steps.env.outputs.key }}"


def test_the_store_saves_every_shard_s_receipt_under_one_key():
    """Each shard saving its own cache would leave the next run the newest one
    shard's receipt; the store merges them first."""
    job = JOBS["mutation-store"]
    _assert_restores(job, "needs.mutation-full.outputs.key")
    download = job["steps"][_uses(job, "actions/download-artifact@")]
    saved = _cache_step(job, "actions/cache/save@")
    _assert_keyed(saved, "needs.mutation-full.outputs.key")

    assert "mutation-full" in job["needs"]
    assert job["if"].startswith("always()") and "weekly" in job["if"]
    assert download["with"] == {"pattern": mutation.RECEIPT_ARTIFACTS,
                                "path": mutation.RECEIPTS.as_posix(), "merge-multiple": True}
    assert (_uses(job, "actions/cache/restore@") < _uses(job, "actions/download-artifact@")
            < _uses(job, "actions/cache/save@"))


def test_no_mutation_job_caches_a_folder_no_run_writes():
    """The weekly shards cached and uploaded mutants/ at the checkout's root, which
    no calc run writes: the stage keeps its mutants/ under the calc stage."""
    for name in MUTATION_JOBS:
        paths = [item["with"]["path"] for item in JOBS[name]["steps"]
                 if item.get("uses", "").startswith(("actions/cache", "actions/upload-artifact"))]
        assert all(path.startswith(mutation.RECEIPTS.as_posix()) for path in paths), (name, paths)
