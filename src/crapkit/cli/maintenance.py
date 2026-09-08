"""Apply explicit cleanup policies to recognized, idle Crapkit artifacts."""
from ..mutate_pool import recover_temporary
from ..errors import ToolError
from ..retention import prune_test_runs
from ._shared import _command_root, _load_repo_config, _print_json


def _temporary_results(root, dry_run: bool) -> list[dict]:
    return [{"path": str(row.path), "status": row.status, "reason": row.reason}
            for row in recover_temporary(root, dry_run=dry_run)]


def _print_cleanup(result: dict) -> None:
    for status, paths in result["test_runs"].items():
        for path in paths:
            print(f"test evidence {status}: {path}")
    for row in result["temporary_mutations"]:
        print(f"temporary mutation {row['status']}: {row['path']} ({row['reason']})")


def cmd_clean(args) -> int:
    root = _command_root(args.repo)
    cfg = _load_repo_config(root)
    result = _cleanup(root, cfg, args.dry_run)
    if args.json:
        _print_json(result)
    else:
        _print_cleanup(result)
    return int(any(row["status"] == "failed" for row in result["temporary_mutations"]))


def _cleanup(root, cfg, dry_run: bool) -> dict:
    try:
        return {"dry_run": dry_run,
                "test_runs": prune_test_runs(root, keep=cfg.test_retention_count,
                                             days=cfg.test_retention_days, dry_run=dry_run),
                "temporary_mutations": _temporary_results(root, dry_run)}
    except OSError as error:
        raise ToolError(f"cleanup could not complete: {error}") from error
