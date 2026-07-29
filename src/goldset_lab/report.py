"""Join frozen references after local QA and render every case to Markdown."""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any

from .contracts import load_jsonl, validate_atomic_label, validate_mode_count
from .io_utils import file_sha256


def _cell(value: Any) -> str:
    return html.escape(str(value), quote=False)


def _case_markdown(
    fixture: dict[str, Any], result: dict[str, Any], labels: dict[tuple[str, str], str], mode: str,
    trusted_independent_clusters: set[str] | None = None,
) -> str:
    retrieval = result.get("retrieval", [])
    result_lines = []
    for hit in retrieval:
        result_lines.append(
            f"{hit['rank']}. `{_cell(hit['content_id'])}` ({_cell(hit['arm'])}, score={hit['score']}) — {_cell(hit['title'])}: {_cell(hit['excerpt'])}"
        )
    if not result_lines:
        result_lines = ["검색 결과 없음"]
    answer = result.get("local_answer", {})
    citations = ", ".join(f"`{_cell(item)}`" for item in answer.get("citations", [])) or "없음"
    source = fixture.get("source_url", "")
    review = labels.get(("reference_supported", fixture["fact_cluster_id"]), "pending")
    if review != "Y":
        reference_label = "GENERATED REFERENCE CANDIDATE (not independently approved)"
    elif fixture.get("reference_provenance") == "independent_web_verified" and fixture["fact_cluster_id"] in (trusted_independent_clusters or set()):
        reference_label = "INDEPENDENT INTERNET REFERENCE ANSWER"
    elif fixture.get("reference_provenance") == "same_source_web_refreshed":
        reference_label = "REFRESHED SAME-SOURCE INTERNET REFERENCE ANSWER"
    else:
        reference_label = "APPROVED SYNTHETIC SELF-RETRIEVAL REFERENCE"
    predicates = "; ".join(item["text"] for item in fixture.get("predicates", [])) or "없음"
    return "\n".join(
        [
            f"## {fixture['question_id']} · {_cell(fixture['query_kind'])}",
            "",
            f"- fact cluster: `{_cell(fixture['fact_cluster_id'])}`",
            f"- QUERY: {_cell(fixture['query'])}",
            "- RESULT:",
            *[f"  {line}" for line in result_lines],
            f"- LOCAL ANSWER: [{_cell(answer.get('status', 'missing'))}] {_cell(answer.get('answer', ''))}".rstrip(),
            f"- LOCAL CITATIONS: {citations}",
            f"- {reference_label}: {_cell(fixture['reference_answer'])}",
            f"- REFERENCE PREDICATES: {_cell(predicates)}",
            f"- REFERENCE REVIEW: {_cell(review)}",
            f"- REFERENCE: [{_cell(fixture['source_title'])}]({source}) · retrieved {_cell(fixture['retrieved_at'])} · revision {_cell(fixture['source_revision'])} · digest `{_cell(fixture['content_digest'])}`",
            f"- SOURCE LICENSE: {_cell(fixture.get('license_basis', '확인 필요'))}",
            f"- RUN: elapsed_ms={_cell(result.get('elapsed_ms', ''))} · error={_cell(result.get('error', ''))} · usage={_cell(result.get('usage', {}))}",
            f"- LABELS: reference={_cell(review)}, query_natural={_cell(labels.get(('query_natural', fixture['question_id']), 'pending'))}",
            "",
        ]
    )


