#!/usr/bin/env python3
"""
EVMBench Skill Wrapper — runs our /audit-hunt skill against EVMBench cases
and scores results against ground truth findings.

This measures how well our custom hunting skills perform compared to
the official EVMBench harness results.

Usage:
    # Run all detect-tasks cases
    python3 benchmark/evmbench_skill_runner.py --split detect-tasks

    # Run a single audit
    python3 benchmark/evmbench_skill_runner.py --audit 2024-04-noya

    # Dry run (validate setup, no API calls)
    python3 benchmark/evmbench_skill_runner.py --dry-run

    # Compare with previous run
    python3 benchmark/evmbench_skill_runner.py --compare
"""

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import yaml
from datetime import datetime
from pathlib import Path

# ─── PATHS ──────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent
EVMBENCH_DIR = REPO_ROOT / "evmbench-upstream" / "project" / "evmbench"
AUDITS_DIR = EVMBENCH_DIR / "audits"
SPLITS_DIR = EVMBENCH_DIR / "splits"
RESULTS_DIR = Path(__file__).parent / "results" / "evmbench"
SOURCES_DIR = REPO_ROOT / "evmbench-sources"  # cloned audit source code

# ─── CONFIG ─────────────────────────────────────────────────────────────────

# Max time per audit (seconds) — increased for thorough analysis
TIMEOUT_PER_AUDIT = 900  # 15 min default (was 10 min)

def _set_timeout(val):
    global TIMEOUT_PER_AUDIT
    TIMEOUT_PER_AUDIT = val

# Scoring weights
WEIGHTS = {
    "recall": 0.40,       # % of vulns found
    "precision": 0.30,    # valid findings / total findings
    "severity": 0.15,     # correct severity classification
    "cost": 0.15,         # normalized cost efficiency
}


def load_split(split_name: str) -> list[str]:
    """Load audit IDs from a split file."""
    split_file = SPLITS_DIR / f"{split_name}.txt"
    if not split_file.exists():
        print(f"Error: split '{split_name}' not found at {split_file}")
        sys.exit(1)
    return [line.strip() for line in split_file.read_text().splitlines() if line.strip()]


def load_audit_config(audit_id: str) -> dict:
    """Load audit config.yaml with vulnerability ground truth."""
    config_path = AUDITS_DIR / audit_id / "config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f)


def load_gold_audit(audit_id: str) -> str:
    """Load the gold standard audit report for comparison."""
    gold_path = AUDITS_DIR / audit_id / "findings" / "gold_audit.md"
    if gold_path.exists():
        return gold_path.read_text()
    return ""


def load_finding_details(audit_id: str) -> dict[str, str]:
    """Load individual finding files for keyword extraction."""
    findings_dir = AUDITS_DIR / audit_id / "findings"
    findings = {}
    if findings_dir.exists():
        for f in findings_dir.glob("H-*.md"):
            vuln_id = f.stem  # e.g., "H-01"
            findings[vuln_id] = f.read_text()
    return findings


def extract_keywords_from_finding(finding_text: str) -> list[str]:
    """Extract key phrases from a finding for fuzzy matching."""
    keywords = []
    # Extract title (first line after #)
    title_match = re.search(r"#\s*\[H-\d+\]\s*(.*)", finding_text)
    if title_match:
        title = title_match.group(1).strip()
        # Split title into meaningful phrases (3+ words)
        words = title.split()
        if len(words) >= 3:
            keywords.append(title.lower())
            # Add 3-word sliding windows
            for i in range(len(words) - 2):
                phrase = " ".join(words[i : i + 3]).lower()
                # Skip generic phrases
                if not all(w in ("the", "a", "an", "in", "of", "to", "is", "can", "be") for w in words[i : i + 3]):
                    keywords.append(phrase)

    # Extract function names mentioned
    func_names = re.findall(r"`(\w+(?:\.\w+)*)\(`", finding_text)
    keywords.extend([f.lower() for f in func_names])

    # Extract contract names
    contract_names = re.findall(r"`(\w+(?:\.sol)?)`", finding_text)
    keywords.extend([c.lower().replace(".sol", "") for c in contract_names if len(c) > 3])

    return list(set(keywords))


