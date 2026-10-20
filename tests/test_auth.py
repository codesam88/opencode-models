"""Local storage for the Console service-account key.

The key lives in one file under the user's config dir, readable only by
its owner — never in the repo, never in a data dir (a `--data` checkout
may be shared or committed). The environment always wins: CI and
one-off runs set `OPENCODE_API_KEY` without touching the file.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opencode_rank.auth import key_file, load_key, save_key


class KeyFileTest(unittest.TestCase):
    def _home(self, tmp):
        return {"XDG_CONFIG_HOME": str(Path(tmp) / "config"), "HOME": tmp}

    def test_key_file_lives_under_the_config_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, self._home(tmp), clear=True):
                self.assertEqual(
                    key_file(), Path(tmp) / "config" / "opencode-rank" / "api_key"
                )

    def test_save_creates_an_owner_only_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, self._home(tmp), clear=True):
                save_key("oc_sk_test")
                path = key_file()
                self.assertEqual(path.read_text(), "oc_sk_test")
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_save_tightens_a_loose_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, self._home(tmp), clear=True):
                path = key_file()
                path.parent.mkdir(parents=True)
                path.write_text("old")
                path.chmod(0o644)
                save_key("oc_sk_new")
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_the_environment_wins_over_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = self._home(tmp)
            env["OPENCODE_API_KEY"] = "oc_sk_env"
            with mock.patch.dict(os.environ, env, clear=True):
                save_key("oc_sk_file")
                self.assertEqual(load_key(), "oc_sk_env")

    def test_the_file_is_used_when_the_environment_is_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, self._home(tmp), clear=True):
                save_key("oc_sk_file")
                self.assertEqual(load_key(), "oc_sk_file")

    def test_no_key_anywhere_is_no_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, self._home(tmp), clear=True):
                self.assertIsNone(load_key())


if __name__ == "__main__":
    unittest.main()
