"""Seed a reference dataset on install, without touching what's there.

A fresh `uv tool install` leaves the XDG data dir empty (or stale from an
older fetch): the curated `overrides/` live in the repo, the catalog lives
in no branch at all, and a bare `opencode-rank list` then silently disagrees
with `just list`. Seeding copies reference files into the target directory
for files that are *missing* only - an existing file is never overwritten,
so a workspace's own fetch results are always safe.

Reference precedence per file: the repo working tree first, then the
`local-data` branch. The branch is read with `ls-tree`/`show`, so the
checkout is never switched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

#: Data subdirectories worth seeding. Everything fetched belongs here;
#: nothing curated is ever written by `fetch`, so seeding it is safe.
SEED_SUBDIRS = ("opencode", "tier_prices", "boards", "aiarena", "usage",
                "overrides")

BRANCH = "local-data"
PREFIX = "data/"


def copy_missing(src: Path, dst: Path) -> tuple[int, int]:
    """Copy files under *src* to *dst* where *dst* lacks them.

    Returns ``(copied, skipped)``. A missing source is not an error -
    a fresh clone simply has nothing to seed from.
    """
    src, dst = Path(src), Path(dst)
    if not src.is_dir():
        return (0, 0)
    copied = skipped = 0
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        dest = dst / path.relative_to(src)
        if dest.exists():
            skipped += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(path.read_bytes())
        copied += 1
    return (copied, skipped)


def read_branch_files(repo: Path, branch: str,
                      prefix: str) -> dict[str, str]:
    """``{path: text}`` for files under *prefix* on *branch*, undecoded raw.

    A missing branch (or a directory that is not a repo at all) reads as
    empty rather than failing: seeding degrades to the working tree.
    """
    try:
        names = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", branch, "--", prefix],
            cwd=repo, capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, OSError):
        return {}
    files: dict[str, str] = {}
    for name in names.splitlines():
        name = name.strip()
        if not name:
            continue
        try:
            text = subprocess.run(
                ["git", "show", f"{branch}:{name}"],
                cwd=repo, capture_output=True, check=True).stdout
        except (subprocess.CalledProcessError, OSError):
            continue
        files[name] = text.decode("utf-8", errors="replace")
    return files


def seed(target: Path, repo: Path) -> dict[str, int]:
    """Seed *target* (a data dir) from *repo*, never overwriting.

    Working-tree reference data first, then the branch for whatever is
    still missing. Returns ``{"copied": n, "skipped": m}``.
    """
    target, repo = Path(target), Path(repo)
    copied = skipped = 0
    for sub in SEED_SUBDIRS:
        c, s = copy_missing(repo / "data" / sub, target / sub)
        copied += c
        skipped += s
    for key, text in sorted(read_branch_files(repo, BRANCH, PREFIX).items()):
        rel = key[len(PREFIX):] if key.startswith(PREFIX) else key
        dest = target / rel
        if dest.exists():
            skipped += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        copied += 1
    return {"copied": copied, "skipped": skipped}


def _repo_root() -> Path:
    """The repo this seed runs from: git toplevel, else the cwd."""
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout
    except (subprocess.CalledProcessError, OSError):
        return Path.cwd()
    return Path(out.strip() or ".")


def main() -> int:
    from .cli import default_data_dir
    target = default_data_dir()
    result = seed(target, _repo_root())
    print(f"seeded {result['copied']} file(s) into {target} "
          f"({result['skipped']} already present)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
