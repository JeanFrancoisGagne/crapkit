"""Public CLI process entry point. Helpers live in their command-family modules."""

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    """Load the parser only when a caller starts the CLI. Each call is one
    command, and names again every file it leaves out: a process that runs
    several, the test suite's in-process runner among them, keeps no names
    from the last one."""
    from ..gitpaths import reset_left_out
    from .parser import main as dispatch

    reset_left_out()
    return dispatch(argv)
