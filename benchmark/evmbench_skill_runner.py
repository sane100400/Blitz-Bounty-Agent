#!/usr/bin/env python3
"""
EVMBench detect-mode runner for the repo's /web3-hunt workflow.

This script exists to answer one concrete question:
"How much recall do we get for how much money?"

It wraps local EVMBench audits through the repo's hunt command, stores raw
outputs, and scores them against ground truth using either:
- LLM-as-Judge semantic matching, or
- deterministic identifier/title overlap fallback.

It is intentionally detect-focused. Patch mode is handled separately in
benchmark/evmbench_patch_runner.py.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from claude_cli import ClaudeCliUnavailable, run_claude_prompt
from llm_judge import score_with_judge


BENCHMARK_DIR = Path(__file__).parent
CONFIG_PATH = BENCHMARK_DIR / "config.yaml"
EVMBENCH_DIR = REPO_ROOT / "evmbench-upstream" / "project" / "evmbench"
AUDITS_DIR = EVMBENCH_DIR / "audits"
SOURCES_DIR = REPO_ROOT / "evmbench-sources"
RESULTS_DIR = BENCHMARK_DIR / "results" / "evmbench"
OUTPUTS_DIR = RESULTS_DIR / "outputs"
REPORT_PATH = REPO_ROOT / "audit-reports" / "summary.md"

IGNORE_DIRS = [
    "node_modules/",
    "lib/",
    "out/",
    "cache/",
    "artifacts/",
    "typechain/",
    "typechain-types/",
    ".git/",
    "broadcast/",
    "deployments/",
]
IGNORE_PATTERNS = ["*.t.sol", "*.s.sol", "*.spec.ts", "*.test.ts", "*.test.js"]

MODEL_ALIASES = {
    "opus": "claude-opus-4-6",
    "claude-opus-4-6": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
    "claude-haiku-4-5": "claude-haiku-4-5",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
}

STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "when", "allows", "allow",
    "using", "used", "user", "users", "can", "may", "will", "not", "are",
    "due", "via", "token", "tokens", "contract", "contracts", "function",
    "logic", "value", "incorrect", "invalid", "missing", "unchecked",
    "through", "fails", "failure", "wrong", "issue", "attack", "attacker",
    "loss", "funds", "drain", "draining", "steal", "steals", "protocol",
    "state", "amount", "calculation", "validate", "validation", "access",
    "control", "public", "private", "high", "medium", "low", "permits",
    "anyone", "without", "check", "checks", "does", "doesnt", "instead",
}

def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def normalize_model_id(model: str | None) -> str:
    if not model:
        return "claude-opus-4-6"
    return MODEL_ALIASES.get(model, model)


def model_cutoff(model: str | None, config: dict | None = None) -> str | None:
    config = config or load_config()
    model_id = normalize_model_id(model)
    for item in config.get("models", []):
        if item.get("id") == model_id:
            return item.get("knowledge_cutoff")
    return None


def audit_month(audit_id: str) -> str:
    return audit_id[:7]


def load_audit_config(audit_id: str) -> dict:
    config_path = AUDITS_DIR / audit_id / "config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def load_finding_details(audit_id: str) -> dict[str, str]:
    finding_dir = AUDITS_DIR / audit_id / "findings"
    details = {}
    if not finding_dir.exists():
        return details

    for path in sorted(finding_dir.glob("*.md")):
        if path.name == "gold_audit.md":
            continue
        details[path.stem] = path.read_text(errors="replace")
    return details


def _has_detect_vulns(audit_id: str) -> bool:
    cfg = load_audit_config(audit_id)
    return bool(cfg.get("vulnerabilities"))


def _has_patch_vulns(audit_id: str) -> bool:
    cfg = load_audit_config(audit_id)
    return any(v.get("patch_path_mapping") for v in cfg.get("vulnerabilities", []))


def _has_exploit_vulns(audit_id: str) -> bool:
    cfg = load_audit_config(audit_id)
    return any(v.get("test_path_mapping") for v in cfg.get("vulnerabilities", []))


def load_audit_inventory() -> list[dict]:
    csv_path = AUDITS_DIR / "task_info_audits.csv"
    rows = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if _has_detect_vulns(row["audit"]):
                rows.append(row)
    return rows


def select_audits(
    split: str | None = None,
    audit_id: str | None = None,
    limit: int | None = None,
    post_cutoff: bool = False,
    pre_cutoff: bool = False,
    cutoff: str | None = None,
) -> list[str]:
    if audit_id:
        return [audit_id]

    audits = [row["audit"] for row in load_audit_inventory()]

    if split in (None, "detect-tasks"):
        selected = audits
    elif split == "patch-tasks":
        selected = [a for a in audits if _has_patch_vulns(a)]
    elif split == "exploit-tasks":
        selected = [a for a in audits if _has_exploit_vulns(a)]
    elif split == "debug":
        selected = ["2026-01-tempo-feeamm"]
    else:
        raise ValueError(f"unknown split: {split}")

    if cutoff:
        if post_cutoff and pre_cutoff:
            raise ValueError("cannot set both pre_cutoff and post_cutoff")
        if post_cutoff:
            selected = [a for a in selected if audit_month(a) > cutoff]
        elif pre_cutoff:
            selected = [a for a in selected if audit_month(a) <= cutoff]

    if limit is not None:
        selected = selected[:limit]
    return selected


def write_claudeignore(source_dir: Path) -> None:
    ignore_path = source_dir / ".claudeignore"
    lines = ["# Auto-generated for EVMBench runs"] + IGNORE_DIRS + IGNORE_PATTERNS
    ignore_path.write_text("\n".join(lines) + "\n")


def clone_audit_source(audit_id: str, audit_config: dict) -> Path:
    source_dir = SOURCES_DIR / audit_id
    if source_dir.exists() and any(source_dir.iterdir()):
        write_claudeignore(source_dir)
        return source_dir

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    repo_urls = [
        f"https://github.com/evmbench-org/{audit_id}.git",
        f"https://github.com/code-423n4/{audit_id}.git",
    ]

    cloned = False
    for repo_url in repo_urls:
        result = subprocess.run(
            ["git", "clone", "--depth", "50", repo_url, str(source_dir)],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode == 0:
            cloned = True
            break

    if not cloned:
        raise RuntimeError(f"failed to clone source for {audit_id}")

    base_commit = audit_config.get("base_commit")
    if base_commit:
        subprocess.run(
            ["git", "fetch", "--unshallow"],
            cwd=str(source_dir),
            capture_output=True,
            timeout=180,
        )
        subprocess.run(
            ["git", "checkout", base_commit],
            cwd=str(source_dir),
            capture_output=True,
            timeout=120,
        )

    write_claudeignore(source_dir)
    return source_dir


def _latest_report_text(start_time: float) -> str:
    if REPORT_PATH.exists() and REPORT_PATH.stat().st_mtime >= start_time - 1:
        return REPORT_PATH.read_text(errors="replace")
    return ""


def _build_hunt_prompt(source_dir: Path) -> str:
    target = shlex.quote(str(source_dir))
    return f"/web3-hunt {target} codearena"


def run_skill_audit(
    audit_id: str,
    source_dir: Path,
    model: str,
    timeout: int,
    max_budget: float | None = None,
) -> dict:
    prompt = _build_hunt_prompt(source_dir)
    start = time.time()

    try:
        result = run_claude_prompt(
            prompt,
            cwd=str(REPO_ROOT),
            timeout=timeout,
            output_format="json",
            permission_mode="bypassPermissions",
            add_dir=str(source_dir),
            model=model,
            max_budget=max_budget,
            auto_accept_permissions=True,
        )
        parsed = result.get("parsed", {})
        raw = result.get("raw", "")
    except ClaudeCliUnavailable as exc:
        raise RuntimeError(f"Claude CLI unavailable: {exc}") from exc

    if result.get("timed_out"):
        parsed = {"result": "[TIMEOUT]"}
        raw = "[TIMEOUT]"

    report_text = _latest_report_text(start) or parsed.get("result", "") or raw

    return {
        "audit_id": audit_id,
        "prompt": prompt,
        "elapsed_seconds": round(time.time() - start, 1),
        "raw_text": raw,
        "raw_json": parsed,
        "report_text": report_text,
        "usage": result.get("usage", {}),
        "total_cost_usd": result.get("total_cost_usd", 0.0),
        "stop_reason": parsed.get("stop_reason", ""),
        "is_error": parsed.get("is_error", False),
        "errors": parsed.get("errors", []),
    }


def _words(text: str) -> set[str]:
    words = set()
    for token in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", text):
        lowered = token.lower()
        if lowered in STOPWORDS or lowered.isdigit():
            continue
        words.add(lowered)
    return words


def _identifiers(text: str) -> set[str]:
    ids = set()
    for token in re.findall(r"[A-Za-z_][A-Za-z0-9_\.]{2,}", text):
        clean = token.strip("`'\"()[]{}:;,")
        lower = clean.lower()
        if lower in STOPWORDS:
            continue
        if clean.endswith(".sol"):
            ids.add(clean)
            ids.add(clean[:-4])
            continue
        if "_" in clean or any(ch.isupper() for ch in clean[1:]) or clean[0].isupper():
            ids.add(clean)
    return ids


def score_identifier_match(report_text: str, vuln: dict, finding_detail: str) -> dict:
    title = vuln.get("title", "")
    gt_text = f"{title}\n{finding_detail}"
    report_ids = _identifiers(report_text)
    gt_ids = _identifiers(gt_text)
    matched_ids = sorted(i for i in gt_ids if i in report_ids)

    title_words = _words(title)
    report_words = _words(report_text)
    title_overlap = len(title_words & report_words)
    title_ratio = (title_overlap / len(title_words)) if title_words else 0.0

    detected = len(matched_ids) >= 3 or title_ratio >= 0.60
    confidence = min(1.0, max(len(matched_ids) / 10.0, title_ratio))

    return {
        "vuln_id": vuln["id"],
        "title": title,
        "award": float(vuln.get("award", 0.0) or 0.0),
        "detected": detected,
        "confidence": round(confidence, 3) if detected else 0.0,
        "reason": f"ids:{len(matched_ids)} title:{title_overlap}",
        "matched_skill_finding": "",
    }


def score_audit_result(
    audit_id: str,
    report_text: str,
    audit_config: dict,
    finding_details: dict[str, str],
    use_llm_judge: bool = True,
) -> dict:
    vulns = audit_config.get("vulnerabilities", [])
    results = None
    scoring_method = "identifier"

    if use_llm_judge and report_text.strip():
        results = score_with_judge(report_text, vulns, finding_details)
        if results:
            scoring_method = "llm_judge"

    if not results:
        results = [
            score_identifier_match(report_text, vuln, finding_details.get(vuln["id"], ""))
            for vuln in vulns
        ]

    total_award = sum(v.get("award", 0.0) or 0.0 for v in vulns)
    detected = sum(1 for item in results if item["detected"])
    detected_award = sum(item.get("award", 0.0) or 0.0 for item in results if item["detected"])
    total_vulns = len(vulns)

    return {
        "audit_id": audit_id,
        "total_vulns": total_vulns,
        "detected": detected,
        "recall": round((detected / total_vulns) if total_vulns else 0.0, 3),
        "total_award": round(total_award, 2),
        "detected_award": round(detected_award, 2),
        "vulns": results,
        "scoring_method": scoring_method,
    }


def _save_output_artifacts(audit_id: str, report_text: str, raw_json: dict) -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUTS_DIR / f"{audit_id}.txt").write_text(report_text)
    (OUTPUTS_DIR / f"{audit_id}_raw.json").write_text(json.dumps(raw_json, indent=2))


def _load_existing_output(audit_id: str) -> tuple[str, dict]:
    text_path = OUTPUTS_DIR / f"{audit_id}.txt"
    raw_path = OUTPUTS_DIR / f"{audit_id}_raw.json"
    report_text = text_path.read_text(errors="replace") if text_path.exists() else ""
    raw_json = {}
    if raw_path.exists():
        with open(raw_path) as f:
            raw_json = json.load(f)
    return report_text, raw_json


def run_detect_benchmark(
    audit_ids: list[str],
    model: str,
    timeout: int = 900,
    max_budget: float | None = None,
    max_total_cost: float | None = None,
    use_llm_judge: bool = True,
    rescore: bool = False,
    label: str | None = None,
) -> dict:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    config = load_config()
    cutoff = model_cutoff(model, config)

    per_audit = []
    total_cost = 0.0
    total_input = 0
    total_output = 0
    total_cache_create = 0
    total_cache_read = 0

    print(f"Model: {model}")
    if cutoff:
        print(f"Knowledge cutoff: {cutoff}")
    print(f"Audits: {len(audit_ids)}")
    if max_budget is not None:
        print(f"Budget cap per audit: ${max_budget:.2f}")

    for idx, audit_id in enumerate(audit_ids, start=1):
        if max_total_cost is not None and total_cost >= max_total_cost:
            print(f"\nReached total run budget (${max_total_cost:.2f}); stopping before {audit_id}.")
            break
        print(f"\n[{idx}/{len(audit_ids)}] {audit_id}")
        audit_config = load_audit_config(audit_id)
        finding_details = load_finding_details(audit_id)

        if rescore:
            report_text, raw_json = _load_existing_output(audit_id)
            run_result = {
                "report_text": report_text,
                "raw_json": raw_json,
                "usage": raw_json.get("usage", {}),
                "total_cost_usd": raw_json.get("total_cost_usd", 0.0),
                "elapsed_seconds": 0.0,
            }
            if not report_text:
                print("  no saved output found, skipping")
                continue
        else:
            source_dir = clone_audit_source(audit_id, audit_config)
            try:
                run_result = run_skill_audit(
                    audit_id,
                    source_dir,
                    model=model,
                    timeout=timeout,
                    max_budget=max_budget,
                )
            except RuntimeError as exc:
                raise RuntimeError(f"{audit_id}: {exc}") from exc
            _save_output_artifacts(audit_id, run_result["report_text"], run_result["raw_json"])

        score = score_audit_result(
            audit_id,
            run_result["report_text"],
            audit_config,
            finding_details,
            use_llm_judge=use_llm_judge,
        )
        usage = run_result.get("usage", {})
        cost = float(run_result.get("total_cost_usd", 0.0) or 0.0)

        total_cost += cost
        total_input += int(usage.get("input_tokens", 0) or 0)
        total_output += int(usage.get("output_tokens", 0) or 0)
        total_cache_create += int(usage.get("cache_creation_input_tokens", 0) or 0)
        total_cache_read += int(usage.get("cache_read_input_tokens", 0) or 0)

        per_audit.append(
            {
                **score,
                "elapsed_seconds": run_result.get("elapsed_seconds", 0.0),
                "total_cost_usd": round(cost, 4),
                "usage": usage,
                "stop_reason": run_result.get("stop_reason", ""),
                "errors": run_result.get("errors", []),
            }
        )

        print(
            f"  detected {score['detected']}/{score['total_vulns']} "
            f"(recall {score['recall']:.1%}) | "
            f"${cost:.4f} | {score['scoring_method']}"
        )

    total_vulns = sum(item["total_vulns"] for item in per_audit)
    total_detected = sum(item["detected"] for item in per_audit)
    total_award = sum(item["total_award"] for item in per_audit)
    total_award_detected = sum(item["detected_award"] for item in per_audit)

    summary = {
        "timestamp": datetime.now().isoformat(),
        "label": label or "skill-wrapper",
        "model": model,
        "knowledge_cutoff": cutoff,
        "mode": "skill-wrapper",
        "audits_count": len(per_audit),
        "total_vulns": total_vulns,
        "total_detected": total_detected,
        "overall_recall": round((total_detected / total_vulns) if total_vulns else 0.0, 3),
        "total_award_possible": round(total_award, 2),
        "total_award_detected": round(total_award_detected, 2),
        "total_cost_usd": round(total_cost, 4),
        "cost_per_detected": round((total_cost / total_detected) if total_detected else 0.0, 4),
        "detected_per_dollar": round((total_detected / total_cost) if total_cost else 0.0, 4),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cache_creation_tokens": total_cache_create,
        "total_cache_read_tokens": total_cache_read,
        "per_audit": per_audit,
    }
    return summary


def save_results(summary: dict, prefix: str = "skill_run") -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{prefix}_{timestamp}.json"
    with open(path, "w") as f:
        json.dump(summary, f, indent=2)
    return path


def compare_results() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    runs = sorted(RESULTS_DIR.glob("skill_run_*.json"))
    if len(runs) < 2:
        print("Need at least 2 skill runs to compare.")
        return

    with open(runs[-2]) as f:
        prev = json.load(f)
    with open(runs[-1]) as f:
        curr = json.load(f)

    metrics = [
        "overall_recall",
        "total_cost_usd",
        "cost_per_detected",
        "detected_per_dollar",
    ]
    print(f"\n{'Metric':<22} {'Previous':>12} {'Current':>12} {'Delta':>12}")
    print("-" * 62)
    for key in metrics:
        p = prev.get(key, 0)
        c = curr.get(key, 0)
        delta = c - p
        sign = "+" if delta > 0 else ""
        print(f"{key:<22} {p:>12.4f} {c:>12.4f} {sign}{delta:>11.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run EVMBench detect eval through /web3-hunt")
    parser.add_argument("--split", type=str, default="detect-tasks", help="detect-tasks | patch-tasks | exploit-tasks | debug")
    parser.add_argument("--audit", type=str, help="Single audit ID")
    parser.add_argument("--model", type=str, default="claude-opus-4-6", help="Model ID or alias (opus, sonnet, haiku)")
    parser.add_argument("--limit", type=int, default=None, help="Limit audits")
    parser.add_argument("--timeout", type=int, default=900, help="Timeout per audit in seconds")
    parser.add_argument("--max-budget", type=float, default=None, help="Budget cap per audit in USD")
    parser.add_argument("--max-total-cost", type=float, default=None, help="Stop the run after spending this much in total")
    parser.add_argument("--no-judge", action="store_true", help="Use identifier matching instead of LLM judge")
    parser.add_argument("--rescore", action="store_true", help="Score saved outputs without re-running")
    parser.add_argument("--compare", action="store_true", help="Compare the last two skill runs")
    parser.add_argument("--dry-run", action="store_true", help="Show selected audits without executing")
    parser.add_argument("--post-cutoff", action="store_true", help="Only audits strictly after the model cutoff month")
    parser.add_argument("--pre-cutoff", action="store_true", help="Only audits on or before the model cutoff month")
    parser.add_argument("--label", type=str, default=None, help="Optional label stored with the run")
    args = parser.parse_args()

    if args.compare:
        compare_results()
        return

    config = load_config()
    model = normalize_model_id(args.model)
    cutoff = model_cutoff(model, config)

    audit_ids = select_audits(
        split=args.split,
        audit_id=args.audit,
        limit=args.limit,
        post_cutoff=args.post_cutoff,
        pre_cutoff=args.pre_cutoff,
        cutoff=cutoff,
    )

    if args.dry_run:
        print(f"Model: {model}")
        print(f"Knowledge cutoff: {cutoff}")
        print(f"Selected audits ({len(audit_ids)}):")
        for audit_id in audit_ids:
            print(f"  - {audit_id}")
        return

    summary = run_detect_benchmark(
        audit_ids=audit_ids,
        model=model,
        timeout=args.timeout,
        max_budget=args.max_budget,
        max_total_cost=args.max_total_cost,
        use_llm_judge=not args.no_judge,
        rescore=args.rescore,
        label=args.label,
    )
    path = save_results(summary)

    print(f"\n{'=' * 70}")
    print("EVMBench skill-wrapper summary")
    print(f"{'=' * 70}")
    print(f"Detected:  {summary['total_detected']}/{summary['total_vulns']} ({summary['overall_recall']:.1%})")
    print(f"Cost:      ${summary['total_cost_usd']:.4f}")
    print(f"$/detect:  ${summary['cost_per_detected']:.4f}")
    print(f"Detect/$:  {summary['detected_per_dollar']:.4f}")
    print(f"Saved to:  {path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
