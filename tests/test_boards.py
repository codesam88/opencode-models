"""Official benchmark boards fetched directly from their own sources."""

import json
import unittest
from pathlib import Path

from opencode_rank.boards import (
    BoardRow,
    tbench_page_rows,
    merge_boards,
    match_model,
    SWE_URL,
    fetch_tbench,
    swebench_rows,
)

# The board pages are fetched, not committed: this branch ships no scraped
# bytes. What the parsers must cope with is the *shape* of each page, and
# that is written out here by hand — the real payloads were checked against
# the live sites, then reduced to synthetic documents so no fetched content
# (and no file that a global *.html ignore rule could swallow) is tracked.

#: A stand-in for swebench.com's embedded island: the four real subsets,
#: each with checked results and the fields the parser reads.
SWE_ISLAND = [
    {"name": "Verified", "results": [
        {"model_display": "Model A", "model_org": "Org A", "agent": "agent-1",
         "agent_org": "Org A", "resolved": 71.8, "checked": True},
        {"model_display": "Model B", "model_org": "Org B", "agent": "agent-2",
         "agent_org": "Org B", "resolved": 65.0, "checked": True}]},
    {"name": "Multilingual", "results": [
        {"model_display": "Model A", "model_org": "Org A", "agent": "agent-3",
         "agent_org": "Org A", "resolved": 48.2, "checked": True}]},
    {"name": "Multimodal", "results": [
        {"model_display": "Model C", "model_org": "Org C", "agent": "agent-4",
         "agent_org": "Org C", "resolved": 30.4, "checked": True}]},
    {"name": "Lite", "results": [
        {"model_display": "Model D", "model_org": "Org D", "agent": "agent-5",
         "agent_org": "Org D", "resolved": 55.5, "checked": True}]},
    {"name": "Test", "results": [
        {"model_display": "Model E", "model_org": "Org E", "agent": "agent-6",
         "agent_org": "Org E", "resolved": 99.0, "checked": True}]},
]


def swe_page(doc=None):
    """swebench.com's shape: the board as a JSON island inside a script tag."""
    return ('<script type="application/json" id="leaderboard-data">'
            + json.dumps(SWE_ISLAND if doc is None else doc) + "</script>")


def tbench_page(rows, split=False):
    """tbench.ai's shape: the board streamed as an escaped RSC payload.

    Next.js sends it as ``self.__next_f.push([1, "…"])`` with the JSON
    escaped inside a JS string literal, and may split the stream at any
    point — including inside an escape sequence, which *split=True* does.
    """
    board = {"leaderboard": {"id": "board-id", "title": "Terminal-Bench 4.0"},
             "rows": rows}
    body = json.dumps(json.dumps(board))[1:-1]
    if split:
        cut = body.index('\\"rows\\"')
        return ("<script>self.__next_f=self.__next_f||[];"
                'self.__next_f.push([1,"' + body[:cut] + '"])\n'
                'self.__next_f.push([1,"' + body[cut:] + '"])</script>')
    return ('<script>self.__next_f=self.__next_f||[];'
            'self.__next_f.push([1,"' + body + '"])</script>')


def tbench_row_data(model, score, effort="max", rank=1, ci=3.1, org="Org",
                    agent="agent-1", trials=100, date="2026-01-02"):
    """One board row as the page carries it.

    ``rank`` sits at the top level; everything describing the run — the
    model, the agent, the effort, the date — is nested under ``metadata``,
    beside ``metrics``. Getting that nesting wrong is exactly the sort of
    thing a hand-written fixture can hide, so it was taken from the payload
    the live page actually streams.
    """
    return {"rank": rank,
            "metadata": {"date": date,
                         "model_display": {"label": model},
                         "model_org": {"label": org},
                         "agent_display": {"label": agent},
                         "reasoning_effort": effort},
            "metrics": {"accuracy": score,
                        "accuracy_ci95_half_width": ci,
                        "n_trials": trials}}


