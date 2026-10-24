"""Docs drift guard: user-facing instructions must name live surfaces only.

Twice now behavior moved without the words following it (the `sync`
command landed with no README mention; the pricing scripts were
deleted while docstrings still invoked them). This test pins the
retired names out of the files users read, so the next move updates
the words in the same change.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Surfaces that no longer exist. `just plans-update` became
# `plans-sync`/`sync`; `scripts/update_*_pricing.py` were retired and
# the package modules own the logic behind `sync --force`/`--check`.
RETIRED = (
    "plans-update",
    "scripts/update_go_pricing",
    "scripts/update_zen_pricing",
)

WATCHED = ("README.md", "justfile") + tuple(
    str(p.relative_to(ROOT)) for p in sorted((ROOT / "opencode_models").glob("*.py"))
)


class DocsDriftTest(unittest.TestCase):
    def test_no_retired_workflow_names_in_user_facing_files(self):
        hits = []
        for rel in WATCHED:
            text = (ROOT / rel).read_text()
            for name in RETIRED:
                if name in text:
                    hits.append(f"{rel}: {name}")
        self.assertEqual(hits, [], f"retired workflow names still documented: {hits}")


if __name__ == "__main__":
    unittest.main()
