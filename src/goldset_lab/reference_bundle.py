"""Validate and join a separately collected public-web reference bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .contracts import ContractError, load_jsonl
from .io_utils import file_sha256, write_jsonl


REQUIRED_ROW_KEYS = {
    "fact_cluster_id", "reference_answer", "source_url", "source_title",
    "retrieved_at", "source_revision", "content_digest", "evidence_excerpt",
    "evidence_digest", "license_basis", "private_input_used", "predicates",
    "source_independence",
}


def _sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _normalized_digest(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def validate_reference_bundle(
    fixtures: list[dict], rows: list[dict], manifest: dict, *,
    bundle_sha256: str, trusted_manifest_sha256: str, manifest_sha256: str,
) -> dict[str, dict]:
    if manifest_sha256 != trusted_manifest_sha256:
        raise ContractError("reference manifest hash is not operator-approved")
    if manifest.get("bundle_sha256") != bundle_sha256:
        raise ContractError("reference bundle hash mismatch")
    if manifest.get("collection_method") != "independent_public_web":
        raise ContractError("reference collection method is not independent public web")
    if manifest.get("public_only") is not True or manifest.get("private_input_used") is not False:
        raise ContractError("reference manifest must attest public-only input")
    if not isinstance(manifest.get("license_basis"), str) or not manifest["license_basis"]:
        raise ContractError("reference manifest requires a license basis")
    local_sha = manifest.get("local_corpus_sha256")
    reference_sha = manifest.get("reference_snapshot_sha256")
    if not _sha256(local_sha) or not _sha256(reference_sha) or local_sha == reference_sha:
        raise ContractError("reference snapshot must differ from the local corpus snapshot")
    domains = set(manifest.get("allowed_domains", []))
    if not domains:
        raise ContractError("reference manifest requires allowed domains")
    fixture_by_cluster: dict[str, dict] = {}
    for fixture in fixtures:
        fixture_by_cluster.setdefault(fixture["fact_cluster_id"], fixture)
    output: dict[str, dict] = {}
    for row in rows:
        missing = REQUIRED_ROW_KEYS - set(row)
        if missing:
            raise ContractError(f"reference row missing: {', '.join(sorted(missing))}")
        cluster = row["fact_cluster_id"]
        if cluster not in fixture_by_cluster or cluster in output:
            raise ContractError("reference cluster is unknown or duplicated")
        domain = (urlparse(row["source_url"]).hostname or "").lower()
        if urlparse(row["source_url"]).scheme != "https" or domain not in domains:
            raise ContractError("reference URL domain is not allowlisted")
        digest = row["content_digest"]
        if not _sha256(digest):
            raise ContractError("reference content digest must be SHA-256")
        if row.get("evidence_digest") != hashlib.sha256(row["evidence_excerpt"].encode("utf-8")).hexdigest():
            raise ContractError("reference evidence digest does not match exact UTF-8 excerpt bytes")
        try:
            timestamp = datetime.fromisoformat(row["retrieved_at"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ContractError("reference retrieved_at must be timezone-aware ISO-8601") from exc
        if row.get("private_input_used") is not False or not isinstance(row.get("license_basis"), str) or not row["license_basis"]:
            raise ContractError("reference row must be public-only with a license basis")
        if row.get("source_independence") not in {"different_domain", "same_source_new_revision"}:
            raise ContractError("reference row requires a source independence classification")
        predicates = row.get("predicates")
        if not isinstance(predicates, list) or not predicates or any(
            not isinstance(item, dict) or not isinstance(item.get("text"), str)
            or item.get("importance", "mandatory") not in {"mandatory", "optional", "excluded"}
            for item in predicates
        ):
            raise ContractError("reference row requires atomic predicates")
        if digest == fixture_by_cluster[cluster].get("content_digest"):
            raise ContractError("reference evidence is byte-identical to fixture evidence")
        if _normalized_digest(row["evidence_excerpt"]) == _normalized_digest(fixture_by_cluster[cluster].get("evidence_excerpt", "")):
            raise ContractError("reference evidence is normalized-identical to fixture evidence")
        output[cluster] = row
    return output


def _enrich_fixture(fixture: dict, reference: dict, manifest_sha256: str) -> dict:
    row = {key: value for key, value in fixture.items() if key not in {"reference_review", "query_natural_review", "parent_fixture_sha256"}}
    row.update({
        "reference_answer": reference["reference_answer"],
        "source_url": reference["source_url"],
        "source_title": reference["source_title"],
        "retrieved_at": reference["retrieved_at"],
        "source_revision": reference["source_revision"],
        "content_digest": reference["content_digest"],
        "evidence_excerpt": reference["evidence_excerpt"],
        "predicates": reference["predicates"],
        "original_fixture_license_basis": fixture.get("license_basis"),
        "license_basis": reference["license_basis"],
        "reference_provenance": "independent_web_verified" if reference["source_independence"] == "different_domain" else "same_source_web_refreshed",
        "reference_source_independence": reference["source_independence"],
        "reference_manifest_sha256": manifest_sha256,
        "review_required_after_reference_join": True,
    })
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--trusted-manifest-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--receipt", required=True, type=Path)
    args = parser.parse_args(argv)
    fixtures = load_jsonl(args.fixtures)
    rows = load_jsonl(args.bundle)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    references = validate_reference_bundle(
        fixtures, rows, manifest,
        bundle_sha256=file_sha256(args.bundle),
        trusted_manifest_sha256=args.trusted_manifest_sha256,
        manifest_sha256=file_sha256(args.manifest),
    )
    enriched = []
    for fixture in fixtures:
        row = {key: value for key, value in fixture.items() if key not in {"reference_review", "query_natural_review", "parent_fixture_sha256"}}
        reference = references.get(fixture["fact_cluster_id"])
        if reference:
            row = _enrich_fixture(fixture, reference, file_sha256(args.manifest))
        enriched.append(row)
    write_jsonl(args.output, enriched)
    receipt = {
        "schema_version": 1,
        "enriched_fixtures_sha256": file_sha256(args.output),
        "input_fixtures_sha256": file_sha256(args.fixtures),
        "bundle_sha256": file_sha256(args.bundle),
        "reference_manifest_sha256": file_sha256(args.manifest),
        "trusted_reference_manifest_sha256": args.trusted_manifest_sha256,
        "independent_clusters": sorted(cluster for cluster, row in references.items() if row["source_independence"] == "different_domain"),
        "same_source_refreshed_clusters": sorted(cluster for cluster, row in references.items() if row["source_independence"] == "same_source_new_revision"),
        "verification_boundary": "operator-approved manifest plus deterministic validation; not mathematical semantic independence",
    }
    args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"joined={len(references)} clusters output={len(enriched)} sha256={file_sha256(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
