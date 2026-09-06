"""Public CLI process entry point. Helpers live in their command-family modules."""

__all__ = ["main"]


def main(argv: list[str] | None = None) -> int:
    """Load the parser only when a caller starts the CLI."""
    from .parser import main as dispatch

    return dispatch(argv)
