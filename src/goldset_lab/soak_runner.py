"""Repeat immutable local QA runs for at least four hours and record telemetry."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from .contracts import load_jsonl
from .io_utils import file_sha256, object_sha256, stable_json
from .run_lock import acquire_model_lock, acquire_run_lock


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def _resource_sample() -> dict:
    sample = {"epoch": time.time(), "gpu_memory_mib": None, "ollama_working_set_bytes": None}
    try:
        gpu = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            text=True, capture_output=True, timeout=10, check=False,
        )
        if gpu.returncode == 0:
            sample["gpu_memory_mib"] = sum(float(item) for item in gpu.stdout.split())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    try:
        memory = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "(Get-Process ollama -ErrorAction SilentlyContinue | Measure-Object WorkingSet64 -Sum).Sum"],
            text=True, capture_output=True, timeout=10, check=False,
        )
        if memory.returncode == 0 and memory.stdout.strip():
            sample["ollama_working_set_bytes"] = int(memory.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return sample


def _signatures(rows: list[dict]) -> dict[str, tuple[str, str]]:
    return {
        row["question_id"]: (
            object_sha256([(hit.get("content_id"), hit.get("rank")) for hit in row.get("retrieval", [])]),
            object_sha256(row.get("local_answer", {})),
        )
        for row in rows
    }


def _gate_status(total: float, phase_counts: dict[str, int], allow_short_test: bool) -> dict:
    duration_ok = total >= 14400
    phase_ok = phase_counts.get("fixed_seed_reproducibility", 0) >= 2 and phase_counts.get("varied_seed_robustness", 0) >= 1
    passed = allow_short_test or (duration_ok and phase_ok)
    reasons = []
    if not duration_ok:
        reasons.append("duration_below_14400")
    if not phase_ok:
        reasons.append("phase_coverage_insufficient")
    return {"duration_gate_passed": duration_ok, "phase_gate_passed": phase_ok, "formal_gate_passed": passed, "completed": passed, "exit_reason": "completed" if passed else ",".join(reasons)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--questions", required=True, type=Path)
    parser.add_argument("--review-manifest", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--duration-seconds", type=int, default=14400)
    parser.add_argument("--allow-short-test", action="store_true")
    parser.add_argument("--model", default="qwen2.5:7b")
    parser.add_argument("--endpoint", default="http://127.0.0.1:11434")
    parser.add_argument("--seed", type=int, default=20260730)
    parser.add_argument("--mode", choices=("smoke", "diagnostic", "full"), default="smoke")
    parser.add_argument("--child-timeout-seconds", type=int, default=3600)
    parser.add_argument("--max-disk-mib", type=int, default=2048)
    parser.add_argument("--recover-stale-lock", action="store_true")
    args = parser.parse_args(argv)
    if args.duration_seconds < 14400 and not args.allow_short_test:
        raise SystemExit("soak duration must be at least 14400 seconds")
    if args.out_dir.exists() and any(args.out_dir.iterdir()):
        raise SystemExit("soak output directory must be empty or absent")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    soak_fingerprint = object_sha256({"questions_sha256": file_sha256(args.questions), "review_manifest_sha256": file_sha256(args.review_manifest), "duration_seconds": args.duration_seconds, "model": args.model, "endpoint": args.endpoint, "seed": args.seed, "mode": args.mode})
    acquire_run_lock(args.out_dir / "run.lock", soak_fingerprint, recover_stale=args.recover_stale_lock)
    acquire_model_lock(args.endpoint, soak_fingerprint, recover_stale=args.recover_stale_lock)
    events_path = args.out_dir / "events.jsonl"
    started_wall = time.time()
    started = time.monotonic()
    iteration = 0
    question_rows = load_jsonl(args.questions)
    question_id_list = [row["question_id"] for row in question_rows]
    if len(question_id_list) != len(set(question_id_list)):
        raise SystemExit("soak questions contain duplicate IDs")
    question_ids = set(question_id_list)
    latencies: list[float] = []
    errors = 0
    fixed_baseline: dict[str, tuple[str, str]] | None = None
    fixed_retrieval_matches = fixed_answer_matches = fixed_comparisons = 0
    varied_retrieval_matches = varied_answer_matches = varied_comparisons = 0
    varied_answer_signatures: dict[str, set[str]] = {question_id: set() for question_id in question_ids}
    phase_counts = {"fixed_seed_reproducibility": 0, "varied_seed_robustness": 0}
    all_resource_samples = []
    last_elapsed = 0.0
    while time.monotonic() - started < args.duration_seconds or iteration == 0:
        remaining_time = args.duration_seconds - (time.monotonic() - started)
        if iteration and remaining_time < last_elapsed and args.allow_short_test:
            break
        iteration += 1
        phase = "fixed_seed_reproducibility" if iteration % 2 else "varied_seed_robustness"
        iteration_dir = args.out_dir / f"iteration-{iteration:05d}"
        iteration_dir.mkdir()
        command = [
            sys.executable, "-m", "goldset_lab.local_runner",
            "--db", str(args.db), "--questions", str(args.questions),
            "--review-manifest", str(args.review_manifest),
            "--results", str(iteration_dir / "results.jsonl"),
            "--attempts", str(iteration_dir / "attempts.jsonl"),
            "--manifest", str(iteration_dir / "manifest.json"),
            "--model", args.model, "--endpoint", args.endpoint,
            "--seed", str(args.seed if phase == "fixed_seed_reproducibility" else args.seed + iteration), "--mode", args.mode,
            "--skip-shared-model-lock",
        ]
        run_started = time.monotonic()
        process = subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        samples = [_resource_sample()]
        timed_out = False
        while process.poll() is None:
            if time.monotonic() - run_started > args.child_timeout_seconds:
                process.kill()
                timed_out = True
                break
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass
            samples.append(_resource_sample())
        stdout, stderr = process.communicate()
        elapsed = time.monotonic() - run_started
        last_elapsed = elapsed
        latencies.append(elapsed)
        if process.returncode != 0:
            errors += 1
        event = {
            "iteration": iteration,
            "phase": phase,
            "elapsed_seconds": round(elapsed, 3),
            "returncode": process.returncode,
            "results_sha256": file_sha256(iteration_dir / "results.jsonl") if (iteration_dir / "results.jsonl").exists() else None,
            "stdout_tail": stdout[-1000:],
            "stderr_tail": stderr[-1000:],
            "resource_samples": samples,
            "timed_out": timed_out,
            "recorded_at_epoch": time.time(),
        }
        with events_path.open("a", encoding="utf-8", newline="\n") as output:
            output.write(stable_json(event) + "\n")
        print(f"soak iteration={iteration} elapsed={elapsed:.1f}s errors={errors}", flush=True)
        result_path = iteration_dir / "results.jsonl"
        result_rows = load_jsonl(result_path) if result_path.exists() else []
        result_ids = {row["question_id"] for row in result_rows}
        if process.returncode != 0 or result_ids != question_ids:
            raise SystemExit("soak stopped on failed immutable iteration")
        phase_counts[phase] += 1
        all_resource_samples.extend(samples)
        signatures = _signatures(result_rows)
        if phase == "fixed_seed_reproducibility":
            if fixed_baseline is None:
                fixed_baseline = signatures
            else:
                for question_id in question_ids:
                    fixed_comparisons += 1
                    fixed_retrieval_matches += int(signatures[question_id][0] == fixed_baseline[question_id][0])
                    fixed_answer_matches += int(signatures[question_id][1] == fixed_baseline[question_id][1])
        elif fixed_baseline is not None:
            for question_id in question_ids:
                varied_comparisons += 1
                varied_retrieval_matches += int(signatures[question_id][0] == fixed_baseline[question_id][0])
                varied_answer_matches += int(signatures[question_id][1] == fixed_baseline[question_id][1])
                varied_answer_signatures[question_id].add(signatures[question_id][1])
        disk_bytes = sum(path.stat().st_size for path in args.out_dir.rglob("*") if path.is_file())
        if disk_bytes > args.max_disk_mib * 1024 * 1024:
            raise SystemExit("soak stopped after exceeding disk limit")
    total = time.monotonic() - started
    gates = _gate_status(total, phase_counts, args.allow_short_test)
    summary = {
        "schema_version": 1,
        "started_at_epoch": started_wall,
        "duration_seconds": round(total, 3),
        "required_duration_seconds": 14400,
        "short_test": total < 14400,
        "iterations": iteration,
        "errors": errors,
        "iteration_seconds_p50": round(_percentile(latencies, 0.50), 3),
        "iteration_seconds_p95": round(_percentile(latencies, 0.95), 3),
        "iteration_seconds_max": round(max(latencies), 3),
        "questions_sha256": file_sha256(args.questions),
        "review_manifest_sha256": file_sha256(args.review_manifest),
        "events_sha256": file_sha256(events_path),
        "network_observation": "not_observed_by_runner; separate OS evidence required",
        "company_pc_claim": False,
        "phase_counts": phase_counts,
        "fixed_retrieval_match_rate": fixed_retrieval_matches / fixed_comparisons if fixed_comparisons else None,
        "fixed_answer_match_rate": fixed_answer_matches / fixed_comparisons if fixed_comparisons else None,
        "fixed_comparisons": fixed_comparisons,
        "varied_retrieval_match_to_fixed_rate": varied_retrieval_matches / varied_comparisons if varied_comparisons else None,
        "varied_answer_match_to_fixed_rate": varied_answer_matches / varied_comparisons if varied_comparisons else None,
        "varied_comparisons": varied_comparisons,
        "varied_answer_unique_signature_count": sum(len(items) for items in varied_answer_signatures.values()),
        "gpu_memory_mib_peak": max((item["gpu_memory_mib"] for item in all_resource_samples if item["gpu_memory_mib"] is not None), default=None),
        "ollama_working_set_bytes_peak": max((item["ollama_working_set_bytes"] for item in all_resource_samples if item["ollama_working_set_bytes"] is not None), default=None),
        **gates,
    }
    (args.out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if not gates["formal_gate_passed"]:
        raise SystemExit(gates["exit_reason"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
