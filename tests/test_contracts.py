from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from goldset_lab.contracts import (
    ContractError,
    scan_canary,
    sha256_text,
    validate_atomic_label,
    validate_candidate,
    validate_public_fixture,
    validate_mode_count,
)


def fixture() -> dict:
    passage = "Public evidence passage."
    return {
        "question_id": "q-001",
        "fact_cluster_id": "f-001",
        "query": "public question",
        "source_type": "public_web",
        "source_url": "https://example.invalid/reference",
        "source_title": "Public source",
        "retrieved_at": "2026-07-30T00:00:00+09:00",
        "source_revision": "rev-1",
        "content_digest": sha256_text(passage),
        "derivation": "Question derived only from the cited public passage.",
        "private_input_used": False,
        "license_basis": "Short factual summary with source attribution.",
        "reference_answer": "A bounded factual summary.",
        "predicates": [{"predicate_id": "p-001", "text": "A public fact."}],
    }


class ContractTests(unittest.TestCase):
    def test_mode_minimums_are_explicit(self) -> None:
        validate_mode_count("smoke", 12)
        validate_mode_count("diagnostic", 32)
        validate_mode_count("full", 1000)
        with self.assertRaises(ContractError):
            validate_mode_count("smoke", 8)
    def test_public_fixture_is_accepted(self) -> None:
        validate_public_fixture(fixture())

    def test_private_derived_fixture_is_rejected(self) -> None:
        row = fixture()
        row["private_input_used"] = True
        with self.assertRaises(ContractError):
            validate_public_fixture(row)

    def test_public_fixture_requires_https_and_digest(self) -> None:
        row = fixture()
        row["source_url"] = "http://example.invalid/reference"
        with self.assertRaises(ContractError):
            validate_public_fixture(row)
        row = fixture()
        row["content_digest"] = "not-a-hash"
        with self.assertRaises(ContractError):
            validate_public_fixture(row)

    def test_unknown_label_requires_fixed_reason(self) -> None:
        row = {"question_id": "q", "target_type": "corpus_evidence", "target_id": "p", "value": "U"}
        with self.assertRaises(ContractError):
            validate_atomic_label(row)
        row["reason"] = "version_unknown"
        validate_atomic_label(row)

    def test_free_text_label_is_rejected(self) -> None:
        row = {"question_id": "q", "target_type": "citation_valid", "target_id": "c", "value": "Y", "free_text": "because"}
        with self.assertRaises(ContractError):
            validate_atomic_label(row)

    def test_unknown_target_type_is_rejected(self) -> None:
        row = {"question_id": "q", "target_type": "A", "target_id": "p", "value": "Y"}
        with self.assertRaises(ContractError):
            validate_atomic_label(row)

    def test_negative_elapsed_time_is_rejected(self) -> None:
        row = {"question_id": "q", "target_type": "citation_valid", "target_id": "c", "value": "Y", "elapsed_ms": -1}
        with self.assertRaises(ContractError):
            validate_atomic_label(row)

    def test_candidate_preserves_pre_dedup_rank(self) -> None:
        row = {"question_id": "q", "content_id": "sha256:x", "arm": "lexical", "rank_before_dedup": 1, "content_digest": "0" * 64}
        validate_candidate(row)
        row["rank_before_dedup"] = 0
        with self.assertRaises(ContractError):
            validate_candidate(row)

    def test_candidate_rejects_unknown_arm_and_invalid_digest(self) -> None:
        row = {"question_id": "q", "content_id": "sha256:x", "arm": "magic", "rank_before_dedup": 1, "content_digest": "0" * 64}
        with self.assertRaises(ContractError):
            validate_candidate(row)
        row["arm"] = "lexical"
        row["content_digest"] = "bad"
        with self.assertRaises(ContractError):
            validate_candidate(row)

    def test_fixture_rejects_invalid_predicate_importance(self) -> None:
        row = fixture()
        row["predicates"][0]["importance"] = "maybe"
        with self.assertRaises(ContractError):
            validate_public_fixture(row)

    def test_canary_scanner_detects_leak(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clean = Path(directory, "clean.txt")
            leaked = Path(directory, "leaked.txt")
            clean.write_text("question only", encoding="utf-8")
            leaked.write_text("ORACLE_CANARY_123", encoding="utf-8")
            self.assertEqual(scan_canary("ORACLE_CANARY_123", [clean]), [])
            self.assertEqual(scan_canary("ORACLE_CANARY_123", [clean, leaked]), [str(leaked)])


if __name__ == "__main__":
    unittest.main()
