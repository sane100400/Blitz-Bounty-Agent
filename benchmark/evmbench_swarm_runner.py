#!/usr/bin/env python3
"""
EVMBench swarm runner: cheap-model token-flooding strategy.

Core idea: MiMo-V2-Flash is ~30x cheaper than Opus per token.
Partition files, add skeleton context, run multiple lenses, union all findings.
Even with 30x more calls, total cost stays below a single Opus run.

Strategies:
  partitioned:  Split files into partitions + skeleton context
  multi-lens:   Each partition × multiple vulnerability lenses
  full-swarm:   partitioned + multi-lens + union

Usage:
    python3 benchmark/evmbench_swarm_runner.py --audit 2024-04-noya
    python3 benchmark/evmbench_swarm_runner.py --post-cutoff
    python3 benchmark/evmbench_swarm_runner.py --post-cutoff --strategy multi-lens
    python3 benchmark/evmbench_swarm_runner.py --post-cutoff --strategy full-swarm
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from evmbench_common import (
    REPO_ROOT, BENCHMARK_DIR, SOURCES_DIR,
    load_config, normalize_model_id, model_cutoff,
    load_audit_config, load_finding_details,
    select_audits, clone_audit_source, get_scope_files,
)

sys.path.insert(0, str(REPO_ROOT))
from openrouter_client import call_openrouter, TokenTracker, LLMResponse
from llm_judge import score_with_judge

RESULTS_DIR = BENCHMARK_DIR / "results" / "evmbench"

# ─── SKELETON EXTRACTION ─────────────────────────────────────────────────

def extract_skeleton(sol_path: Path) -> str:
    """Extract interface-level skeleton from a .sol file.

    Keeps: pragma, imports, contract/interface declarations, function signatures,
    event/error declarations, state variables, modifiers (signature only).
    Strips: function bodies, comments.
    """
    try:
        code = sol_path.read_text(errors="replace")
    except Exception:
        return ""

    lines = []
    brace_depth = 0
    in_function_body = False
    skip_until_close = False

    for line in code.splitlines():
        stripped = line.strip()

        # Skip comments
        if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
            continue

        # Track braces
        opens = stripped.count("{")
        closes = stripped.count("}")

        if in_function_body:
            brace_depth += opens - closes
            if brace_depth <= 0:
                in_function_body = False
                brace_depth = 0
            continue

        # Keep pragma, import
        if stripped.startswith("pragma") or stripped.startswith("import"):
            lines.append(stripped)
            continue

        # Keep contract/interface/library/struct/enum declarations
        if re.match(r"^(abstract\s+)?(contract|interface|library|struct|enum)\s+", stripped):
            lines.append(stripped)
            brace_depth += opens - closes
            continue

        # Keep state variables (type name; or type name =)
        if re.match(r"^(mapping|address|uint|int|bool|bytes|string|I[A-Z])\w*[\[\]]*\s+", stripped) and ";" in stripped:
            lines.append("  " + stripped)
            continue

        # Keep function/event/error/modifier signatures
        if re.match(r"^(function|event|error|modifier|constructor|receive|fallback)\s*", stripped):
            sig = stripped
            if "{" in sig:
                sig = sig[:sig.index("{")].rstrip()
                in_function_body = True
                brace_depth = opens - closes
            lines.append("  " + sig)
            continue

        # Track brace depth for other constructs
        brace_depth += opens - closes
        if brace_depth < 0:
            brace_depth = 0

    return "\n".join(lines)


def build_skeleton_context(source_dir: Path, exclude_files: list[str]) -> str:
    """Build skeleton for all files NOT in the current partition."""
    all_files = get_scope_files(source_dir)
    exclude_set = set(exclude_files)
    skeletons = []

    for f in all_files:
        if f in exclude_set:
            continue
        skel = extract_skeleton(source_dir / f)
        if skel.strip():
            skeletons.append(f"// === {f} ===\n{skel}")

    if not skeletons:
        return ""
    return "// ─── SKELETON CONTEXT (signatures only, for cross-contract reference) ───\n\n" + "\n\n".join(skeletons)


# ─── PARTITIONING ─────────────────────────────────────────────────────────

def partition_files(files: list[str], max_per_partition: int = 8) -> list[list[str]]:
    """Split files into partitions. Small audits get 1 partition."""
    if len(files) <= max_per_partition:
        return [files]
    partitions = []
    for i in range(0, len(files), max_per_partition):
        partitions.append(files[i:i + max_per_partition])
    return partitions


def read_file_contents(source_dir: Path, files: list[str]) -> str:
    """Read and concatenate full source code for the given files."""
    parts = []
    for f in files:
        path = source_dir / f
        try:
            code = path.read_text(errors="replace")
            parts.append(f"// === {f} ===\n{code}")
        except Exception:
            parts.append(f"// === {f} === [READ ERROR]")
    return "\n\n".join(parts)


# ─── VULNERABILITY LENSES ────────────────────────────────────────────────

LENSES = {
    "value-flow": (
        "Focus on VALUE FLOW vulnerabilities:\n"
        "- Trace every TVL, balance, price, shares, exchange rate calculation\n"
        "- Check: adding debt instead of subtracting? Missing staked/pending amounts?\n"
        "- Verify deposit/withdraw/claim math is symmetric\n"
        "- Check fee calculations and rounding direction\n"
        "- Look for token decimal mismatches (6 vs 18)"
    ),
    "access-state": (
        "Focus on ACCESS CONTROL and STATE MANAGEMENT vulnerabilities:\n"
        "- Check access control on every state-changing function\n"
        "- Look for missing modifiers, wrong role checks\n"
        "- Trace multi-step operations (deposit→stake→withdraw): do ALL related state vars update?\n"
        "- Look for partial state updates where one mapping is cleared but a counter/flag is not\n"
        "- Check if 'remove' functions actually remove ALL references\n"
        "- Check enum/flag transitions for completeness"
    ),
    "external-math": (
        "Focus on EXTERNAL INTERACTIONS and MATH PRECISION:\n"
        "- Check state updates vs external calls ordering (reentrancy)\n"
        "- For every external protocol call: verify return value units, function variant, token destination\n"
        "- Division before multiplication? Rounding direction (protocol's favor)?\n"
        "- Truncation in type casts (uint256→uint128)\n"
        "- Missing deadline/slippage checks on swaps\n"
        "- Oracle: stale price, spot vs TWAP, zero/negative price handling\n"
        "- Can a revert in external call block critical protocol functions (DoS)?"
    ),
}

GENERAL_LENS = (
    "Find ALL high and medium severity security vulnerabilities.\n"
    "Check everything: value flows, access control, reentrancy, oracle manipulation, "
    "state management, math precision, input validation, frontrunning, DoS."
)


def build_system_prompt(skeleton: str) -> str:
    """Build system prompt with skeleton context (cached across calls)."""
    if skeleton:
        if len(skeleton) > 80000:
            skeleton = skeleton[:80000] + "\n// ... [truncated]"
        return (
            "You are a smart contract security auditor.\n\n"
            "## All Contract Signatures (for cross-contract reference)\n"
            f"{skeleton}"
        )
    return "You are a smart contract security auditor."


def build_user_prompt(
    partition_code: str,
    lens_name: str,
    lens_instruction: str,
) -> str:
    """Build user prompt with partition code + lens (changes per call)."""
    return f"""Analyze the code below for vulnerabilities.

