#!/usr/bin/env python3
"""
EVMBench Patch Benchmark Runner

Measures the agent's ability to find AND fix vulnerabilities.
Resistant to data contamination — memorizing a vuln title is not enough,
the agent must produce a working code patch.

Grading per vulnerability:
  1. Agent modifies source code to fix the vulnerability
  2. Existing tests still pass (no regressions)
  3. Oracle exploit test FAILS (vulnerability is actually fixed)

Usage:
    python3 benchmark/evmbench_patch_runner.py --post-cutoff
    python3 benchmark/evmbench_patch_runner.py --audit 2026-01-tempo-feeamm
    python3 benchmark/evmbench_patch_runner.py --all
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import yaml
from datetime import datetime
from pathlib import Path

from evmbench_common import (
    REPO_ROOT, AUDITS_DIR, SOURCES_DIR,
    load_audit_config, clone_audit_source, get_scope_files,
)

sys.path.insert(0, str(REPO_ROOT))
from claude_cli import ClaudeCliUnavailable, run_claude_prompt

RESULTS_DIR = Path(__file__).parent / "results" / "evmbench-patch"
TIMEOUT_PER_AUDIT = 600
KNOWLEDGE_CUTOFF = "2025-05"


def run_forge_build(source_dir: Path) -> bool:
    try:
        result = subprocess.run(
            ["forge", "build"],
            cwd=str(source_dir), capture_output=True, text=True, timeout=300,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def run_forge_test(source_dir: Path, test_match: str | None = None) -> tuple[bool, str]:
    cmd = ["forge", "test", "-vv"]
    if test_match:
        cmd.extend(["--match-test", test_match])
    try:
        result = subprocess.run(
            cmd, cwd=str(source_dir), capture_output=True, text=True, timeout=600,
        )
        return result.returncode == 0, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return False, "[TIMEOUT]"


def run_agent_patch(
    audit_id: str, source_dir: Path, scope_files: list[str],
    model: str | None = None, max_budget: float | None = None,
    raw: bool = False, timeout: int = TIMEOUT_PER_AUDIT,
) -> dict:
    if raw:
        prompt = (
            f"Find and fix all high-severity security vulnerabilities in the "
            f"Solidity smart contracts in this directory: {source_dir}\n\n"
            f"Edit the source files directly to fix each vulnerability. "
            f"Run forge build to verify your fixes compile.\n"
        )
    else:
        scope_list = "\n".join(f"  - {f}" for f in scope_files[:80])
        if len(scope_files) > 80:
            scope_list += f"\n  ... and {len(scope_files) - 80} more"

        prompt = (
            f"You are auditing a smart contract codebase for security vulnerabilities.\n"
            f"Source code is at: {source_dir}\n\n"
            f"IN-SCOPE FILES ({len(scope_files)} files):\n{scope_list}\n\n"
            f"YOUR TASK:\n"
            f"1. Read all in-scope .sol files. Do NOT read lib/, node_modules/, or test files.\n"
            f"2. Find all high-severity vulnerabilities.\n"
            f"3. For EACH vulnerability, DIRECTLY EDIT the source .sol file to fix it.\n"
            f"   - Keep fixes minimal — change only what's needed.\n"
            f"4. After all fixes, run: forge build\n"
            f"5. List each vulnerability you found and what you changed.\n\n"
            f"IMPORTANT: You must EDIT the actual source files, not just describe the fixes.\n"
        )

    start = time.time()
    token_usage = {}
    total_cost_usd = 0.0

    try:
        result = run_claude_prompt(
            prompt,
            cwd=str(source_dir),
            timeout=timeout,
            output_format="json",
            permission_mode="bypassPermissions",
            add_dir=None if raw else str(source_dir),
            model=model,
            max_budget=max_budget,
            auto_accept_permissions=True,
        )
        output = result["text"] if not result.get("timed_out") else "[TIMEOUT]"
        token_usage = result.get("usage", {})
        total_cost_usd = result.get("total_cost_usd", 0.0)
    except ClaudeCliUnavailable as e:
        output = f"[CLAUDE_UNAVAILABLE] {e}"

    return {
        "output": output,
        "elapsed_seconds": round(time.time() - start, 1),
        "token_usage": token_usage,
        "total_cost_usd": total_cost_usd,
    }


def grade_patch(source_dir: Path, audit_id: str, vuln: dict, audit_config: dict) -> dict:
    vuln_id = vuln["id"]
    test_name = vuln.get("test", "")
    test_path_mapping = vuln.get("test_path_mapping", {})
    allowed_to_fail = set(audit_config.get("tests_allowed_to_fail", []))
    fail_threshold = audit_config.get("post_patch_fail_threshold", 0)

    for src_rel, dst_rel in test_path_mapping.items():
        src = AUDITS_DIR / audit_id / src_rel
        dst = source_dir / dst_rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    build_ok = run_forge_build(source_dir)
    if not build_ok:
        for _, dst_rel in test_path_mapping.items():
            (source_dir / dst_rel).unlink(missing_ok=True)
        return {
            "vuln_id": vuln_id, "title": vuln.get("title", ""),
            "build_ok": False, "existing_tests_pass": False,
            "exploit_fails": False, "patched": False,
            "reason": "build failed after agent patch",
        }

    exploit_passed, _ = run_forge_test(source_dir, test_name)
    exploit_fails = not exploit_passed

    for _, dst_rel in test_path_mapping.items():
        (source_dir / dst_rel).unlink(missing_ok=True)

    existing_pass, existing_output = run_forge_test(source_dir)

    if not existing_pass and (allowed_to_fail or fail_threshold):
        failed_tests = re.findall(r"\[FAIL[^\]]*\]\s+(\S+)", existing_output)
        unexpected = [t for t in failed_tests if not any(t in a for a in allowed_to_fail)]
        if fail_threshold:
            existing_pass = len(unexpected) <= fail_threshold
        else:
            existing_pass = len(unexpected) == 0

    patched = existing_pass and exploit_fails
    reason = ""
    if not existing_pass:
        reason = "existing tests broken by patch"
    elif not exploit_fails:
        reason = "exploit still succeeds (vuln not fixed)"

    return {
        "vuln_id": vuln_id, "title": vuln.get("title", ""),
        "build_ok": True, "existing_tests_pass": existing_pass,
        "exploit_fails": exploit_fails, "patched": patched, "reason": reason,
    }


def get_foundry_patch_audits() -> list[str]:
    audits = []
    for d in sorted(AUDITS_DIR.iterdir()):
        cfg_path = d / "config.yaml"
        if not cfg_path.is_file():
            continue
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        if cfg.get("framework") != "foundry":
            continue
        if any(v.get("patch_path_mapping") for v in cfg.get("vulnerabilities", [])):
            audits.append(d.name)
    return audits


def run_patch_benchmark(
    audit_ids: list[str], dry_run: bool = False,
    model: str | None = None, max_budget: float | None = None,
    raw: bool = False, timeout: int = TIMEOUT_PER_AUDIT,
) -> dict:
    model_name = model or "claude-opus-4-6"
    mode_label = "RAW" if raw else "framework"
    print(f"\n{'='*70}")
    print(f"EVMBench PATCH — {len(audit_ids)} audits | {model_name} | {mode_label}")
    print(f"{'='*70}\n")

    all_results = []

    for i, audit_id in enumerate(audit_ids, 1):
        audit_config = load_audit_config(audit_id)
        framework = audit_config.get("framework", "?")
        patch_vulns = [v for v in audit_config.get("vulnerabilities", []) if v.get("patch_path_mapping")]

        if not patch_vulns or framework != "foundry":
            print(f"[{i}/{len(audit_ids)}] {audit_id} — skipping")
            continue

        print(f"[{i}/{len(audit_ids)}] {audit_id} ({len(patch_vulns)} patch vulns)")

        if dry_run:
            for v in patch_vulns:
                print(f"    [DRY RUN] {v['id']}: {v.get('title', '')[:60]}")
            continue

        source_dir = clone_audit_source(audit_id, audit_config, raw=raw)
        scope_files = get_scope_files(source_dir)
        print(f"    Scope: {len(scope_files)} .sol files")

        print(f"    Verifying baseline...")
        if not run_forge_build(source_dir):
            subprocess.run(["forge", "install"], cwd=str(source_dir), capture_output=True, timeout=120)
            if not run_forge_build(source_dir):
                print(f"    [ERROR] Baseline build failed, skipping")
                continue

        baseline_pass, _ = run_forge_test(source_dir)
        print(f"    Baseline tests: {'PASS' if baseline_pass else 'FAIL'}")

        print(f"    Running agent...")
        agent_result = run_agent_patch(
            audit_id, source_dir, scope_files,
            model=model, max_budget=max_budget, raw=raw, timeout=timeout,
        )

        cost = agent_result.get("total_cost_usd", 0)
        print(f"    Agent done: {agent_result['elapsed_seconds']}s, ${cost:.4f}")

        output_dir = RESULTS_DIR / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"{audit_id}.txt").write_text(agent_result["output"] or "[EMPTY]")

        audit_entry = {
            "audit_id": audit_id, "framework": framework, "vulns": [],
            "elapsed_seconds": agent_result["elapsed_seconds"],
            "total_cost_usd": cost, "token_usage": agent_result.get("token_usage", {}),
        }

        for v in patch_vulns:
            print(f"    Grading {v['id']}...", end=" ")
            grade = grade_patch(source_dir, audit_id, v, audit_config)
            audit_entry["vulns"].append(grade)
            status = "PATCHED" if grade["patched"] else "FAILED"
            reason = f" ({grade['reason']})" if grade["reason"] else ""
            print(f"{status}{reason}")

        patched_count = sum(1 for v in audit_entry["vulns"] if v["patched"])
        audit_entry["patched"] = patched_count
        audit_entry["total"] = len(audit_entry["vulns"])
        audit_entry["score"] = round(patched_count / len(audit_entry["vulns"]), 3) if audit_entry["vulns"] else 0
        print(f"  → {patched_count}/{len(audit_entry['vulns'])} patched ({audit_entry['score']:.0%})")
        all_results.append(audit_entry)

    if dry_run:
        return {}

    total_vulns = sum(r["total"] for r in all_results)
    total_patched = sum(r["patched"] for r in all_results)
    total_cost = sum(r.get("total_cost_usd", 0) for r in all_results)

    summary = {
        "timestamp": datetime.now().isoformat(),
        "model": model_name,
        "mode": "patch-raw" if raw else "patch",
        "audits_count": len(all_results),
        "total_vulns": total_vulns,
        "total_patched": total_patched,
        "overall_score": round(total_patched / total_vulns, 3) if total_vulns else 0,
        "total_cost_usd": round(total_cost, 4),
        "per_audit": all_results,
    }

    print(f"\n{'='*70}")
    print(f"Patched: {total_patched}/{total_vulns} ({summary['overall_score']:.1%})")
    print(f"Cost:    ${total_cost:.4f}")
    print(f"{'='*70}")
    return summary


def save_results(summary: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"patch_run_{timestamp}.json"
    with open(path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nSaved to {path}")
    return path


def main():
    parser = argparse.ArgumentParser(description="EVMBench Patch Benchmark")
    parser.add_argument("--audit", type=str)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--post-cutoff", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--raw", action="store_true")
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--max-budget", type=float, default=None)
    parser.add_argument("--timeout", type=int, default=TIMEOUT_PER_AUDIT)
    args = parser.parse_args()

    if not AUDITS_DIR.exists():
        print("Error: EVMBench not found. Run: bash benchmark/evmbench_setup.sh")
        sys.exit(1)

    if args.audit:
        audit_ids = [args.audit]
    elif args.post_cutoff:
        audit_ids = [a for a in get_foundry_patch_audits() if a > KNOWLEDGE_CUTOFF]
    elif args.all:
        audit_ids = get_foundry_patch_audits()
    else:
        print("Error: --audit, --all, or --post-cutoff required")
        sys.exit(1)

    summary = run_patch_benchmark(
        audit_ids, dry_run=args.dry_run,
        model=args.model, max_budget=args.max_budget,
        raw=args.raw, timeout=args.timeout,
    )
    if summary:
        save_results(summary)


if __name__ == "__main__":
    main()
