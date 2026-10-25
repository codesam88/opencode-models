"""Plan identity: stored preference, --plan flag, fit hint.

The workspace plan (Go vs Plus) is exposed by no endpoint, so it comes
from the user: an explicit flag wins, then a stored preference, then
the Go default. When neither pins it down, measured spend exceeding a
Go cap is evidence for Plus — anything less is silence, since light
usage fits both plans.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from opencode_models import auth as auth_mod
from opencode_models.allowance import resolve_plan, suggest_plan


def _spend(**kw):
    return dict(kw)


def _limits(*models):
    # models: (slug, go_cap, plus_cap)
    return {slug: {"go": go, "go_plus": plus} for slug, go, plus in models}


class ResolvePlanTest(unittest.TestCase):
    def test_flag_wins_over_everything(self):
        self.assertEqual(resolve_plan("go-plus", "go"), "go_plus")
        self.assertEqual(resolve_plan("go", "go_plus"), "go")

    def test_dash_form_maps_to_snapshot_key(self):
        self.assertEqual(resolve_plan("go-plus", None), "go_plus")

    def test_stored_preference_wins_over_default(self):
        self.assertEqual(resolve_plan(None, "go_plus"), "go_plus")
        self.assertEqual(resolve_plan(None, "go"), "go")

    def test_default_is_go(self):
        self.assertEqual(resolve_plan(None, None), "go")

    def test_garbage_stored_preference_falls_back_to_default(self):
        self.assertEqual(resolve_plan(None, "plus"), "go")
        self.assertEqual(resolve_plan(None, ""), "go")


class SuggestPlanTest(unittest.TestCase):
    def test_spend_beyond_go_cap_suggests_plus_with_evidence(self):
        hint = suggest_plan(
            _spend(**{"muse-spark-1.3": 45.0}),
            _limits(("muse-spark-1.3", 30.0, 120.0)),
        )
        self.assertIsNotNone(hint)
        plan, evidence = hint
        self.assertEqual(plan, "go_plus")
        self.assertIn("muse-spark-1.3", evidence)
        self.assertIn("45", evidence)

    def test_spend_within_go_caps_suggests_nothing(self):
        self.assertIsNone(
            suggest_plan(
                _spend(**{"muse-spark-1.3": 12.0}),
                _limits(("muse-spark-1.3", 30.0, 120.0)),
            )
        )

    def test_no_spend_data_suggests_nothing(self):
        self.assertIsNone(suggest_plan({}, _limits(("muse-spark-1.3", 30.0, 120.0))))

    def test_unknown_model_is_skipped_not_assumed(self):
        self.assertIsNone(
            suggest_plan(
                {"ghost-model": 999.0},
                _limits(("muse-spark-1.3", 30.0, 120.0)),
            )
        )


class PlanPreferenceFileTest(unittest.TestCase):
    def _home(self, tmp):
        return {"XDG_CONFIG_HOME": str(Path(tmp) / "config")}

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, self._home(tmp), clear=True):
                self.assertIsNone(auth_mod.load_plan_preference())
                auth_mod.save_plan_preference("go_plus")
                self.assertEqual(auth_mod.load_plan_preference(), "go_plus")

    def test_garbage_reads_as_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, self._home(tmp), clear=True):
                path = auth_mod.plan_file()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("platinum\n")
                self.assertIsNone(auth_mod.load_plan_preference())

    def test_rejects_unknown_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, self._home(tmp), clear=True):
                with self.assertRaises(ValueError):
                    auth_mod.save_plan_preference("plus")


if __name__ == "__main__":
    unittest.main()