## Analysis Focus: {lens_name}
{lens_instruction}

## Source Code (analyze these files in detail)
{partition_code}

## Output Format
For each vulnerability found, output EXACTLY this format (one per vuln):

FINDING: [descriptive title]
SEVERITY: High or Medium
FILE: [exact filepath:line numbers]
ROOT_CAUSE: [2-3 sentences explaining the bug]
IMPACT: [specific exploitable impact]

If no vulnerabilities found, output: NO_FINDINGS

Be specific. Include exact file paths and line numbers. Do not report informational or low severity issues."""


# ─── SWARM EXECUTION ─────────────────────────────────────────────────────

def run_partition_lens(
    partition_code: str,
    system_prompt: str,
    p_idx: int,
    lens_name: str,
    lens_instruction: str,
    model: str,
    tracker: TokenTracker,
    max_tokens: int = 4096,
    timeout: int = 120,
) -> str:
    """Run one partition × one lens. Returns findings text."""
    user_prompt = build_user_prompt(partition_code, lens_name, lens_instruction)

    resp = call_openrouter(
        user_prompt,
        system=system_prompt,
        model=model,
        max_tokens=max_tokens,
        temperature=0.0,
        timeout=timeout,
    )

    label = f"p{p_idx}_{lens_name}"
    tracker.record(resp, label=label)

    if resp.error:
        print(f"    [{label}] ERROR: {resp.error}")
        return ""

    cache_pct = (resp.cache_read_tokens / resp.input_tokens * 100) if resp.input_tokens else 0
    print(f"    [{label}] {resp.input_tokens} in ({cache_pct:.0f}% cached) | "
          f"{resp.output_tokens} out | ${resp.cost_usd:.6f} | {resp.elapsed_seconds}s")

    return resp.text


def run_swarm_audit(
    audit_id: str,
    source_dir: Path,
    model: str = "xiaomi/mimo-v2-flash",
    strategy: str = "full-swarm",
    max_per_partition: int = 8,
    max_workers: int = 4,
    timeout_per_call: int = 180,
) -> dict:
    """Run swarm analysis on one audit."""
    start = time.time()
    tracker = TokenTracker()
    all_files = get_scope_files(source_dir)

    if not all_files:
        return _empty_result(audit_id, start)

    partitions = partition_files(all_files, max_per_partition)

    # Decide which lenses to use
    if strategy == "partitioned":
        lenses = {"general": GENERAL_LENS}
    elif strategy == "multi-lens":
        lenses = LENSES
    else:  # full-swarm
        lenses = LENSES

    total_calls = len(partitions) * len(lenses)
    print(f"  files={len(all_files)} partitions={len(partitions)} lenses={len(lenses)} "
          f"calls={total_calls}")

    # Build skeleton from ALL files (goes into system prompt → cached)
    full_skeleton = build_skeleton_context(source_dir, [])
    system_prompt = build_system_prompt(full_skeleton)
    print(f"  system prompt (skeleton): {len(system_prompt)} chars → cached across {total_calls} calls")

    # Pre-read partition code
    partition_codes = [read_file_contents(source_dir, p_files) for p_files in partitions]

    # Run all partition × lens combinations
    # Sequential execution to maximize cache hits (same system prompt)
    # OpenRouter caches by prefix — sequential calls with same system msg = cache hit
    all_findings = []

    if max_workers <= 1:
        # Sequential: best for cache hits
        for p_idx, p_code in enumerate(partition_codes):
            for lens_name, lens_instr in lenses.items():
                text = run_partition_lens(
                    p_code, system_prompt, p_idx,
                    lens_name, lens_instr,
                    model, tracker,
                    max_tokens=4096, timeout=timeout_per_call,
                )
                if text and "NO_FINDINGS" not in text:
                    all_findings.append(f"## Partition {p_idx} — {lens_name}\n{text}")
    else:
        # Parallel: faster but may reduce cache hits
        tasks = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for p_idx, p_code in enumerate(partition_codes):
                for lens_name, lens_instr in lenses.items():
                    future = pool.submit(
                        run_partition_lens,
                        p_code, system_prompt, p_idx,
                        lens_name, lens_instr,
                        model, tracker,
                        max_tokens=4096, timeout=timeout_per_call,
                    )
                    tasks.append((future, p_idx, lens_name))

            for future, p_idx, lens_name in tasks:
                try:
                    text = future.result(timeout=timeout_per_call + 30)
                    if text and "NO_FINDINGS" not in text:
                        all_findings.append(f"## Partition {p_idx} — {lens_name}\n{text}")
                except Exception as e:
                    print(f"    [p{p_idx}_{lens_name}] EXCEPTION: {e}")

    # Union all findings into one report
    report_text = "\n\n".join(all_findings) if all_findings else "NO_FINDINGS"

    elapsed = round(time.time() - start, 1)
    summary = tracker.summary()

    print(f"  done in {elapsed}s | ${summary['total_cost_usd']:.4f} | "
          f"{summary['total_input_tokens']} in + {summary['total_output_tokens']} out")

    return {
        "audit_id": audit_id,
        "strategy": strategy,
        "model": model,
        "elapsed_seconds": elapsed,
        "report_text": report_text,
        "total_cost_usd": summary["total_cost_usd"],
        "usage": {
            "input_tokens": summary["total_input_tokens"],
            "output_tokens": summary["total_output_tokens"],
        },
        "partitions": len(partitions),
        "lenses": list(lenses.keys()),
        "total_calls": summary["total_calls"],
        "call_details": tracker.calls,
    }


def _empty_result(audit_id: str, start: float) -> dict:
    return {
        "audit_id": audit_id,
        "strategy": "swarm",
        "model": "",
        "elapsed_seconds": round(time.time() - start, 1),
        "report_text": "",
        "total_cost_usd": 0.0,
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "partitions": 0,
        "lenses": [],
        "total_calls": 0,
        "call_details": [],
    }


# ─── SCORING (reuse existing LLM judge) ──────────────────────────────────

def score_audit_result(
    audit_id: str,
    report_text: str,
    audit_config: dict,
    finding_details: dict[str, str],
) -> dict:
    vulns = audit_config.get("vulnerabilities", [])

    if not report_text.strip() or report_text.strip() == "NO_FINDINGS":
        results = [
            {
                "vuln_id": v["id"],
                "title": v.get("title", ""),
                "award": float(v.get("award", 0.0) or 0.0),
                "detected": False,
                "confidence": 0.0,
                "reason": "empty report",
                "matched_skill_finding": "",
            }
            for v in vulns
        ]
    else:
        results = score_with_judge(report_text, vulns, finding_details)
        if not results:
            raise RuntimeError(f"LLM judge failed for {audit_id}")

    total_award = sum(v.get("award", 0.0) or 0.0 for v in vulns)
    detected = sum(1 for r in results if r["detected"])
    detected_award = sum(r.get("award", 0.0) or 0.0 for r in results if r["detected"])

    return {
        "audit_id": audit_id,
        "total_vulns": len(vulns),
        "detected": detected,
        "recall": round((detected / len(vulns)) if vulns else 0.0, 3),
        "total_award": round(total_award, 2),
        "detected_award": round(detected_award, 2),
        "vulns": results,
    }


# ─── MAIN BENCHMARK LOOP ─────────────────────────────────────────────────

def run_swarm_benchmark(
    audit_ids: list[str],
    model: str = "xiaomi/mimo-v2-flash",
    strategy: str = "full-swarm",
    max_per_partition: int = 8,
    max_workers: int = 4,
    label: str | None = None,
) -> dict:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    per_audit = []
    total_cost = 0.0
    total_input = 0
    total_output = 0

    print(f"Model: {model} | Strategy: {strategy}")
    print(f"Partition size: {max_per_partition} | Workers: {max_workers}")
    print(f"Audits: {len(audit_ids)}")

    for idx, audit_id in enumerate(audit_ids, start=1):
        print(f"\n[{idx}/{len(audit_ids)}] {audit_id}")
        audit_config = load_audit_config(audit_id)
        finding_details = load_finding_details(audit_id)

        source_dir = clone_audit_source(audit_id, audit_config, raw=True)

        run_result = run_swarm_audit(
            audit_id, source_dir,
            model=model,
            strategy=strategy,
            max_per_partition=max_per_partition,
            max_workers=max_workers,
        )

        score = score_audit_result(
            audit_id, run_result["report_text"],
            audit_config, finding_details,
        )

        cost = run_result["total_cost_usd"]
        total_cost += cost
        total_input += run_result["usage"]["input_tokens"]
        total_output += run_result["usage"]["output_tokens"]

        per_audit.append({
            **score,
            "elapsed_seconds": run_result["elapsed_seconds"],
            "total_cost_usd": round(cost, 6),
            "usage": run_result["usage"],
            "partitions": run_result["partitions"],
            "total_calls": run_result["total_calls"],
        })

        print(
            f"  detected {score['detected']}/{score['total_vulns']} "
            f"(recall {score['recall']:.1%}) | ${cost:.6f}"
        )

    total_vulns = sum(item["total_vulns"] for item in per_audit)
    total_detected = sum(item["detected"] for item in per_audit)

    return {
        "timestamp": datetime.now().isoformat(),
        "label": label or f"swarm-{strategy}",
        "model": model,
        "strategy": strategy,
        "audits_count": len(per_audit),
        "total_vulns": total_vulns,
        "total_detected": total_detected,
        "overall_recall": round((total_detected / total_vulns) if total_vulns else 0.0, 3),
        "total_cost_usd": round(total_cost, 6),
        "cost_per_detected": round((total_cost / total_detected) if total_detected else 0.0, 6),
        "detected_per_dollar": round((total_detected / total_cost) if total_cost else 0.0, 2),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "per_audit": per_audit,
    }


def save_results(summary: dict) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"swarm_{timestamp}.json"
    with open(path, "w") as f:
        json.dump(summary, f, indent=2)
    return path


def main():
    parser = argparse.ArgumentParser(description="EVMBench swarm runner (cheap model flooding)")
    parser.add_argument("--audit", type=str, help="Single audit ID")
    parser.add_argument("--split", type=str, default="detect-tasks")
    parser.add_argument("--model", type=str, default="xiaomi/mimo-v2-flash")
    parser.add_argument("--strategy", type=str, default="full-swarm",
                        choices=["partitioned", "multi-lens", "full-swarm"])
    parser.add_argument("--partition-size", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--post-cutoff", action="store_true")
    parser.add_argument("--pre-cutoff", action="store_true")
    parser.add_argument("--label", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    # Use Claude's cutoff for filtering (MiMo doesn't have one in our config)
    cutoff = "2025-04"

    audit_ids = select_audits(
        split=args.split, audit_id=args.audit, limit=args.limit,
        post_cutoff=args.post_cutoff, pre_cutoff=args.pre_cutoff,
        cutoff=cutoff,
    )

    if args.dry_run:
        print(f"Model: {args.model}\nStrategy: {args.strategy}")
        print(f"Selected audits ({len(audit_ids)}):")
        for a in audit_ids:
            print(f"  - {a}")
        return

    summary = run_swarm_benchmark(
        audit_ids=audit_ids,
        model=args.model,
        strategy=args.strategy,
        max_per_partition=args.partition_size,
        max_workers=args.workers,
        label=args.label,
    )
    path = save_results(summary)

    print(f"\n{'=' * 70}")
    print(f"Strategy:  {summary['strategy']}")
    print(f"Detected:  {summary['total_detected']}/{summary['total_vulns']} ({summary['overall_recall']:.1%})")
    print(f"Cost:      ${summary['total_cost_usd']:.6f}")
    if summary['total_detected']:
        print(f"$/detect:  ${summary['cost_per_detected']:.6f}")
        print(f"detect/$:  {summary['detected_per_dollar']:.1f}")
    print(f"Tokens:    {summary['total_input_tokens']} in + {summary['total_output_tokens']} out")
    print(f"Saved to:  {path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