def clone_audit_source(audit_id: str, audit_config: dict) -> Path:
    """Clone the actual source code repo for an EVMBench audit.

    EVMBench stores code in Docker images built from evmbench-org/{audit-id}.
    We clone the repo locally and checkout the base_commit so the skill
    can actually read the vulnerable source code.
    """
    source_dir = SOURCES_DIR / audit_id
    if source_dir.exists() and any(source_dir.iterdir()):
        # Already cloned — just ensure correct commit
        base_commit = audit_config.get("base_commit")
        if base_commit:
            subprocess.run(
                ["git", "checkout", base_commit],
                cwd=str(source_dir),
                capture_output=True,
            )
        return source_dir

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)

    # Clone from evmbench-org (the standardized fork)
    repo_url = f"https://github.com/evmbench-org/{audit_id}.git"
    print(f"    Cloning {repo_url}...")

    clone_result = subprocess.run(
        ["git", "clone", "--depth", "50", repo_url, str(source_dir)],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if clone_result.returncode != 0:
        # Fallback: try code-423n4 org
        fallback_url = f"https://github.com/code-423n4/{audit_id}.git"
        print(f"    evmbench-org failed, trying {fallback_url}...")
        subprocess.run(
            ["git", "clone", "--depth", "50", fallback_url, str(source_dir)],
            capture_output=True,
            text=True,
            timeout=120,
        )

    # Checkout the specific vulnerable commit
    base_commit = audit_config.get("base_commit")
    if base_commit and source_dir.exists():
        # Need full history to reach the commit
        subprocess.run(
            ["git", "fetch", "--unshallow"],
            cwd=str(source_dir),
            capture_output=True,
            timeout=120,
        )
        subprocess.run(
            ["git", "checkout", base_commit],
            cwd=str(source_dir),
            capture_output=True,
        )

    return source_dir


def find_solidity_dirs(source_dir: Path) -> list[str]:
    """Find directories containing Solidity source files (not test/lib)."""
    sol_dirs = set()
    for sol_file in source_dir.rglob("*.sol"):
        rel = sol_file.relative_to(source_dir)
        parts = rel.parts
        # Skip test, lib, node_modules, out, cache
        skip = {"test", "tests", "lib", "node_modules", "out", "cache", "mock", "mocks"}
        if any(p.lower() in skip for p in parts):
            continue
        sol_dirs.add(str(sol_file.parent))
    return sorted(sol_dirs)


def run_skill_on_audit(audit_id: str, audit_config: dict, mode: str = "hunt") -> dict:
    """Run /audit-hunt or /audit-loop skill on a single audit.

    Args:
        mode: "hunt" for single-shot, "loop" for iterative (3 iterations)
    """
    # Clone actual source code
    source_dir = clone_audit_source(audit_id, audit_config)

    if not source_dir.exists():
        return {
            "audit_id": audit_id,
            "output": "[CLONE FAILED]",
            "exit_code": -2,
            "elapsed_seconds": 0,
        }

    # Find where the Solidity files actually are
    sol_dirs = find_solidity_dirs(source_dir)
    sol_count = len(list(source_dir.rglob("*.sol")))
    contracts_hint = ""
    if sol_dirs:
        # List the top-level contract directories
        unique_roots = set()
        for d in sol_dirs:
            rel = Path(d).relative_to(source_dir)
            unique_roots.add(str(rel).split("/")[0])
        contracts_hint = f"Contract source directories: {', '.join(sorted(unique_roots))}. "

    start = time.time()

    # Build prompt — point at the ACTUAL cloned source
    if mode == "loop":
        skill_cmd = f"/audit-loop {source_dir} codearena 3"
        extra = (
            "Run 3 iterations. Each iteration should cover files missed in previous iterations. "
            "After iteration 1, check which .sol files were NOT analyzed and prioritize them. "
        )
    else:
        skill_cmd = f"/audit-hunt {source_dir} codearena"
        extra = ""

    prompt = (
        f"{skill_cmd}\n\n"
        f"IMPORTANT: The full source code has been cloned to {source_dir}. "
        f"There are {sol_count} Solidity files. {contracts_hint}"
        f"This is a Code4rena audit contest ({audit_id}). "
        f"{extra}"
        f"CRITICAL INSTRUCTIONS:\n"
        f"1. Read EVERY .sol file in scope — not just the 'interesting' ones. "
        f"   Use the Agent tool to parallelize reading if there are 50+ files.\n"
        f"2. For protocols with repeated patterns (Connectors, Adapters, Strategies), "
        f"   cross-compare ALL implementations of the same interface.\n"
        f"3. Trace EVERY function that returns a value/balance/TVL/price. "
        f"   Check: correct add/subtract? handles debt? includes staked tokens?\n"
        f"4. Do NOT rely on prior knowledge — read the actual code.\n"
        f"5. For each finding, cite exact file path, function name, and line.\n"
        f"6. Write the full report to audit-reports/summary.md.\n"
    )

    cmd = [
        "claude",
        "-p", prompt,
        "--output-format", "text",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_PER_AUDIT,
            cwd=str(source_dir),  # Run FROM the source dir so claude can read files
            env={**os.environ, "CLAUDE_AUTO_ACCEPT_PERMISSIONS": "true"},
        )
        output = result.stdout or ""
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        output = "[TIMEOUT]"
        exit_code = -1

    elapsed = time.time() - start

    # claude -p only returns the final summary. The full audit report
    # is written to audit-reports/summary.md by the skill.
    # Read it if it exists — it has the detailed findings.
    full_report = ""
    report_paths = [
        source_dir / "audit-reports" / "summary.md",
        source_dir / "audit-report.md",
        REPO_ROOT / "audit-reports" / "summary.md",
    ]
    for rp in report_paths:
        if rp.exists():
            full_report = rp.read_text()
            break

    # Combine stdout + full report for scoring
    combined_output = output + "\n\n" + full_report

    return {
        "audit_id": audit_id,
        "output": combined_output,
        "exit_code": exit_code,
        "elapsed_seconds": round(elapsed, 1),
        "sol_count": sol_count,
        "source_dir": str(source_dir),
        "report_found": bool(full_report),
        "report_length": len(full_report),
    }


def extract_core_identifiers(text: str) -> set[str]:
    """Extract function names, contract names, and .sol filenames from text.

    Only extracts Solidity-specific identifiers to avoid matching
    generic English words.
    """
    identifiers = set()

    # Function calls with parens: word( or word.word(
    for m in re.findall(r'\b(\w{4,})\s*\(', text):
        identifiers.add(m.lower())
    for m in re.findall(r'\b(\w+\.\w+)\s*\(', text):
        identifiers.add(m.lower())

    # .sol filenames (specific — these are strong signals)
    for m in re.findall(r'(\w+)\.sol', text):
        identifiers.add(m.lower() + ".sol")

    # camelCase/PascalCase compound names (5+ chars, must have mixed case)
    for m in re.findall(r'\b([a-z]+[A-Z]\w{3,}|[A-Z][a-z]+[A-Z]\w{2,})\b', text):
        identifiers.add(m.lower())

    return identifiers


# Words that appear in nearly every Solidity audit — exclude from matching
AUDIT_COMMON_WORDS = {
    # Solidity keywords
    "function", "returns", "uint256", "address", "public", "internal",
    "external", "memory", "storage", "calldata", "require", "revert",
    "event", "emit", "contract", "interface", "import", "mapping",
    "struct", "modifier", "constructor", "view", "pure", "payable",
    "true", "false", "msg.sender", "block", "return", "bytes",
    # Common audit words
    "high", "medium", "critical", "severity", "finding", "impact",
    "attack", "attacker", "user", "vulnerability", "exploit",
    "manual", "review", "audit", "report", "funds", "tokens",
    "amount", "value", "price", "position", "balance",
    # Common interfaces/libs
    "ierc20", "ierc721", "ierc1155", "openzeppelin", "safemath",
    # Generic code words
    "encode", "abi.encode", "keccak256", "deposit", "withdraw",
    "transfer", "approve", "after", "before", "first", "here",
    "both", "when", "execute", "call", "data", "result",
    "collateral", "token", "pool", "vault",
}


def score_audit_result(
    audit_id: str,
    output: str,
    audit_config: dict,
    finding_details: dict[str, str],
    use_llm_judge: bool = True,
) -> dict:
    """Score the skill output against ground truth vulnerabilities.

    Scoring strategy (in order of preference):
    1. LLM-as-Judge: semantic matching via Haiku (most accurate)
    2. Identifier matching: .sol files + camelCase names (fallback)
    """
    vulns = audit_config.get("vulnerabilities", [])

    # ── Try LLM judge first ──
    if use_llm_judge:
        try:
            from llm_judge import LLMJudge
            judge = LLMJudge()
            llm_results = judge.score_findings(output, vulns, finding_details)
            if llm_results:
                return _build_score_result(audit_id, vulns, llm_results, method="llm_judge")
        except Exception as e:
            print(f"    LLM judge failed ({e}), falling back to identifier matching")

    # ── Fallback: identifier matching ──
    results_per_vuln = _score_by_identifiers(output, vulns, finding_details)
    return _build_score_result(audit_id, vulns, results_per_vuln, method="identifier")


def _score_by_identifiers(
    output: str,
    vulns: list[dict],
    finding_details: dict[str, str],
) -> list[dict]:
    """Fallback scoring via code identifier overlap."""
    output_lower = output.lower()
    output_identifiers = extract_core_identifiers(output)
    output_specific = output_identifiers - AUDIT_COMMON_WORDS

    results = []
    for vuln in vulns:
        vuln_id = vuln["id"]
        title = vuln.get("title", "")
        award = vuln.get("award", 0)

        finding_text = finding_details.get(vuln_id, "")
        gt_identifiers = extract_core_identifiers(title + " " + finding_text)
        gt_specific = gt_identifiers - AUDIT_COMMON_WORDS
        matched_identifiers = gt_specific & output_specific

        stopwords = {"the", "and", "for", "are", "not", "can", "will", "when",
                     "from", "with", "that", "this", "into", "been", "have",
                     "should", "instead", "which", "than", "also", "used",
                     "using", "does", "due", "any", "all", "its"}
        title_words = [w.lower() for w in re.findall(r'\b\w{4,}\b', title)
                       if w.lower() not in stopwords]
        matched_title = [w for w in title_words if w in output_lower]
        title_ratio = len(matched_title) / max(len(title_words), 1)

        vuln_id_found = vuln_id.lower() in output_lower
        sol_file_match = any(m.endswith(".sol") for m in matched_identifiers)
        detected = (
            vuln_id_found
            or (sol_file_match and len(matched_identifiers) >= 2)
            or len(matched_identifiers) >= 3
            or (title_ratio >= 0.6 and len(matched_title) >= 3)
        )

        results.append({
            "vuln_id": vuln_id,
            "title": title,
            "award": award,
            "detected": detected,
            "confidence": 1.0 if detected else 0.0,
            "reason": f"ids:{len(matched_identifiers)} title:{len(matched_title)}",
            "matched_skill_finding": "",
        })

    return results


def _build_score_result(
    audit_id: str,
    vulns: list[dict],
    results_per_vuln: list[dict],
    method: str,
) -> dict:

    # Aggregate scores
    total = len(results_per_vuln)
    detected_count = sum(1 for r in results_per_vuln if r["detected"])
    total_award = sum(v.get("award", 0) for v in vulns)
    detected_award = sum(
        r["award"] for r in results_per_vuln if r["detected"]
    )
    recall = detected_count / total if total > 0 else 0

    return {
        "audit_id": audit_id,
        "total_vulns": total,
        "detected": detected_count,
        "recall": round(recall, 3),
        "total_award": round(total_award, 2),
        "detected_award": round(detected_award, 2),
        "vulns": results_per_vuln,
        "scoring_method": method,
    }


def run_benchmark(audit_ids: list[str], dry_run: bool = False, use_llm_judge: bool = True, mode: str = "hunt") -> dict:
    """Run the full benchmark across all specified audits."""
    all_scores = []
    all_raw = []

    print(f"\n{'='*70}")
    print(f"EVMBench Skill Benchmark — {len(audit_ids)} audits")
    print(f"Model: Claude Opus 4.6 | Knowledge cutoff: May 2025")
    print(f"{'='*70}\n")

    for i, audit_id in enumerate(audit_ids, 1):
        audit_config = load_audit_config(audit_id)
        vulns = audit_config.get("vulnerabilities", [])
        finding_details = load_finding_details(audit_id)

        print(f"[{i}/{len(audit_ids)}] {audit_id} ({len(vulns)} vulns)")

        if not dry_run:
            # Pre-clone to show progress
            src = clone_audit_source(audit_id, audit_config)
            sol_count = len(list(src.rglob("*.sol"))) if src.exists() else 0
            print(f"    Source: {src} ({sol_count} .sol files)")

        if dry_run:
            print(f"  → [DRY RUN] Would run /audit-hunt on {audit_id}")
            continue

        # Run skill
        raw = run_skill_on_audit(audit_id, audit_config, mode=mode)
        all_raw.append(raw)

        # Save raw output for debugging
        output_dir = RESULTS_DIR / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{audit_id}.txt"
        output_file.write_text(raw["output"] or "[EMPTY]")
        print(f"    Output saved: {output_file} ({len(raw['output'] or '')} chars)")

        # Score
        score = score_audit_result(
            audit_id, raw["output"], audit_config, finding_details,
            use_llm_judge=use_llm_judge,
        )
        all_scores.append(score)

        # Print per-audit summary
        print(f"  → {score['detected']}/{score['total_vulns']} found "
              f"(recall: {score['recall']:.1%}) "
              f"| ${score['detected_award']:.0f}/${score['total_award']:.0f} award "
              f"| {raw['elapsed_seconds']}s")

        # Print per-vuln details
        method = score.get("scoring_method", "identifier")
        for v in score["vulns"]:
            status = "✓" if v["detected"] else "✗"
            conf = v.get("confidence", 0)
            reason = v.get("reason", "")[:60]
            matched = v.get("matched_skill_finding", "")
            extra = f" → {matched}" if matched and v["detected"] else ""
            print(f"    {status} {v['vuln_id']}: {v['title'][:50]}.. [{method} conf:{conf:.0%}]{extra}")
            if reason and (not v["detected"] or conf < 0.8):
                print(f"      reason: {reason}")

    if dry_run:
        print(f"\nDry run complete. {len(audit_ids)} audits validated.")
        return {}

    # Aggregate
    total_vulns = sum(s["total_vulns"] for s in all_scores)
    total_detected = sum(s["detected"] for s in all_scores)
    total_award_possible = sum(s["total_award"] for s in all_scores)
    total_award_detected = sum(s["detected_award"] for s in all_scores)
    overall_recall = total_detected / total_vulns if total_vulns > 0 else 0

    summary = {
        "timestamp": datetime.now().isoformat(),
        "model": "claude-opus-4-6",
        "knowledge_cutoff": "2025-05",
        "mode": "skill-wrapper",
        "audits_count": len(audit_ids),
        "total_vulns": total_vulns,
        "total_detected": total_detected,
        "overall_recall": round(overall_recall, 3),
        "total_award_possible": round(total_award_possible, 2),
        "total_award_detected": round(total_award_detected, 2),
        "per_audit": all_scores,
    }

    print(f"\n{'='*70}")
    print(f"RESULTS SUMMARY")
    print(f"{'='*70}")
    print(f"Audits:    {len(audit_ids)}")
    print(f"Vulns:     {total_detected}/{total_vulns} detected")
    print(f"Recall:    {overall_recall:.1%}")
    print(f"Awards:    ${total_award_detected:.2f} / ${total_award_possible:.2f}")
    print(f"{'='*70}")

    return summary


def save_results(summary: dict):
    """Save benchmark results."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"skill_run_{timestamp}.json"
    with open(path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nResults saved to {path}")
    return path


def rescore_existing(audit_ids: list[str], use_llm_judge: bool = True):
    """Re-score existing outputs without re-running skills."""
    print(f"\n{'='*70}")
    print(f"RE-SCORING {len(audit_ids)} audits from saved outputs")
    print(f"Judge: {'LLM (Haiku)' if use_llm_judge else 'identifier matching'}")
    print(f"{'='*70}\n")

    all_scores = []
    for i, audit_id in enumerate(audit_ids, 1):
        audit_config = load_audit_config(audit_id)
        vulns = audit_config.get("vulnerabilities", [])
        finding_details = load_finding_details(audit_id)

        # Read saved output + report
        output_file = RESULTS_DIR / "outputs" / f"{audit_id}.txt"
        report_file = SOURCES_DIR / audit_id / "audit-reports" / "summary.md"

        combined = ""
        if output_file.exists():
            combined += output_file.read_text()
        if report_file.exists():
            combined += "\n\n" + report_file.read_text()

        if not combined.strip():
            print(f"[{i}/{len(audit_ids)}] {audit_id} — no saved output, skipping")
            continue

        print(f"[{i}/{len(audit_ids)}] {audit_id} ({len(vulns)} vulns)")
        score = score_audit_result(
            audit_id, combined, audit_config, finding_details,
            use_llm_judge=use_llm_judge,
        )
        all_scores.append(score)

        method = score.get("scoring_method", "?")
        print(f"  → {score['detected']}/{score['total_vulns']} "
              f"(recall: {score['recall']:.1%}) [{method}]")

        for v in score["vulns"]:
            status = "✓" if v["detected"] else "✗"
            conf = v.get("confidence", 0)
            print(f"    {status} {v['vuln_id']}: {v['title'][:50]}.. [conf:{conf:.0%}]")

    if all_scores:
        total_v = sum(s["total_vulns"] for s in all_scores)
        total_d = sum(s["detected"] for s in all_scores)
        recall = total_d / total_v if total_v else 0
        print(f"\n{'='*70}")
        print(f"RESCORE: {total_d}/{total_v} ({recall:.1%})")
        print(f"{'='*70}")


def compare_runs():
    """Compare last two skill wrapper runs."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    runs = sorted(RESULTS_DIR.glob("skill_run_*.json"))
    if len(runs) < 2:
        print("Need at least 2 runs to compare.")
        return

    with open(runs[-2]) as f:
        prev = json.load(f)
    with open(runs[-1]) as f:
        curr = json.load(f)

    print(f"\n{'Metric':<25} {'Previous':>12} {'Current':>12} {'Delta':>12}")
    print("-" * 63)
    for key in ["overall_recall", "total_detected", "total_award_detected"]:
        p = prev.get(key, 0)
        c = curr.get(key, 0)
        delta = c - p
        sign = "+" if delta > 0 else ""
        if isinstance(p, float):
            print(f"{key:<25} {p:>12.3f} {c:>12.3f} {sign}{delta:>11.3f}")
        else:
            print(f"{key:<25} {p:>12} {c:>12} {sign}{delta:>11}")

    # Per-audit comparison
    prev_audits = {a["audit_id"]: a for a in prev.get("per_audit", [])}
    curr_audits = {a["audit_id"]: a for a in curr.get("per_audit", [])}

    common = set(prev_audits.keys()) & set(curr_audits.keys())
    if common:
        print(f"\nPer-audit recall changes:")
        for audit_id in sorted(common):
            pr = prev_audits[audit_id]["recall"]
            cr = curr_audits[audit_id]["recall"]
            if pr != cr:
                delta = cr - pr
                sign = "+" if delta > 0 else ""
                print(f"  {audit_id:<35} {pr:.1%} → {cr:.1%} ({sign}{delta:.1%})")


def main():
    parser = argparse.ArgumentParser(description="EVMBench Skill Wrapper Benchmark")
    parser.add_argument("--split", type=str, help="EVMBench split (detect-tasks, patch-tasks, exploit-tasks, debug)")
    parser.add_argument("--audit", type=str, help="Single audit ID (e.g., 2024-04-noya)")
    parser.add_argument("--dry-run", action="store_true", help="Validate setup without API calls")
    parser.add_argument("--compare", action="store_true", help="Compare last two runs")
    parser.add_argument("--limit", type=int, help="Limit number of audits to run")
    parser.add_argument("--no-judge", action="store_true", help="Disable LLM judge, use identifier matching only")
    parser.add_argument("--rescore", action="store_true", help="Re-score existing outputs without re-running skills")
    parser.add_argument("--timeout", type=int, default=TIMEOUT_PER_AUDIT, help=f"Timeout per audit in seconds (default: {TIMEOUT_PER_AUDIT})")
    parser.add_argument("--mode", choices=["hunt", "loop"], default="hunt", help="Skill mode: hunt (single-shot) or loop (iterative)")
    args = parser.parse_args()

    if args.compare:
        compare_runs()
        return

    # Apply timeout override
    if args.timeout != 900:
        _set_timeout(args.timeout)

    # Validate EVMBench is cloned
    if not AUDITS_DIR.exists():
        print("Error: EVMBench not found. Run: bash benchmark/evmbench_setup.sh")
        sys.exit(1)

    # Determine which audits to run
    if args.audit:
        audit_ids = [args.audit]
    elif args.split:
        audit_ids = load_split(args.split)
    else:
        print("Error: --split or --audit required (or use --compare / --rescore)")
        sys.exit(1)

    if args.limit:
        audit_ids = audit_ids[: args.limit]

    use_judge = not args.no_judge

    # Re-score mode: use existing outputs
    if args.rescore:
        rescore_existing(audit_ids, use_llm_judge=use_judge)
        return

    summary = run_benchmark(audit_ids, dry_run=args.dry_run, use_llm_judge=use_judge, mode=args.mode)

    if summary:
        save_results(summary)


if __name__ == "__main__":
    main()
