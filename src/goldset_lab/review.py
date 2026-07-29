"""One-decision-at-a-time Y/N/U review with append-only resume and undo."""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .contracts import U_REASONS, load_jsonl, validate_atomic_label
from .io_utils import file_sha256, object_sha256, stable_json, write_jsonl


@dataclass(frozen=True)
class ReviewItem:
    question_id: str
    target_type: str
    target_id: str
    title: str
    evidence: str

    @property
    def key(self) -> tuple[str, str, str]:
        return self.question_id, self.target_type, self.target_id


def build_candidate_queue(fixtures: list[dict[str, Any]]) -> list[ReviewItem]:
    queue: list[ReviewItem] = []
    seen_clusters: set[str] = set()
    for row in fixtures:
        cluster = row["fact_cluster_id"]
        if cluster not in seen_clusters:
            predicate = "; ".join(item["text"] for item in row.get("predicates", []))
            queue.append(
                ReviewItem(
                    row["question_id"],
                    "reference_supported",
                    cluster,
                    "공개 근거가 기준답 후보를 직접 뒷받침합니까?",
                    f"근거: {row.get('evidence_excerpt', '')}\n기준답 후보: {row['reference_answer']}\npredicate: {predicate}",
                )
            )
            seen_clusters.add(cluster)
        queue.append(
            ReviewItem(
                row["question_id"],
                "query_natural",
                row["question_id"],
                "이 질의가 표시된 기준답을 묻는 자연스러운 표현입니까?",
                f"질의: {row['query']}\n기준답 후보: {row['reference_answer']}\n유형: {row.get('query_kind', '')}",
            )
        )
    return queue


def reduce_events(events: list[dict[str, Any]]) -> tuple[dict[tuple[str, str, str], dict[str, Any]], list[tuple[str, str, str]]]:
    active: dict[tuple[str, str, str], dict[str, Any]] = {}
    order: list[tuple[str, str, str]] = []
    for event in events:
        if event.get("action") not in {"label", "undo"}:
            raise ValueError("unknown review event action")
        key = tuple(event[part] for part in ("question_id", "target_type", "target_id"))
        if event.get("action") == "undo":
            active.pop(key, None)
            if key in order:
                order.remove(key)
        elif event.get("action") == "label":
            label = {key_name: event[key_name] for key_name in ("question_id", "target_type", "target_id", "value")}
            if event.get("reason"):
                label["reason"] = event["reason"]
            if "elapsed_ms" in event:
                label["elapsed_ms"] = event["elapsed_ms"]
            validate_atomic_label(label)
            active[key] = label
            if key in order:
                order.remove(key)
            order.append(key)
    return active, order


