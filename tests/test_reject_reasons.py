from __future__ import annotations

import unittest
from unittest import mock

from goldset_lab.bg3 import Chunk
from goldset_lab.fixture_builder import (
    REJECT_REASONS,
    _generate_candidate,
    _parse_candidate,
    _parse_candidate_with_reason,
)


def _payload(**override):
    base = {
        "reference_answer": "내성 굴림은 d20을 굴려 난이도와 비교한다",
        "predicate": "d20을 굴린다",
        "queries": [
            {"kind": "natural", "text": "내성 굴림은 어떻게 판정하나"},
            {"kind": "mixed", "text": "saving throw 판정 방법"},
            {"kind": "spacing_abbreviation", "text": "BG3 내성굴림 판정"},
            {"kind": "paraphrase", "text": "저항 판정을 계산하는 절차"},
        ],
    }
    base.update(override)
    return base


def _queries(**override):
    items = {item["kind"]: dict(item) for item in _payload()["queries"]}
    for kind, text in override.items():
        items[kind]["text"] = text
    return list(items.values())


class RejectReasonTest(unittest.TestCase):
    def _reason(self, payload):
        parsed, reason = _parse_candidate_with_reason(payload)
        self.assertIn(reason, REJECT_REASONS)
        return parsed, reason

    def test_a_well_formed_candidate_is_accepted(self):
        parsed, reason = self._reason(_payload())
        self.assertEqual(reason, "ok")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[1], "d20을 굴린다")

    def test_every_gate_reports_its_own_reason(self):
        cases = {
            "explicit_skip": {"skip": True},
            "missing_reference_answer": _payload(reference_answer="  "),
            "missing_predicate": _payload(predicate=None),
            "queries_not_four": _payload(queries=_payload()["queries"][:3]),
            "query_item_malformed": _payload(queries=[{"kind": "unknown", "text": "x"}] * 4),
            "empty_query_text": _payload(queries=_queries(paraphrase="   ")),
            "duplicate_query_text": _payload(
                queries=_queries(paraphrase="내성 굴림은 어떻게 판정하나")
            ),
            "mixed_has_no_latin": _payload(queries=_queries(mixed="혼합 표현인데 영어가 없다")),
            "spacing_has_no_marker": _payload(
                queries=_queries(spacing_abbreviation="띄어쓰기 변형만 있고 약어가 없다")
            ),
        }
        for expected, payload in cases.items():
            with self.subTest(reason=expected):
                parsed, reason = self._reason(payload)
                self.assertIsNone(parsed)
                self.assertEqual(reason, expected)

    def test_missing_kind_is_separated_from_a_malformed_item(self):
        queries = _payload()["queries"]
        queries[3] = {"kind": "natural", "text": "같은 종류가 두 번"}
        parsed, reason = self._reason(_payload(queries=queries))
        self.assertIsNone(parsed)
        self.assertEqual(reason, "kinds_incomplete")

    def test_a_pure_spacing_variant_can_never_pass(self):
        """The schema asks for a spacing variation and a different gate forbids it.

        `duplicate_query_text` compares queries after stripping all whitespace, so
        a spacing_abbreviation query that re-spaces the natural query without
        changing a word always collapses onto it and discards the whole candidate.
        This test pins the contradiction so it is not mistaken for the model
        declining to produce spacing variants.
        """
        payload = _payload(
            queries=_queries(spacing_abbreviation="내성굴림은  어떻게판정하나"),
        )
        payload["queries"] = [
            item if item["kind"] != "natural" else {"kind": "natural", "text": "내성 굴림은 어떻게 판정하나"}
            for item in payload["queries"]
        ]
        parsed, reason = self._reason(payload)
        self.assertIsNone(parsed)
        self.assertEqual(reason, "duplicate_query_text")

    def test_the_bg3_alternative_is_unreachable(self):
        """`[A-Z]{2,}` already matches the 'BG' in 'BG3', so the branch is dead."""
        import re as _re

        for text in ("BG3 내성굴림", "PHB 규칙"):
            self.assertTrue(_re.search(r"[A-Z]{2,}", text), text)
        self.assertIsNone(_re.search(r"\bBG3\b", "bg3 내성굴림"))

    def test_the_thin_wrapper_still_returns_only_the_parsed_value(self):
        self.assertIsNone(_parse_candidate({"skip": True}))
        self.assertIsNotNone(_parse_candidate(_payload()))


class GenerationReasonTest(unittest.TestCase):
    def _chunk(self):
        return Chunk(
            chunk_id=1,
            document_id=1,
            chunk_number=1,
            title="내성 굴림",
            text="내성 굴림은 d20을 굴려 난이도와 비교한다. " * 8,
            source_url="https://example.invalid/a",
            content_digest="d" * 64,
            retrieved_at="2026-01-01T00:00:00Z",
            source_revision="1",
            license_basis="CC BY-SA 4.0",
        )

    def test_a_repeated_answer_is_recorded_as_a_duplicate_not_a_parse_failure(self):
        with mock.patch(
            "goldset_lab.fixture_builder.generate_json",
            return_value=(_payload(), {"eval_count": 1}),
        ):
            parsed, _, status, reasons = _generate_candidate(
                self._chunk(), endpoint="http://127.0.0.1:0", model="test", seed=1
            )
        self.assertEqual(status, "valid")
        self.assertEqual(len(parsed), 1)
        self.assertEqual(reasons, ["ok", "duplicate_of_previous_pass"])

    def test_a_rejected_response_records_the_gate_for_every_pass(self):
        with mock.patch(
            "goldset_lab.fixture_builder.generate_json",
            return_value=(_payload(queries=_queries(mixed="영어가 없는 혼합")), {}),
        ):
            parsed, _, status, reasons = _generate_candidate(
                self._chunk(), endpoint="http://127.0.0.1:0", model="test", seed=1
            )
        self.assertEqual(status, "invalid")
        self.assertEqual(parsed, [])
        self.assertEqual(reasons, ["mixed_has_no_latin", "mixed_has_no_latin"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