def _validate_full_gate(
    fixtures: list[dict[str, Any]], label_rows: list[dict[str, Any]],
    review_manifest: dict[str, Any], labels_sha256: str,
    trusted_independent_clusters: set[str],
) -> None:
    fixture_clusters = {row["fact_cluster_id"] for row in fixtures}
    if fixture_clusters != trusted_independent_clusters or any(row.get("reference_provenance") != "independent_web_verified" for row in fixtures):
        raise ValueError("full report requires an independent reference for every cluster")
    if review_manifest.get("labels_sha256") != labels_sha256 or review_manifest.get("label_count") != len(label_rows):
        raise ValueError("full report labels are not bound to the review manifest")
    labels = {(row["target_type"], row["target_id"]): row["value"] for row in label_rows}
    sources = {(row["target_type"], row["target_id"]): row.get("decision_source", "human") for row in label_rows}
    if any(labels.get(("reference_supported", row["fact_cluster_id"])) != "Y" or labels.get(("query_natural", row["question_id"])) != "Y" for row in fixtures):
        raise ValueError("full report requires Y review for every reference and query")
    if any(sources.get(("reference_supported", row["fact_cluster_id"])) != "human" or sources.get(("query_natural", row["question_id"])) != "human" for row in fixtures):
        raise ValueError("full report requires human Y review for every reference and query")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--review-manifest", type=Path)
    parser.add_argument("--cases-per-file", type=int, default=100)
    parser.add_argument("--mode", choices=("smoke", "diagnostic", "full"), default="full")
    parser.add_argument("--reference-receipt", type=Path)
    parser.add_argument("--trusted-reference-receipt-sha256")
    args = parser.parse_args(argv)
    fixtures = load_jsonl(args.fixtures)
    results = load_jsonl(args.results)
    validate_mode_count(args.mode, len(fixtures))
    validate_mode_count(args.mode, len(results))
    fixture_ids = [row["question_id"] for row in fixtures]
    result_by_id = {row["question_id"]: row for row in results}
    if len(result_by_id) != len(results) or set(fixture_ids) != set(result_by_id):
        raise SystemExit("fixture/result question ID sets must match exactly")
    if args.out_dir.exists() and any(args.out_dir.iterdir()):
        raise SystemExit("report output directory must be empty or absent")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    label_rows = load_jsonl(args.labels) if args.labels and args.labels.exists() else []
    review_manifest: dict[str, Any] = {}
    if label_rows:
        if not args.review_manifest or not args.review_manifest.exists():
            raise SystemExit("labels require a review manifest")
        review_manifest = json.loads(args.review_manifest.read_text(encoding="utf-8"))
        direct_match = review_manifest.get("fixtures_sha256") == file_sha256(args.fixtures)
        derivative_match = bool(fixtures) and all(
            row.get("parent_fixture_sha256") == review_manifest.get("fixtures_sha256")
            for row in fixtures
        )
        if not direct_match and not derivative_match:
            raise SystemExit("review manifest is not bound to this fixture file")
    for row in label_rows:
        validate_atomic_label(row)
    labels = {(row["target_type"], row["target_id"]): row["value"] for row in label_rows}
    label_sources = {(row["target_type"], row["target_id"]): row.get("decision_source", "human") for row in label_rows}
    independent_clusters = {
        row["fact_cluster_id"] for row in fixtures
        if row.get("reference_provenance") == "independent_web_verified"
    }
    if independent_clusters and review_manifest.get("fixtures_sha256") != file_sha256(args.fixtures):
        raise SystemExit("independent references require a fresh review directly bound to enriched fixtures")
    trusted_independent_clusters: set[str] = set()
    if independent_clusters:
        if not args.reference_receipt or not args.trusted_reference_receipt_sha256:
            raise SystemExit("independent references require an operator-approved receipt")
        if file_sha256(args.reference_receipt) != args.trusted_reference_receipt_sha256:
            raise SystemExit("reference receipt hash is not operator-approved")
        receipt = json.loads(args.reference_receipt.read_text(encoding="utf-8"))
        if receipt.get("enriched_fixtures_sha256") != file_sha256(args.fixtures):
            raise SystemExit("reference receipt is not bound to the fixture file")
        trusted_independent_clusters = set(receipt.get("independent_clusters", []))
        if independent_clusters - trusted_independent_clusters:
            raise SystemExit("fixture contains unreceipted independent references")
    if args.mode == "full":
        try:
            _validate_full_gate(fixtures, label_rows, review_manifest, file_sha256(args.labels), trusted_independent_clusters)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    index_lines = [
        "# 전체 질의 결과",
        "",
        f"실행 모드: `{args.mode}`",
        f"총 질의: {len(fixtures)}",
        f"품질 주장 금지: {'예' if args.mode != 'full' else '아니요'}",
        "",
    ]
    for start in range(0, len(fixtures), args.cases_per_file):
        batch = fixtures[start : start + args.cases_per_file]
        filename = f"cases-{start + 1:04d}-{start + len(batch):04d}.md"
        body = [
            "# 질의별 상세 결과",
            "",
            f"> 이 파일은 `{args.mode}` 실행의 {len(batch)}개 질의입니다. 독립 인터넷 기준답 실험으로 간주하지 않으며, full run 이전에는 품질 주장을 금지합니다.",
            "",
            "> 데이터 라이선스는 각 case의 SOURCE LICENSE를 따릅니다. 저장소의 MIT 라이선스는 코드에만 적용됩니다.",
            "",
        ]
        body.extend(_case_markdown(item, result_by_id[item["question_id"]], labels, args.mode, trusted_independent_clusters) for item in batch)
        path = args.out_dir / filename
        path.write_text("\n".join(body), encoding="utf-8", newline="\n")
        index_lines.append(f"- [{filename}]({filename}) — {len(batch)}건 · SHA-256 `{file_sha256(path)}`")
    index = args.out_dir / "README.md"
    index.write_text("\n".join(index_lines) + "\n", encoding="utf-8", newline="\n")
    print(f"rendered={len(fixtures)} index={index} sha256={file_sha256(index)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
