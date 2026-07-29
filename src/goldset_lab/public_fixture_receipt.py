"""Bind generated fixtures to an operator-approved public source manifest."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import load_jsonl
from .io_utils import file_sha256


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--fixture-run-manifest", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--trusted-source-manifest-sha256", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    source_sha = file_sha256(args.source_manifest)
    if source_sha != args.trusted_source_manifest_sha256:
        raise SystemExit("source manifest hash is not operator-approved")
    run_manifest = json.loads(args.fixture_run_manifest.read_text(encoding="utf-8"))
    if run_manifest.get("source_manifest_sha256") != source_sha:
        raise SystemExit("fixture run is not bound to the approved source manifest")
    fixtures = load_jsonl(args.fixtures)
    if not fixtures or any(row.get("run_fingerprint") != run_manifest.get("run_fingerprint") or row.get("private_input_used") is not False or row.get("source_type") != "public_web" for row in fixtures):
        raise SystemExit("fixtures are not a single public-only run")
    receipt = {
        "schema_version": 1,
        "fixtures_sha256": file_sha256(args.fixtures),
        "fixture_run_manifest_sha256": file_sha256(args.fixture_run_manifest),
        "source_manifest_sha256": source_sha,
        "trusted_source_manifest_sha256": args.trusted_source_manifest_sha256,
        "public_only": True,
        "private_input_used": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"receipt={args.output} sha256={file_sha256(args.output)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
