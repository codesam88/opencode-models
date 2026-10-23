"""Seeding a reference dataset on install, without touching what's there.

A fresh `uv tool install` leaves the XDG data dir empty (or stale from an
older fetch): the curated `overrides/` live in the repo, the catalog lives
in no branch at all, and a bare `opencode-models list` then silently disagrees
with `just run list`. Seeding copies reference files into the target directory
for files that are *missing* only - an existing file is never overwritten,
so a workspace's own fetch results are always safe.
"""

import shutil
import subprocess
import unittest
from pathlib import Path

from opencode_models.seed import (
    copy_missing,
    read_branch_files,
    seed,
)

NEEDS_GIT = shutil.which("git") is None


def _write(root: Path, rel: str, text: str = "x") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _git(args, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


@unittest.skipIf(NEEDS_GIT, "no git on PATH")
def _repo_with_branch(tmp: Path, checkout: str = "local-data") -> Path:
    """A repo whose main has one data file and local-data has another.

    The working tree ends on *checkout*: main ({"a": 1}) for seeding, so
    the reference is the working tree and the branch is only fallback.
    """
    tmp.mkdir(parents=True, exist_ok=True)
    _git(["init", "-qb", "main"], tmp)
    _git(["config", "user.email", "t@t"], tmp)
    _git(["config", "user.name", "t"], tmp)
    _write(tmp, "data/opencode/a.json", '{"a": 1}')
    _git(["add", "-A"], tmp)
    _git(["commit", "-qm", "main"], tmp)
    _git(["checkout", "-qb", "local-data"], tmp)
    _write(tmp, "data/opencode/a.json", '{"a": 2}')
    _write(tmp, "data/opencode/b.json", '{"b": 2}')
    _git(["add", "-A"], tmp)
    _git(["commit", "-qm", "branch"], tmp)
    _git(["checkout", "-q", checkout], tmp)
    return tmp


class CopyMissingTest(unittest.TestCase):
    def test_it_copies_absent_files_including_nested_ones(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "src", Path(tmp) / "dst"
            _write(src, "opencode/a.json", '{"a": 1}')
            _write(src, "tier_prices/z.json", "[]")
            copied, skipped = copy_missing(src, dst)
            self.assertEqual((copied, skipped), (2, 0))
            self.assertEqual((dst / "opencode/a.json").read_text(), '{"a": 1}')

    def test_it_never_overwrites_what_is_there(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "src", Path(tmp) / "dst"
            _write(src, "opencode/a.json", "reference")
            _write(dst, "opencode/a.json", "mine")
            copied, skipped = copy_missing(src, dst)
            self.assertEqual((copied, skipped), (0, 1))
            self.assertEqual((dst / "opencode/a.json").read_text(), "mine")

    def test_a_missing_source_is_not_an_error(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            copied, skipped = copy_missing(Path(tmp) / "nope", Path(tmp) / "dst")
            self.assertEqual((copied, skipped), (0, 0))


@unittest.skipIf(NEEDS_GIT, "no git on PATH")
class BranchFilesTest(unittest.TestCase):
    def test_it_reads_branch_files_without_checking_anything_out(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo_with_branch(Path(tmp) / "repo")
            files = read_branch_files(repo, "local-data", "data/")
            self.assertIn("data/opencode/a.json", files)
            self.assertIn("data/opencode/b.json", files)
            # only reads ran, so nothing was checked out or switched
            self.assertEqual(
                _git(["branch", "--show-current"], repo).strip(), "local-data"
            )

    def test_a_missing_branch_reads_as_empty(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(["init", "-q"], repo)
            self.assertEqual(read_branch_files(repo, "local-data", "data/"), {})


@unittest.skipIf(NEEDS_GIT, "no git on PATH")
class OverrideDeployTest(unittest.TestCase):
    """Tracked overrides deploy with the code; fetched data never does.

    `data/overrides/` is versioned curation, not fetched records: installing
    a newer tool must bring newer curation, or the XDG dir silently keeps
    ranking on stale pins. Same-named files sync from the repo; local files
    the repo never shipped are left alone; fetched layers stay missing-only.
    """

    def _seed_repo(self, tmp: Path) -> Path:
        tmp.mkdir(parents=True, exist_ok=True)
        _git(["init", "-qb", "main"], tmp)
        _git(["config", "user.email", "t@t"], tmp)
        _git(["config", "user.name", "t"], tmp)
        _write(tmp, "data/overrides/pinned.json", '{"v": 2}')
        _write(tmp, "data/opencode/a.json", '{"a": 1}')
        _git(["add", "-A"], tmp)
        _git(["commit", "-qm", "seed"], tmp)
        return tmp

    def test_a_shipped_override_updates_even_when_present(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._seed_repo(Path(tmp) / "repo")
            target = Path(tmp) / "xdg"
            _write(target, "overrides/pinned.json", '{"v": 1}')
            result = seed(target, repo)
            self.assertEqual((target / "overrides/pinned.json").read_text(), '{"v": 2}')
            self.assertEqual(result["synced"], 1)

    def test_a_local_override_the_repo_never_shipped_survives(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._seed_repo(Path(tmp) / "repo")
            target = Path(tmp) / "xdg"
            _write(target, "overrides/mine.json", '{"v": 9}')
            seed(target, repo)
            self.assertEqual((target / "overrides/mine.json").read_text(), '{"v": 9}')

    def test_fetched_files_are_still_never_overwritten(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._seed_repo(Path(tmp) / "repo")
            target = Path(tmp) / "xdg"
            _write(target, "opencode/a.json", "mine")
            result = seed(target, repo)
            self.assertEqual((target / "opencode/a.json").read_text(), "mine")
            self.assertEqual(result["copied"], 0)
            self.assertEqual(result["synced"], 1)


@unittest.skipIf(NEEDS_GIT, "no git on PATH")
class SeedTest(unittest.TestCase):
    def test_repo_data_wins_over_the_branch_and_reruns_change_nothing(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo_with_branch(Path(tmp) / "repo", checkout="main")
            target = Path(tmp) / "xdg"
            first = seed(target, repo)
            self.assertEqual(first["copied"] >= 2, True)
            self.assertEqual((target / "opencode/a.json").read_text(), '{"a": 1}')
            self.assertEqual((target / "opencode/b.json").read_text(), '{"b": 2}')
            # b.json came from the branch (main never had it); a.json came
            # from the working tree, not the identical branch copy
            second = seed(target, repo)
            self.assertEqual(second["copied"], 0)

    def test_user_files_survive_a_seed(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = _repo_with_branch(Path(tmp) / "repo")
            target = Path(tmp) / "xdg"
            _write(target, "opencode/a.json", "mine")
            seed(target, repo)
            self.assertEqual((target / "opencode/a.json").read_text(), "mine")


@unittest.skipIf(NEEDS_GIT, "no git on PATH")
class ManualDeployTest(unittest.TestCase):
    """Tracked manual records deploy like overrides: same-named files sync
    when the repo ships a newer pin, local-only files survive, and the
    fetched layers stay missing-only."""

    def _seed_repo(self, tmp: Path) -> Path:
        tmp.mkdir(parents=True, exist_ok=True)
        _git(["init", "-qb", "main"], tmp)
        _git(["config", "user.email", "t@t"], tmp)
        _git(["config", "user.name", "t"], tmp)
        _write(tmp, "data/manual/pinned.json", '{"v": 2}')
        _write(tmp, "data/opencode/a.json", '{"a": 1}')
        _git(["add", "-A"], tmp)
        _git(["commit", "-qm", "seed"], tmp)
        return tmp

    def test_a_shipped_manual_record_updates_even_when_present(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._seed_repo(Path(tmp) / "repo")
            target = Path(tmp) / "xdg"
            _write(target, "manual/pinned.json", '{"v": 1}')
            result = seed(target, repo)
            self.assertEqual(
                (target / "manual" / "pinned.json").read_text(), '{"v": 2}'
            )
            self.assertEqual(result["synced"], 1)

    def test_a_local_only_manual_file_survives(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._seed_repo(Path(tmp) / "repo")
            target = Path(tmp) / "xdg"
            _write(target, "manual/mine.json", '{"v": 9}')
            seed(target, repo)
            self.assertEqual((target / "manual" / "mine.json").read_text(), '{"v": 9}')


if __name__ == "__main__":
    unittest.main()