def _ask(item: ReviewItem, input_fn: Callable[[str], str]) -> tuple[str, str | None]:
    print("\n" + item.title)
    print(item.evidence)
    while True:
        value = input_fn("[y]예 [n]아니요 [u]모름 [b]뒤로 [q]저장 후 종료: ").strip().lower()
        if value in {"y", "n", "b", "q"}:
            return value.upper(), None
        if value == "u":
            reasons = list(sorted(U_REASONS))
            for index, reason in enumerate(reasons, 1):
                print(f"{index}. {reason}")
            selected = input_fn("모름 사유 번호: ").strip()
            if selected.isdigit() and 1 <= int(selected) <= len(reasons):
                return "U", reasons[int(selected) - 1]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--labels", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--approved-questions", type=Path)
    parser.add_argument("--approved-fixtures", type=Path)
    args = parser.parse_args(argv)
    fixtures = load_jsonl(args.fixtures)
    fixture_fingerprints = {row.get("run_fingerprint") for row in fixtures}
    if len(fixture_fingerprints) != 1 or None in fixture_fingerprints:
        raise SystemExit("fixtures must have one run fingerprint")
    reference_canaries = {row.get("reference_canary") for row in fixtures}
    if len(reference_canaries) != 1 or None in reference_canaries:
        raise SystemExit("fixtures must have one reference canary")
    queue = build_candidate_queue(fixtures)
    queue_contract = [
        {"question_id": item.question_id, "target_type": item.target_type, "target_id": item.target_id}
        for item in queue
    ]
    review_payload = {
        "schema_version": 1,
        "fixtures_sha256": file_sha256(args.fixtures),
        "fixture_run_fingerprint": next(iter(fixture_fingerprints)),
        "review_queue_sha256": object_sha256(queue_contract),
        "review_contract_version": 1,
        "reference_canary": next(iter(reference_canaries)),
    }
    review_fingerprint = object_sha256(review_payload)
    if not args.manifest.exists() and (args.events.exists() or args.labels.exists()):
        raise SystemExit("orphan review artifacts exist without a review manifest")
    if args.manifest.exists():
        prior = json.loads(args.manifest.read_text(encoding="utf-8"))
        if prior.get("review_fingerprint") != review_fingerprint:
            raise SystemExit("review resume fingerprint mismatch")
    else:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(
            json.dumps({**review_payload, "review_fingerprint": review_fingerprint}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    events = load_jsonl(args.events) if args.events.exists() else []
    if any(event.get("review_fingerprint") != review_fingerprint for event in events):
        raise SystemExit("review event fingerprint mismatch")
    active, order = reduce_events(events)
    args.events.parent.mkdir(parents=True, exist_ok=True)
    with args.events.open("a", encoding="utf-8", newline="\n") as output:
        index = 0
        while index < len(queue):
            item = queue[index]
            if item.key in active:
                index += 1
                continue
            started = time.perf_counter()
            value, reason = _ask(item, input)
            if value == "Q":
                break
            if value == "B":
                if order:
                    key = order.pop()
                    active.pop(key, None)
                    output.write(stable_json({"action": "undo", "question_id": key[0], "target_type": key[1], "target_id": key[2], "review_fingerprint": review_fingerprint}) + "\n")
                    output.flush()
                    index = next((position for position, queued in enumerate(queue) if queued.key == key), 0)
                continue
            label = {
                "question_id": item.question_id,
                "target_type": item.target_type,
                "target_id": item.target_id,
                "value": value,
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
            }
            if reason:
                label["reason"] = reason
            validate_atomic_label(label)
            event = {"action": "label", **label, "review_fingerprint": review_fingerprint}
            output.write(stable_json(event) + "\n")
            output.flush()
            active[item.key] = label
            order.append(item.key)
            index += 1
    write_jsonl(args.labels, (active[key] for key in sorted(active)))
    if args.approved_questions:
        reference_y = {
            target_id
            for (_, target_type, target_id), label in active.items()
            if target_type == "reference_supported" and label["value"] == "Y"
        }
        query_y = {
            target_id
            for (_, target_type, target_id), label in active.items()
            if target_type == "query_natural" and label["value"] == "Y"
        }
        cluster_questions: dict[str, set[str]] = {}
        for row in fixtures:
            cluster_questions.setdefault(row["fact_cluster_id"], set()).add(row["question_id"])
        approved_clusters = {
            cluster
            for cluster, question_ids in cluster_questions.items()
            if cluster in reference_y and len(question_ids) == 4 and question_ids <= query_y
        }
        approved = [
            {
                "question_id": row["question_id"],
                "fact_cluster_id": row["fact_cluster_id"],
                "query": row["query"],
                "query_kind": row["query_kind"],
                "fixture_run_fingerprint": row["run_fingerprint"],
            }
            for row in fixtures
            if row["fact_cluster_id"] in approved_clusters
        ]
        write_jsonl(args.approved_questions, approved)
        approved_sha256 = file_sha256(args.approved_questions)
        review_manifest = {
            **review_payload,
            "review_fingerprint": review_fingerprint,
            "approved_questions_sha256": approved_sha256,
            "approved_question_count": len(approved),
            "review_complete": len(active) == len(queue),
        }
        args.manifest.write_text(
            json.dumps(review_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"approved_questions={len(approved)} sha256={approved_sha256} path={args.approved_questions}")
        if args.approved_fixtures:
            approved_ids = {row["question_id"] for row in approved}
            write_jsonl(
                args.approved_fixtures,
                (
                    {
                        **row,
                        "reference_review": "Y",
                        "query_natural_review": "Y",
                        "parent_fixture_sha256": review_payload["fixtures_sha256"],
                    }
                    for row in fixtures
                    if row["question_id"] in approved_ids
                ),
            )
            print(f"approved_fixtures={len(approved_ids)} path={args.approved_fixtures}")
    print(f"reviewed={len(active)}/{len(queue)} labels={args.labels}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
