"""Ranked console of AI models: benchmark scores against real per-token prices."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("opencode-rank")
except PackageNotFoundError:  # bare checkout without an install
    __version__ = "0.0.0+unknown"
