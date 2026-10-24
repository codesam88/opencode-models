"""Subcommand implementations, one module per command.

`cli.py` keeps the top-level parser, dispatch, and `_fail`; each
module here exposes `run(args, ...)` with business decisions
separated from rendering (pure functions of result objects, `print`
once at the command edge).
"""


def fail(msg: str) -> int:
    """Print a command failure and return its exit code. Shared by commands."""
    print(msg)
    return 1