class SwebenchRowsTest(unittest.TestCase):
    def setUp(self):
        self.html = swe_page()

    def _island(self, html):
        return json.loads(html.split('id="leaderboard-data">')[1]
                          .split("</script>")[0])

    def _wrap(self, doc):
        return swe_page(doc)

    def test_rows_come_from_the_embedded_json_island(self):
        rows = swebench_rows(self.html)
        self.assertTrue(rows)
        self.assertEqual({r.benchmark for r in rows},
                         {"SWE-Bench Verified", "SWE-Bench Multilingual",
                          "SWE-Bench Multimodal", "SWE-Bench Lite"})
        self.assertEqual(rows[0].source, SWE_URL)
        self.assertEqual(rows[0].tier, 2)
        self.assertTrue(all(r.score > 0 for r in rows))

    def test_the_internal_test_subset_is_not_a_benchmark(self):
        doc = self._island(self.html) + [{"name": "Test", "results": [
            {"model_display": "X", "resolved": 99.0, "checked": True}]}]
        benchmarks = {r.benchmark for r in swebench_rows(self._wrap(doc))}
        self.assertNotIn("SWE-Bench Test", benchmarks)

    def test_unchecked_submissions_are_claims_not_scores(self):
        doc = self._island(self.html)
        for subset in doc:
            for entry in subset["results"]:
                entry["checked"] = False
        self.assertEqual(swebench_rows(self._wrap(doc)), [])

    def test_os_model_runs_are_noted(self):
        doc = self._island(self.html)
        for subset in doc:
            for entry in subset["results"]:
                entry["os_model"] = False
        doc[0]["results"][0]["os_model"] = True
        row = next(r for r in swebench_rows(self._wrap(doc)))
        self.assertIn("os-model", row.note)

    def test_a_page_without_the_island_yields_nothing(self):
        self.assertEqual(swebench_rows("<html>no island here</html>"), [])




class MatchModelTest(unittest.TestCase):
    """Board display names must meet catalog ids without guessing."""

    def _models(self, *ids):
        from opencode_rank.data import Model
        return [Model(model_id=i, name=i.split("/")[-1].replace("-", " ").title(),
                      lab=i.split("/")[0], price_input=1.0, price_output=1.0,
                      price_cached_input=1.0) for i in ids]

    def _row(self, name, org=None):
        from opencode_rank.boards import BoardRow
        return BoardRow(benchmark="Terminal-Bench 4.0", model_name=name,
                        score=1.0, source="x", model_org=org)

    def test_tbench_display_names_find_their_model(self):
        models = self._models("anthropic/claude-sonnet-5-5",
                              "xai/grok-4-6", "zhipuai/glm-5-3",
                              "alibaba/qwen3-8-max-0902", "meta/muse-spark-1-3")
        for name, org, want in [("Sonnet 5.5", "Anthropic",
                                 "anthropic/claude-sonnet-5-5"),
                                ("Grok 4.6", "xAI", "xai/grok-4-6"),
                                ("GLM-5.3", "Z.ai", "zhipuai/glm-5-3"),
                                ("Qwen3.8-Max-0902", "Qwen",
                                 "alibaba/qwen3-8-max-0902"),
                                ("Muse Spark 1.3", "Meta", "meta/muse-spark-1-3")]:
            self.assertEqual(match_model(self._row(name, org), models), want)

    def test_word_order_does_not_matter(self):
        models = self._models("anthropic/claude-opus-4-6")
        self.assertEqual(match_model(self._row("Claude 4.6 Opus", "Anthropic"),
                                     models), "anthropic/claude-opus-4-6")

    def test_a_prefix_must_not_swallow_a_longer_id(self):
        models = self._models("anthropic/claude-opus-5",
                              "anthropic/claude-opus-5-5")
        # 5.5 needs an extra "5" the row does not have, so "Opus 5" is the
        # closer match — the count is what separates them
        self.assertEqual(match_model(self._row("Opus 5", "Anthropic"),
                                     models), "anthropic/claude-opus-5")
        self.assertEqual(match_model(self._row("Opus 5.5", "Anthropic"),
                                     models), "anthropic/claude-opus-5-5")

    def test_two_models_with_the_same_tokens_match_nothing(self):
        # identical token sets are equally exact, and nothing separates
        # them — picking one would be a guess
        models = self._models("anthropic/claude-sonnet-5-5",
                              "otherproxy/claude-sonnet-5-5")
        self.assertIsNone(match_model(self._row("Claude Sonnet 5.5"),
                                      models))

    def test_the_org_breaks_an_otherwise_equal_match(self):
        models = self._models("anthropic/claude-sonnet-5-5",
                              "otherproxy/claude-sonnet-5-5")
        self.assertEqual(match_model(self._row("Sonnet 5.5", "Anthropic"),
                                     models), "anthropic/claude-sonnet-5-5")

    def test_a_genuine_tie_matches_nothing(self):
        # two models the row fits equally well is a real ambiguity
        models = self._models("vendor/model-a", "other/model-b")
        self.assertIsNone(match_model(self._row("Model"), models))

    def test_an_exact_token_match_beats_a_subset(self):
        models = self._models("google/gemini-3-flash",
                              "google/gemini-3-flash-preview")
        self.assertEqual(match_model(self._row("Gemini 3 Flash",
                                               "Google DeepMind"), models),
                         "google/gemini-3-flash")

    def test_an_unknown_model_matches_nothing(self):
        models = self._models("xai/grok-4-6")
        self.assertIsNone(match_model(self._row("Nonexistent Model 9"), models))


