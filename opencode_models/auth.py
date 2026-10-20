"""Local storage for the Console service-account key.

The key lives in one file under the user's config dir, readable only by
its owner — never in the repo, never in a data dir (a `--data` checkout
may be shared or committed). The environment always wins: CI and one-off
runs set `OPENCODE_API_KEY` without touching the file.
"""

from __future__ import annotations

import os
from pathlib import Path


def key_file() -> Path:
    """Where the saved key lives (XDG config home, per user)."""
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "opencode-models" / "api_key"


def save_key(key: str) -> Path:
    """Write *key* to the key file, owner-only, creating dirs as needed.

    The file is created with 0o600 and an existing file is tightened to
    it — whatever the umask says.
    """
    path = key_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        handle.write(key)
    os.chmod(path, 0o600)
    return path


def load_key() -> str | None:
    """The service-account key: the environment first, then the file.

    None when neither has one. The file's content is stripped — a
    trailing newline from an editor is not part of the key.
    """
    env = os.environ.get("OPENCODE_API_KEY")
    if env:
        return env
    try:
        return key_file().read_text().strip() or None
    except OSError:
        return None
