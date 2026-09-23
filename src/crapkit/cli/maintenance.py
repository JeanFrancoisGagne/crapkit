"""Recover abandoned temporary mutation checkouts."""
from ..mutate_pool import recover_temporary
from ..errors import ToolError
from ._shared import _command_root, _load_repo_config, _print_json


# Test evidence retention moved to the development runner that writes it,
# tools/testing/run.py. The field stays so --json readers keep every key they
# had; nothing fills it.
_NO_TEST_RUNS = ("removed", "planned", "active", "unproven", "changed")


def _temporary_results(root, dry_run: bool) -> list[dict]:
    return [{"path": str(row.path), "status": row.status, "reason": row.reason}
            for row in recover_temporary(root, dry_run=dry_run)]


def _print_cleanup(result: dict) -> None:
    for row in result["temporary_mutations"]:
        print(f"temporary mutation {row['status']}: {row['path']} ({row['reason']})")


def cmd_clean(args) -> int:
    root = _command_root(args.repo)
    _load_repo_config(root)  # clean acts only where a crapkit.toml admits it
    result = _cleanup(root, args.dry_run)
    if args.json:
        _print_json(result)
    else:
        _print_cleanup(result)
    return int(any(row["status"] == "failed" for row in result["temporary_mutations"]))


def _cleanup(root, dry_run: bool) -> dict:
    try:
        return {"dry_run": dry_run,
                "test_runs": {status: [] for status in _NO_TEST_RUNS},
                "temporary_mutations": _temporary_results(root, dry_run)}
    except OSError as error:
        raise ToolError(f"cleanup could not complete: {error}") from error
