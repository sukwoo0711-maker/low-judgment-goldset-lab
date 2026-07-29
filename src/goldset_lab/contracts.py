"""Dependency-free validation for public fixtures and atomic review labels."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

YNU = frozenset({"Y", "N", "U"})
U_REASONS = frozenset(
    {"version_unknown", "evidence_conflict", "wording_unclear", "expertise_missing"}
)
PUBLIC_SOURCE_TYPES = frozenset({"public_web", "synthetic_template"})
CANDIDATE_ARMS = frozenset(
    {"lexical", "dense", "entity", "glossary", "oracle", "random", "hard_negative", "full_document"}
)
CANONICAL_LABELS = {
    "C": "corpus_evidence",
    "R": "retrieval_success",
    "A": "answer_support",
    "G": "citation_grounding",
    "X": "abstention_appropriateness",
}
MODE_MINIMUMS = {"smoke": 12, "diagnostic": 32, "full": 1000}


def validate_mode_count(mode: str, count: int) -> None:
    if mode not in MODE_MINIMUMS:
        raise ContractError(f"unknown evaluation mode: {mode}")
    if count < MODE_MINIMUMS[mode]:
        raise ContractError(f"{mode} mode requires at least {MODE_MINIMUMS[mode]} cases")
    if count % 4:
        raise ContractError("case count must be a multiple of four")
ATOMIC_TARGET_TYPES = frozenset(
    {
        "corpus_evidence",
        "retrieval_success",
        "predicate_answered",
        "predicate_supported",
        "claim_supported",
        "contradiction_present",
        "citation_valid",
        "abstention_appropriate",
        "reference_supported",
        "query_natural",
    }
)


class ContractError(ValueError):
    """Raised when an artifact violates the frozen evaluation contract."""


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ContractError(f"{path}:{line_no}: invalid JSON: {exc.msg}") from exc
        if not isinstance(row, dict):
            raise ContractError(f"{path}:{line_no}: object required")
        rows.append(row)
    return rows


def _require(record: dict[str, Any], keys: Iterable[str], context: str) -> None:
    missing = [key for key in keys if record.get(key) in (None, "", [])]
    if missing:
        raise ContractError(f"{context}: missing {', '.join(missing)}")


def _require_strings(record: dict[str, Any], keys: Iterable[str], context: str) -> None:
    invalid = [key for key in keys if not isinstance(record.get(key), str) or not record[key]]
    if invalid:
        raise ContractError(f"{context}: non-empty strings required for {', '.join(invalid)}")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        ch in "0123456789abcdef" for ch in value
    )


def validate_public_fixture(record: dict[str, Any]) -> None:
    """Reject private-derived, untraceable, or ambiguous reference fixtures."""
    _require(
        record,
        (
            "question_id",
            "fact_cluster_id",
            "query",
            "source_type",
            "source_title",
            "retrieved_at",
            "source_revision",
            "content_digest",
            "derivation",
            "license_basis",
            "reference_answer",
            "predicates",
        ),
        "fixture",
    )
    _require_strings(
        record,
        (
            "question_id",
            "fact_cluster_id",
            "query",
            "source_type",
            "source_title",
            "retrieved_at",
            "source_revision",
            "content_digest",
            "derivation",
            "license_basis",
            "reference_answer",
        ),
        "fixture",
    )
    if record["source_type"] not in PUBLIC_SOURCE_TYPES:
        raise ContractError("fixture: source_type must be public_web or synthetic_template")
    if record.get("private_input_used") is not False:
        raise ContractError("fixture: private_input_used must be false")
    if not _is_sha256(record["content_digest"]):
        raise ContractError("fixture: content_digest must be lowercase SHA-256")
    try:
        parsed_time = datetime.fromisoformat(record["retrieved_at"].replace("Z", "+00:00"))
        if parsed_time.tzinfo is None:
            raise ValueError("timezone required")
    except ValueError as exc:
        raise ContractError("fixture: retrieved_at must be ISO-8601") from exc
    if "source_url" in record:
        if not isinstance(record["source_url"], str):
            raise ContractError("fixture: source_url must be a string")
        parsed = urlparse(str(record["source_url"]))
        if parsed.scheme != "https" or not parsed.netloc:
            raise ContractError("fixture: source_url must be an HTTPS URL")
    if record["source_type"] == "public_web":
        _require(record, ("source_url",), "public_web fixture")
    if not isinstance(record["predicates"], list) or not all(
        isinstance(item, dict)
        and set(item) <= {"predicate_id", "text", "importance"}
        and isinstance(item.get("predicate_id"), str)
        and bool(item.get("predicate_id"))
        and isinstance(item.get("text"), str)
        and bool(item.get("text"))
        and item.get("importance", "mandatory") in {"mandatory", "optional", "excluded"}
        for item in record["predicates"]
    ):
        raise ContractError("fixture: predicates require predicate_id and text")


def validate_atomic_label(record: dict[str, Any]) -> None:
    """Validate a single Y/N/U decision without accepting free-form rationale."""
    _require(record, ("question_id", "target_type", "target_id", "value"), "label")
    allowed = {"question_id", "target_type", "target_id", "value", "reason", "elapsed_ms", "decision_source", "confidence"}
    if set(record) - allowed:
        raise ContractError("label: additional properties are prohibited")
    _require_strings(record, ("question_id", "target_type", "target_id", "value"), "label")
    if record["target_type"] not in ATOMIC_TARGET_TYPES:
        raise ContractError("label: unknown target_type")
    if record["value"] not in YNU:
        raise ContractError("label: value must be Y, N, or U")
    if record["value"] == "U" and record.get("reason") not in U_REASONS:
        raise ContractError("label: U requires a fixed reason code")
    if record["value"] != "U" and record.get("reason"):
        raise ContractError("label: reason is allowed only for U")
    if "free_text" in record:
        raise ContractError("label: free_text is prohibited")
    if "elapsed_ms" in record and (
        not isinstance(record["elapsed_ms"], int)
        or isinstance(record["elapsed_ms"], bool)
        or record["elapsed_ms"] < 0
    ):
        raise ContractError("label: elapsed_ms must be a non-negative integer")
    if record.get("decision_source", "human") not in {"human", "deterministic", "local_model_consensus"}:
        raise ContractError("label: unknown decision_source")
    if "confidence" in record and (
        not isinstance(record["confidence"], (int, float))
        or isinstance(record["confidence"], bool)
        or not 0 <= record["confidence"] <= 1
    ):
        raise ContractError("label: confidence must be between zero and one")


def validate_candidate(record: dict[str, Any]) -> None:
    """Preserve candidate provenance and rank before blinded deduplication."""
    _require(
        record,
        ("question_id", "content_id", "arm", "rank_before_dedup", "content_digest"),
        "candidate",
    )
    allowed = {
        "question_id",
        "content_id",
        "arm",
        "rank_before_dedup",
        "content_digest",
        "inspection_receipt",
    }
    if set(record) - allowed:
        raise ContractError("candidate: additional properties are prohibited")
    _require_strings(record, ("question_id", "content_id", "arm", "content_digest"), "candidate")
    if record["arm"] not in CANDIDATE_ARMS:
        raise ContractError("candidate: unknown retrieval arm")
    if not _is_sha256(record["content_digest"]):
        raise ContractError("candidate: content_digest must be lowercase SHA-256")
    if "inspection_receipt" in record and not isinstance(record["inspection_receipt"], str):
        raise ContractError("candidate: inspection_receipt must be a string")
    if (
        not isinstance(record["rank_before_dedup"], int)
        or isinstance(record["rank_before_dedup"], bool)
        or record["rank_before_dedup"] < 1
    ):
        raise ContractError("candidate: rank_before_dedup must be a positive integer")


def scan_canary(canary: str, paths: Iterable[Path]) -> list[str]:
    """Return forbidden artifacts containing an evaluation-only canary."""
    if not canary:
        raise ContractError("canary must not be empty")
    hits = []
    for path in paths:
        if path.is_file() and canary in path.read_text(encoding="utf-8", errors="replace"):
            hits.append(str(path))
    return hits
