from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from goldset_lab.bg3 import Chunk
from goldset_lab.auto_triage import deterministic_rejection
from goldset_lab.fixture_builder import _parse_candidate, _parse_candidates
from goldset_lab.io_utils import file_sha256, write_jsonl
from goldset_lab.local_runner import _attempt_counts, _valid_answer
from goldset_lab.ollama_client import LocalEndpointError, generate_json
from goldset_lab.report import _case_markdown, _validate_full_gate, main as report_main
from goldset_lab.retrieval import BM25
from goldset_lab.review import build_candidate_queue, reduce_events
from goldset_lab.web_reference_builder import _focused_excerpt


def chunk(chunk_id: int, title: str, text: str) -> Chunk:
    return Chunk(chunk_id, 1, chunk_id, title, text, "https://example.invalid", "0" * 64, "2026-07-30T00:00:00+09:00", "rev", "summary")


class PipelineTests(unittest.TestCase):
    def test_full_gate_rejects_same_source_and_nonhuman_or_tampered_labels(self) -> None:
        fixture = {"question_id": "q1", "fact_cluster_id": "f1", "reference_provenance": "independent_web_verified"}
        labels = [
            {"target_type": "reference_supported", "target_id": "f1", "value": "Y", "decision_source": "human"},
            {"target_type": "query_natural", "target_id": "q1", "value": "Y", "decision_source": "human"},
        ]
        manifest = {"labels_sha256": "labels", "label_count": 2}
        _validate_full_gate([fixture], labels, manifest, "labels", {"f1"})
        with self.assertRaises(ValueError):
            _validate_full_gate([{**fixture, "reference_provenance": "same_source_web_refreshed"}], labels, manifest, "labels", {"f1"})
        with self.assertRaises(ValueError):
            _validate_full_gate([fixture], labels, manifest, "tampered", {"f1"})
        labels[0]["decision_source"] = "local_model_consensus"
        with self.assertRaises(ValueError):
            _validate_full_gate([fixture], labels, manifest, "labels", {"f1"})
    def test_web_excerpt_focuses_on_search_terms(self) -> None:
        text = "Unrelated introduction. Another unrelated sentence. A natural 20 succeeds and a natural 1 fails."
        excerpt = _focused_excerpt(text, "natural 20 1", limit=100)
        self.assertIn("natural 20", excerpt)
    def test_deterministic_triage_rejects_non_question_and_answer_leak(self) -> None:
        self.assertFalse(deterministic_rejection("그림자 저주 해제 방법", "별도 답변 문장"))
        self.assertTrue(deterministic_rejection("정답은 긴정답문장그대로입니다 맞나요?", "긴정답문장그대로입니다"))
        self.assertFalse(deterministic_rejection("BG3에서 행동을 어떻게 사용하나요?", "행동을 사용한다"))
    def test_bm25_prefers_matching_chunk(self) -> None:
        engine = BM25([chunk(1, "A", "turn based combat"), chunk(2, "B", "romance companion")])
        self.assertEqual(engine.search("turn combat", 1)[0].chunk.chunk_id, 1)

    def test_fixture_candidate_requires_four_distinct_kinds(self) -> None:
        valid = {
            "reference_answer": "answer",
            "predicate": "fact",
            "queries": [
                {"kind": "natural", "text": "q1"},
                {"kind": "mixed", "text": "BG3 query"},
                {"kind": "spacing_abbreviation", "text": "BG3 q3"},
                {"kind": "paraphrase", "text": "q4"},
            ],
        }
        self.assertIsNotNone(_parse_candidate(valid))
        valid["queries"][3]["kind"] = "natural"
        self.assertIsNone(_parse_candidate(valid))

    def test_multi_candidate_parser_accepts_two_distinct_facts(self) -> None:
        def candidate(answer: str) -> dict:
            return {"reference_answer": answer, "predicate": answer, "queries": [
                {"kind": "natural", "text": answer + " 질문1"},
                {"kind": "mixed", "text": "BG3 " + answer + " query"},
                {"kind": "spacing_abbreviation", "text": "BG3 " + answer + " 질문3"},
                {"kind": "paraphrase", "text": answer + " 질문4"},
            ]}
        self.assertEqual(len(_parse_candidates({"candidates": [candidate("fact one"), candidate("fact two")]})), 2)

    def test_answer_citations_must_be_from_retrieval(self) -> None:
        self.assertTrue(_valid_answer({"status": "answered", "answer": "a", "citations": ["sha256:a"]}, {"sha256:a"}))
        self.assertFalse(_valid_answer({"status": "answered", "answer": "a", "citations": ["sha256:b"]}, {"sha256:a"}))

    def test_attempt_counts_ignore_other_runs_and_successes(self) -> None:
        rows = [
            {"question_id": "q1", "status": "error", "run_fingerprint": "run"},
            {"question_id": "q1", "status": "error", "run_fingerprint": "run"},
            {"question_id": "q1", "status": "success", "run_fingerprint": "run"},
            {"question_id": "q1", "status": "error", "run_fingerprint": "other"},
        ]
        self.assertEqual(_attempt_counts(rows, "run"), {"q1": 2})

    def test_non_loopback_model_endpoint_is_rejected_before_network(self) -> None:
        with self.assertRaises(LocalEndpointError):
            generate_json(endpoint="https://example.com", model="x", system="s", prompt="p")

    def test_report_contains_all_1000_case_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = []
            results = []
            labels = []
            for number in range(1, 1001):
                question_id = f"q-{number:04d}"
                fixtures.append(
                    {
                        "question_id": question_id,
                        "fact_cluster_id": f"f-{(number - 1) // 4:04d}",
                        "query": f"query {number}",
                        "query_kind": "natural",
                        "reference_answer": f"internet answer {number}",
                        "source_url": "https://example.invalid",
                        "source_title": "source",
                        "retrieved_at": "2026-07-30T00:00:00+09:00",
                        "source_revision": "rev",
                        "content_digest": "0" * 64,
                    }
                )
                results.append(
                    {
                        "question_id": question_id,
                        "retrieval": [],
                        "local_answer": {"status": "abstain", "answer": "", "citations": []},
                    }
                )
                if (number - 1) % 4 == 0:
                    labels.append({"question_id": question_id, "target_type": "reference_supported", "target_id": f"f-{(number - 1) // 4:04d}", "value": "Y"})
            fixture_path, result_path, label_path = root / "fixtures.jsonl", root / "results.jsonl", root / "labels.jsonl"
            write_jsonl(fixture_path, fixtures)
            write_jsonl(result_path, results)
            write_jsonl(label_path, labels)
            review_manifest = root / "review-manifest.json"
            review_manifest.write_text(json.dumps({"fixtures_sha256": file_sha256(fixture_path)}), encoding="utf-8")
            self.assertEqual(report_main(["--fixtures", str(fixture_path), "--results", str(result_path), "--labels", str(label_path), "--review-manifest", str(review_manifest), "--out-dir", str(root / "report"), "--mode", "smoke"]), 0)
            rendered = "".join(path.read_text(encoding="utf-8") for path in (root / "report").glob("cases-*.md"))
            self.assertEqual(rendered.count("\n## q-"), 1000)
            self.assertIn("QUERY: query 1", rendered)
            self.assertIn("APPROVED SYNTHETIC SELF-RETRIEVAL REFERENCE: internet answer 1000", rendered)
            self.assertNotIn("INDEPENDENT INTERNET REFERENCE ANSWER", rendered)

    def test_independent_web_reference_has_explicit_label(self) -> None:
        fixture = {
            "question_id": "q1", "fact_cluster_id": "f1", "query_kind": "natural",
            "query": "q", "reference_answer": "a", "predicates": [],
            "source_url": "https://example.invalid", "source_title": "source",
            "retrieved_at": "now", "source_revision": "rev", "content_digest": "0" * 64,
            "reference_provenance": "independent_web_verified",
        }
        result = {"retrieval": [], "local_answer": {"status": "abstain", "answer": "", "citations": []}}
        rendered = _case_markdown(fixture, result, {("reference_supported", "f1"): "Y"}, "full", {"f1"})
        self.assertIn("INDEPENDENT INTERNET REFERENCE ANSWER", rendered)

    def test_review_queue_deduplicates_reference_by_cluster(self) -> None:
        fixtures = [
            {"question_id": "q1", "fact_cluster_id": "f1", "query": "a", "query_kind": "natural", "reference_answer": "x", "predicates": [], "evidence_excerpt": "e"},
            {"question_id": "q2", "fact_cluster_id": "f1", "query": "b", "query_kind": "mixed", "reference_answer": "x", "predicates": [], "evidence_excerpt": "e"},
        ]
        queue = build_candidate_queue(fixtures)
        self.assertEqual([item.target_type for item in queue], ["reference_supported", "query_natural", "query_natural"])

    def test_review_events_resume_and_undo(self) -> None:
        label = {"action": "label", "question_id": "q", "target_type": "query_natural", "target_id": "q", "value": "Y", "elapsed_ms": 1}
        active, _ = reduce_events([label])
        self.assertEqual(len(active), 1)
        undo = {"action": "undo", "question_id": "q", "target_type": "query_natural", "target_id": "q"}
        active, _ = reduce_events([label, undo])
        self.assertEqual(active, {})


if __name__ == "__main__":
    unittest.main()
