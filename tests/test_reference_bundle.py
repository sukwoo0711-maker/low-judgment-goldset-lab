from __future__ import annotations

import unittest
import hashlib

from goldset_lab.contracts import ContractError
from goldset_lab.reference_bundle import _enrich_fixture, validate_reference_bundle


class ReferenceBundleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixtures = [{"fact_cluster_id": "f1", "content_digest": "1" * 64}]
        self.rows = [{
            "fact_cluster_id": "f1", "reference_answer": "answer",
            "source_url": "https://example.org/page", "source_title": "title",
            "retrieved_at": "2026-07-30T00:00:00Z", "source_revision": "r2",
            "content_digest": "2" * 64, "evidence_excerpt": "evidence",
            "evidence_digest": hashlib.sha256(b"evidence").hexdigest(),
            "license_basis": "public license", "private_input_used": False,
            "source_independence": "different_domain",
            "predicates": [{"predicate_id": "f1-p1", "text": "fact", "importance": "mandatory"}],
        }]
        self.manifest = {
            "bundle_sha256": "b" * 64, "collection_method": "independent_public_web",
            "local_corpus_sha256": "a" * 64, "reference_snapshot_sha256": "c" * 64,
            "allowed_domains": ["example.org"],
            "public_only": True, "private_input_used": False, "license_basis": "public license",
        }

    def test_accepts_separate_allowlisted_snapshot(self) -> None:
        result = validate_reference_bundle(
            self.fixtures, self.rows, self.manifest, bundle_sha256="b" * 64,
            trusted_manifest_sha256="m" * 64, manifest_sha256="m" * 64,
        )
        self.assertEqual(set(result), {"f1"})

    def test_rejects_same_evidence_digest(self) -> None:
        self.rows[0]["content_digest"] = "1" * 64
        with self.assertRaises(ContractError):
            validate_reference_bundle(
                self.fixtures, self.rows, self.manifest, bundle_sha256="b" * 64,
                trusted_manifest_sha256="m" * 64, manifest_sha256="m" * 64,
            )

    def test_rejects_unapproved_manifest(self) -> None:
        with self.assertRaises(ContractError):
            validate_reference_bundle(
                self.fixtures, self.rows, self.manifest, bundle_sha256="b" * 64,
                trusted_manifest_sha256="x" * 64, manifest_sha256="m" * 64,
            )

    def test_enrichment_uses_reference_license_and_preserves_lineage(self) -> None:
        fixture = {"license_basis": "old license", "reference_review": "Y", "parent_fixture_sha256": "old"}
        reference = dict(self.rows[0])
        enriched = _enrich_fixture(fixture, reference, "m" * 64)
        self.assertEqual(enriched["license_basis"], "public license")
        self.assertEqual(enriched["original_fixture_license_basis"], "old license")
        self.assertNotIn("reference_review", enriched)
        self.assertNotIn("parent_fixture_sha256", enriched)


if __name__ == "__main__":
    unittest.main()
