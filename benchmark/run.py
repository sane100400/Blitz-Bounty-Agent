#!/usr/bin/env python3
"""
Benchmark runner for bounty hunter skills.

Runs hunt skills against known vulnerabilities and measures:
- Precision (valid findings / total findings)
- Recall (found bugs / known bugs)
- Severity accuracy
- Cost efficiency ($ per valid finding)

Usage:
    python3 benchmark/run.py --suite known-vulns
    python3 benchmark/run.py --suite known-vulns --model claude-opus-4-6
    python3 benchmark/run.py --compare  # compare last two runs
"""

import argparse
import json
import shlex
import subprocess
import sys
import time
import yaml
from datetime import datetime
from pathlib import Path

BENCHMARK_DIR = Path(__file__).parent
CONFIG_PATH = BENCHMARK_DIR / "config.yaml"
RESULTS_DIR = BENCHMARK_DIR / "results"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def load_suite(suite_path: str) -> dict:
    with open(BENCHMARK_DIR / suite_path) as f:
        return yaml.safe_load(f)


def run_single_case(case: dict, model: str, mode: str = "immunefi") -> dict:
    """Run a single benchmark case and capture output."""
    start = time.time()

    case_mode = case.get("mode", mode)
    case_setup = case.get("setup", {})

    if case_mode == "immunefi":
        target = case.get("target", case["protocol"])
        platform = case.get("platform", "immunefi")
        rpc_url = case.get("rpc_url", case_setup.get("rpc_url"))
    else:
        target = case.get("target", case_setup.get("repo", case["protocol"]))
        platform = case.get("platform", "codearena")
        rpc_url = None

    prompt = f"/web3-hunt {shlex.quote(str(target))} {platform}"
    if rpc_url:
        prompt += f" {shlex.quote(str(rpc_url))}"

    cmd = [
        "claude", "-p",
        prompt,
        "--model", model,
        "--output-format", "text",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 min max per case
            cwd=str(BENCHMARK_DIR.parent),
        )
        output = result.stdout
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        output = ""
        exit_code = -1

    elapsed = time.time() - start

    # Score the output
    found_keywords = []
    for kw in case.get("detection_keywords", []):
        if kw.lower() in output.lower():
            found_keywords.append(kw)

    detected = len(found_keywords) >= max(1, len(case.get("detection_keywords", [])) // 2)

    return {
        "case_id": case["id"],
        "detected": detected,
        "keywords_found": found_keywords,
        "keywords_total": len(case.get("detection_keywords", [])),
        "elapsed_seconds": round(elapsed, 1),
        "exit_code": exit_code,
        "output_length": len(output),
    }


def compute_scores(results: list, config: dict) -> dict:
    """Compute aggregate scores from individual case results."""
    total = len(results)
    if total == 0:
        return {"precision": 0, "recall": 0, "severity_accuracy": 0, "composite": 0}

    detected = sum(1 for r in results if r["detected"])
    recall = detected / total if total > 0 else 0

    # Precision requires checking false positives — placeholder for now
    precision = recall  # TODO: incorporate false-positive suite

    scoring = config.get("scoring", {})
    composite = (
        scoring.get("precision_weight", 0.35) * precision
        + scoring.get("recall_weight", 0.30) * recall
        + scoring.get("severity_weight", 0.20) * 0  # TODO
        + scoring.get("cost_weight", 0.15) * 0  # TODO
    )

    return {
        "total_cases": total,
        "detected": detected,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "composite": round(composite, 3),
    }


def save_results(run_data: dict):
    """Save benchmark run to results directory."""
    RESULTS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"run_{timestamp}.json"
    with open(path, "w") as f:
        json.dump(run_data, f, indent=2)
    print(f"Results saved to {path}")
    return path


def compare_runs():
    """Compare the two most recent runs."""
    RESULTS_DIR.mkdir(exist_ok=True)
    runs = sorted(RESULTS_DIR.glob("run_*.json"))
    if len(runs) < 2:
        print("Need at least 2 runs to compare.")
        return

    with open(runs[-2]) as f:
        prev = json.load(f)
    with open(runs[-1]) as f:
        curr = json.load(f)

    print(f"\n{'Metric':<20} {'Previous':>10} {'Current':>10} {'Delta':>10}")
    print("-" * 52)
    for key in ["precision", "recall", "composite"]:
        p = prev.get("scores", {}).get(key, 0)
        c = curr.get("scores", {}).get(key, 0)
        delta = c - p
        sign = "+" if delta > 0 else ""
        print(f"{key:<20} {p:>10.3f} {c:>10.3f} {sign}{delta:>9.3f}")


def main():
    parser = argparse.ArgumentParser(description="Bounty Hunter Benchmark Runner")
    parser.add_argument("--suite", type=str, help="Test suite to run (e.g., known-vulns)")
    parser.add_argument("--model", type=str, default="claude-opus-4-6", help="Model ID")
    parser.add_argument("--compare", action="store_true", help="Compare last two runs")
    parser.add_argument("--dry-run", action="store_true", help="Validate config without running")
    args = parser.parse_args()

    if args.compare:
        compare_runs()
        return

    config = load_config()

    if args.dry_run:
        print(f"Config loaded: {len(config.get('suites', {}))} suites defined")
        print(f"Models: {[m['id'] for m in config.get('models', [])]}")
        return

    if not args.suite:
        print("Error: --suite is required (or use --compare)")
        sys.exit(1)

    suite_config = config["suites"].get(args.suite)
    if not suite_config:
        print(f"Error: suite '{args.suite}' not found in config.yaml")
        sys.exit(1)

    suite = load_suite(suite_config["source_dir"])
    cases = suite.get("cases", [])

    if not cases:
        print(f"Suite '{args.suite}' has no test cases yet. Add cases to suites/{args.suite}.yaml")
        sys.exit(0)

    print(f"Running benchmark: {args.suite} ({len(cases)} cases) with {args.model}")
    print("=" * 60)

    results = []
    for i, case in enumerate(cases, 1):
        print(f"\n[{i}/{len(cases)}] {case['id']}: {case.get('description', '')}")
        result = run_single_case(case, args.model)
        results.append(result)
        status = "DETECTED" if result["detected"] else "MISSED"
        print(f"  → {status} ({result['elapsed_seconds']}s)")

    scores = compute_scores(results, config)

    run_data = {
        "timestamp": datetime.now().isoformat(),
        "suite": args.suite,
        "model": args.model,
        "cases": results,
        "scores": scores,
    }

    print(f"\n{'='*60}")
    print(f"Results: {scores['detected']}/{scores['total_cases']} detected")
    print(f"Recall: {scores['recall']:.1%}")
    print(f"Composite: {scores['composite']:.3f}")

    save_results(run_data)


if __name__ == "__main__":
    main()
