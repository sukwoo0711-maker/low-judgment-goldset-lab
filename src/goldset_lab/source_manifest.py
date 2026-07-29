"""Fail-closed validation of an approved public SQLite snapshot."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from .bg3 import all_source_urls
from .contracts import ContractError
from .io_utils import file_sha256


def load_and_validate(path: Path, db_path: Path, *, trusted_manifest_sha256: str | None = None) -> dict:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"invalid source manifest: {exc}") from exc
    required = {
        "schema_version",
        "snapshot_sha256",
        "source_type",
        "allowed_domains",
        "license_basis",
        "approved_for_public_test",
        "contains_private_data",
    }
    if not isinstance(manifest, dict) or required - set(manifest):
        raise ContractError("source manifest is missing required fields")
    if manifest["schema_version"] != 1 or manifest["source_type"] != "public_web_snapshot":
        raise ContractError("unsupported source manifest")
    if manifest["approved_for_public_test"] is not True or manifest["contains_private_data"] is not False:
        raise ContractError("snapshot is not approved as public-only test data")
    if not trusted_manifest_sha256 or file_sha256(path) != trusted_manifest_sha256:
        raise ContractError("source manifest is not in the externally trusted SHA-256 allowlist")
    if file_sha256(db_path) != manifest["snapshot_sha256"]:
        raise ContractError("snapshot SHA-256 does not match source manifest")
    domains = manifest["allowed_domains"]
    if not isinstance(domains, list) or not domains or any(not isinstance(item, str) for item in domains):
        raise ContractError("allowed_domains must be a non-empty string list")
    for source_url in all_source_urls(db_path):
        if urlparse(source_url).hostname not in domains:
            raise ContractError(f"source domain is not approved: {source_url}")
    return manifest