class MergeBoardsTest(unittest.TestCase):
    """Official boards replace self-reported numbers, not better ones."""

    def _models(self, *ids):
        from opencode_rank.data import Model
        return [Model(model_id=i, name=i.split("/")[-1].replace("-", " ").title(),
                      lab=i.split("/")[0], price_input=1.0, price_output=1.0,
                      price_cached_input=1.0) for i in ids]

    def _board_row(self, name, score, benchmark="Terminal-Bench 4.0",
                   source="https://www.tbench.ai/", agent="Claude Code"):
        return BoardRow(benchmark=benchmark, model_name=name, score=score,
                        source=source, agent=agent)

    def _with_score(self, model, key, value, source):
        from dataclasses import replace as _replace
        return _replace(model, scores={key: value},
                        sources={key: source})

    def test_the_board_beats_a_vendor_self_report(self):
        models = [self._with_score(self._models("anthropic/claude-opus-5-5")[0],
                                   "Terminal-Bench 4.0", 66.4,
                                   "https://www.anthropic.com/claude-opus-5-5")]
        merged, conflicts = merge_boards(
            models, [self._board_row("Opus 5.5", 64.8)])
        self.assertEqual(merged[0].scores["Terminal-Bench 4.0"], 64.8)
        self.assertEqual(len(conflicts), 1)
        self.assertTrue(conflicts[0].override_wins)   # board is tier 2, vendor 3
        self.assertIn("official board", conflicts[0].reason)
        self.assertTrue(conflicts[0].applied)

    def test_the_board_does_not_displace_an_independent_run(self):
        models = [self._with_score(self._models("anthropic/claude-opus-5-5")[0],
                                   "Terminal-Bench 4.0", 63.6,
                                   "https://artificialanalysis.ai/models/m")]
        merged, conflicts = merge_boards(
            models, [self._board_row("Opus 5.5", 64.8)])
        self.assertEqual(merged[0].scores["Terminal-Bench 4.0"], 63.6)
        self.assertEqual(len(conflicts), 1)
        self.assertFalse(conflicts[0].applied)         # AA outranks the board

    def test_a_missing_benchmark_is_added_without_a_conflict(self):
        models = self._models("xai/grok-4-7")
        merged, conflicts = merge_boards(
            models, [self._board_row("Grok 4.7", 33.0)])
        self.assertEqual(merged[0].scores["Terminal-Bench 4.0"], 33.0)
        self.assertEqual(conflicts, [])

    def test_agreeing_numbers_conflict_about_nothing(self):
        models = [self._with_score(self._models("xai/grok-4-7")[0],
                                   "Terminal-Bench 4.0", 33.0,
                                   "https://www.anthropic.com/x")]
        merged, conflicts = merge_boards(
            models, [self._board_row("Grok 4.7", 33.0)])
        self.assertEqual(merged[0].scores["Terminal-Bench 4.0"], 33.0)
        self.assertEqual(conflicts, [])

    def test_the_best_submission_of_a_model_wins(self):
        models = self._models("openai/gpt-6-sol")
        merged, _ = merge_boards(models, [
            self._board_row("GPT-6 Sol", 44.5, agent="codex-old"),
            self._board_row("GPT-6 Sol", 49.4, agent="codex"),
        ])
        self.assertEqual(merged[0].scores["Terminal-Bench 4.0"], 49.4)

    def test_an_unmatched_row_changes_nothing(self):
        models = self._models("xai/grok-4-6")
        merged, conflicts = merge_boards(
            models, [self._board_row("Nobody Model 9", 50.0)])
        self.assertEqual(merged[0].scores, {})
        self.assertEqual(conflicts, [])


