"""models.dev is the go/zen catalog: fetch, slice, and build rows from it."""

import json
import tempfile
import unittest
from pathlib import Path

from opencode_models import modelsdev
from opencode_models.models import Model

GO_RECORD = {
    "id": "iceman-contributor-free",
    "name": "Iceman Contributor Free",
    "release_date": "2026-10-24",
    "limit": {"context": 262144, "output": 131072},
    "cost": {"input": 0, "output": 0, "cache_read": 0},
}
ZEN_RECORD = {
    "id": "minimax-m2.5",
    "name": "MiniMax-M2.5",
    "family": "minimax",
    "canonical_model_id": "minimax/MiniMax-M2.5",
    "limit": {"context": 204800, "output": 131072},
    "cost": {"input": 0.3, "output": 1.2, "cache_read": 0.06},
}
PAYLOAD = {
    "opencode-go": {
        "id": "opencode-go",
        "name": "OpenCode Go",
        "models": {"iceman-contributor-free": GO_RECORD},
    },
    "opencode": {
        "id": "opencode",
        "name": "OpenCode",
        "models": {"minimax-m2.5": ZEN_RECORD},
    },
    "some-other-lab": {"id": "some-other-lab", "models": {"x": {"id": "x"}}},
}


def _get(url, payload=PAYLOAD):
    return lambda _url: json.dumps(payload)


def _card(model_id, name="Card Name", scores=None, price=1.0):
    return Model(
        model_id=model_id,
        name=name,
        lab=model_id.split("/")[0],
        price_input=price,
        price_output=price,
        price_cached_input=price,
        scores=scores or {},
        sources={},
    )


class RefreshTest(unittest.TestCase):
    def test_refresh_writes_only_the_opencode_providers(self):
        with tempfile.TemporaryDirectory() as tmp:
            status, messages, _age = modelsdev.refresh(Path(tmp), get=_get("url"))
            body = json.loads((Path(tmp) / "modelsdev.json").read_text())
        self.assertEqual(status, "refreshed")
        self.assertIn("opencode-go", body["providers"])
        self.assertIn("opencode", body["providers"])
        self.assertNotIn("some-other-lab", body["providers"])
        self.assertEqual(body["meta"]["schema_rev"], 1)
        self.assertTrue(body["meta"]["fetched_at"])

    def test_refresh_keeps_the_snapshot_when_the_fetch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)

            def boom(_url):
                raise OSError("network down")

            status, messages, _age = modelsdev.refresh(data, get=boom)
        self.assertEqual(status, "kept")
        self.assertIn("warning: models.dev unavailable (network down)", messages)

    def test_refresh_is_a_noop_while_the_snapshot_is_fresh(self):
        from datetime import datetime, timedelta, timezone

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            data.mkdir(exist_ok=True)
            stale = datetime.now(timezone.utc) - timedelta(minutes=5)
            (data / "modelsdev.json").write_text(
                json.dumps(
                    {
                        "meta": {"fetched_at": stale.isoformat(timespec="seconds")},
                        "providers": {"opencode-go": {"models": {}}},
                    }
                )
            )
            status, messages, _age = modelsdev.refresh(data, get=_get("url"))
        self.assertEqual(status, "fresh")

    def test_force_refetches_a_fresh_snapshot(self):
        from datetime import datetime, timezone

        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "modelsdev.json").write_text(
                json.dumps(
                    {
                        "meta": {
                            "fetched_at": datetime.now(timezone.utc).isoformat(
                                timespec="seconds"
                            )
                        },
                        "providers": {"opencode-go": {"models": {}}},
                    }
                )
            )
            status, _messages, _age = modelsdev.refresh(
                data, get=_get("url"), force=True
            )
            body = json.loads((data / "modelsdev.json").read_text())
        self.assertEqual(status, "refreshed")
        self.assertIn(
            "iceman-contributor-free", body["providers"]["opencode-go"]["models"]
        )


class LoadTest(unittest.TestCase):
    def test_a_missing_snapshot_loads_as_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(modelsdev.load(Path(tmp)))

    def test_records_are_keyed_by_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            modelsdev.refresh(Path(tmp), get=_get("url"))
            loaded = modelsdev.load(Path(tmp))
        self.assertEqual(set(loaded), {"go", "zen"})
        self.assertEqual(set(loaded["go"]), {"iceman-contributor-free"})
        self.assertEqual(loaded["zen"]["minimax-m2.5"]["name"], "MiniMax-M2.5")


class ToModelsTest(unittest.TestCase):
    def test_prices_name_and_lab_come_from_the_record(self):
        models, card_ids = modelsdev.to_models({"minimax-m2.5": ZEN_RECORD}, cards=[])
        (model,) = models
        # the record's own id: what plan records, roster and availability speak
        self.assertEqual(model.model_id, "minimax-m2.5")
        self.assertEqual(model.name, "MiniMax-M2.5")
        self.assertEqual(model.lab, "minimax")
        self.assertEqual(model.price_input, 0.3)
        self.assertEqual(model.price_output, 1.2)
        self.assertEqual(model.price_cached_input, 0.06)
        self.assertEqual(card_ids, {})

    def test_a_record_without_a_canonical_id_keeps_its_record_id(self):
        models, _card_ids = modelsdev.to_models(
            {"iceman-contributor-free": GO_RECORD}, cards=[]
        )
        (model,) = models
        self.assertEqual(model.model_id, "iceman-contributor-free")
        self.assertEqual(model.name, "Iceman Contributor Free")
        self.assertEqual(model.lab, "")
        self.assertEqual(model.price_input, 0.0)

    def test_scores_are_joined_from_the_matching_card(self):
        card = _card("minimax/MiniMax-M2.5", scores={"Terminal-Bench 4.0": 61.0})
        models, card_ids = modelsdev.to_models({"minimax-m2.5": ZEN_RECORD}, [card])
        (model,) = models
        self.assertEqual(model.scores, {"Terminal-Bench 4.0": 61.0})
        self.assertEqual(card_ids, {"minimax-m2.5": "minimax/MiniMax-M2.5"})

    def test_a_card_is_found_by_bare_slug_when_the_ids_differ(self):
        card = _card("minimax/MiniMax-M2.7", scores={"Terminal-Bench 4.0": 55.0})
        record = dict(ZEN_RECORD, id="minimax-m2.7", canonical_model_id=None)
        models, card_ids = modelsdev.to_models({"minimax-m2.7": record}, [card])
        self.assertEqual(models[0].scores, {"Terminal-Bench 4.0": 55.0})
        self.assertEqual(card_ids, {"minimax-m2.7": "minimax/MiniMax-M2.7"})

    def test_no_card_leaves_the_record_scoreless(self):
        models, card_ids = modelsdev.to_models(
            {"iceman-contributor-free": GO_RECORD}, cards=[]
        )
        self.assertEqual(models[0].scores, {})
        self.assertEqual(card_ids, {})

    def test_records_missing_from_the_snapshot_yield_an_empty_list(self):
        models, card_ids = modelsdev.to_models({}, cards=[])
        self.assertEqual(models, [])
        self.assertEqual(card_ids, {})


if __name__ == "__main__":
    unittest.main()
