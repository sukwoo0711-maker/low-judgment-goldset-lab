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
    fixture: dict[str, Any], result: dict[str, Any], labels: dict[tuple[str, str], str]
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
    reference_label = (
        "INTERNET REFERENCE ANSWER"
        if review == "Y"
        else "GENERATED REFERENCE CANDIDATE (not independently approved)"
    )
    predicates = "; ".join(item["text"] for item in fixture.get("predicates", [])) or "없음"
    return "\n".join(
        [
            f"## {fixture['question_id']} · {_cell(fixture['query_kind'])}",
            "",
            f"- fact cluster: `{_cell(fixture['fact_cluster_id'])}`",
            f"- QUERY: {_cell(fixture['query'])}",
            "- RESULT:",
            *[f"  {line}" for line in result_lines],
            f"- LOCAL ANSWER: [{_cell(answer.get('status', 'missing'))}] {_cell(answer.get('answer', ''))}",
            f"- LOCAL CITATIONS: {citations}",
            f"- {reference_label}: {_cell(fixture['reference_answer'])}",
            f"- REFERENCE PREDICATES: {_cell(predicates)}",
            f"- REFERENCE REVIEW: {_cell(review)}",
            f"- REFERENCE: [{_cell(fixture['source_title'])}]({source}) · retrieved {_cell(fixture['retrieved_at'])} · revision {_cell(fixture['source_revision'])} · digest `{_cell(fixture['content_digest'])}`",
            f"- RUN: elapsed_ms={_cell(result.get('elapsed_ms', ''))} · error={_cell(result.get('error', ''))} · usage={_cell(result.get('usage', {}))}",
            f"- LABELS: reference={_cell(review)}, query_natural={_cell(labels.get(('query_natural', fixture['question_id']), 'pending'))}",
            "",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--review-manifest", type=Path)
    parser.add_argument("--cases-per-file", type=int, default=100)
    parser.add_argument("--mode", choices=("smoke", "diagnostic", "full"), default="full")
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
        body = ["# 질의별 상세 결과", ""]
        body.extend(_case_markdown(item, result_by_id[item["question_id"]], labels) for item in batch)
        path = args.out_dir / filename
        path.write_text("\n".join(body), encoding="utf-8", newline="\n")
        index_lines.append(f"- [{filename}]({filename}) — {len(batch)}건 · SHA-256 `{file_sha256(path)}`")
    index = args.out_dir / "README.md"
    index.write_text("\n".join(index_lines) + "\n", encoding="utf-8", newline="\n")
    print(f"rendered={len(fixtures)} index={index} sha256={file_sha256(index)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