class TbenchPageTest(unittest.TestCase):
    """The official board ships inside the page's RSC payload.

    The rendered HTML looked like a bare app shell — but the flight payload
    carries the whole leaderboard (schema plus rows), and it is richer than
    the harness repo's submission files: rows the repo has not received yet,
    and one row per reasoning effort rather than one per model.
    """

    ROWS = [
        tbench_row_data("Model A", 64.85, "max", rank=1, ci=3.11,
                        date="2026-01-02"),
        tbench_row_data("Model B", 58.18, "max", rank=3, ci=2.79),
        tbench_row_data("Model B", 57.88, "xhigh", rank=5, ci=2.72),
        tbench_row_data("Model C", 37.58, "xhigh", rank=21, ci=3.54,
                        org="Org C", agent="agent-9"),
    ]

    def _rows(self, html=None):
        return tbench_page_rows(html if html is not None else tbench_page(self.ROWS))

    def test_every_board_row_becomes_a_row(self):
        rows = self._rows()
        self.assertEqual(len(rows), 4)
        self.assertEqual({r.model_name for r in rows},
                         {"Model A", "Model B", "Model C"})
        self.assertEqual({r.benchmark for r in rows}, {"Terminal-Bench 4.0"})

    def test_scores_agents_and_effort_come_from_the_row(self):
        row = next(r for r in self._rows() if r.model_name == "Model C")
        self.assertEqual(row.score, 37.58)
        self.assertEqual(row.agent, "agent-9")
        self.assertEqual(row.effort, "xhigh")
        self.assertEqual(row.model_org, "Org C")
        self.assertIn("tbench.ai", row.source)

    def test_every_effort_variant_is_kept(self):
        # the board ranks each effort separately; dropping them would hide
        # the fact that a model has several comparable runs
        variants = [r for r in self._rows() if r.model_name == "Model B"]
        self.assertEqual(len(variants), 2)
        self.assertEqual({r.effort for r in variants}, {"max", "xhigh"})

    def test_the_row_note_carries_its_rank_date_and_uncertainty(self):
        row = next(r for r in self._rows() if r.model_name == "Model A")
        self.assertIn("2026-01-02", row.note)
        self.assertIn("3.11", row.note)          # CI95 half width
        self.assertIn("rank 1", row.note)
        self.assertIn("100 trials", row.note)

    def test_a_stream_split_inside_an_escape_still_yields_the_board(self):
        # Next.js may break the payload anywhere, including inside an
        # escape sequence: unescaping chunk by chunk loses all of it
        rows = self._rows(tbench_page(self.ROWS, split=True))
        self.assertEqual(len(rows), 4)

    def test_a_page_without_a_board_yields_nothing(self):
        self.assertEqual(tbench_page_rows("<html><body>nope</body></html>"), [])

    def test_a_row_missing_its_score_is_skipped(self):
        payload = json.dumps({"leaderboard": {"id": "x", "title": "t"}, "rows": [
            {"rank": 1, "metadata": {"model_display": {"label": "M"},
                                     "model_org": {"label": "O"},
                                     "agent_display": {"label": "A"},
                                     "reasoning_effort": "max"},
             "metrics": {}}]})
        html = ('<script>self.__next_f=self.__next_f||[];'
                'self.__next_f.push([1,' + json.dumps(payload) + '])</script>')
        self.assertEqual(tbench_page_rows(html), [])


    def test_a_row_without_a_model_is_skipped(self):
        payload = json.dumps({"leaderboard": {"id": "x", "title": "t"}, "rows": [
            {"rank": 1, "metadata": {"model_display": {},
                                     "model_org": {"label": "O"},
                                     "agent_display": {"label": "A"},
                                     "reasoning_effort": "max"},
             "metrics": {"accuracy": 50.0}}]})
        html = ('<script>self.__next_f=self.__next_f||[];'
                'self.__next_f.push([1,' + json.dumps(payload) + '])</script>')
        self.assertEqual(tbench_page_rows(html), [])

if __name__ == "__main__":
    unittest.main()
