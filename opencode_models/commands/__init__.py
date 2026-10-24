"""Subcommand implementations, one module per command.

`cli.py` keeps the top-level parser, dispatch, and `_fail`; each
module here exposes `run(args, ...)` with business decisions
separated from rendering (pure functions of result objects, `print`
once at the command edge).
"""
