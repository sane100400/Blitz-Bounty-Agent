#!/usr/bin/env python3
"""
Multi-agent audit orchestrator.

Coordinates specialized parallel agents for deep smart contract analysis.
Instead of one monolithic Claude call, runs multiple focused agents in parallel
and merges their findings.

Architecture:
    Phase 1: Recon Agent (1) → scope, file list, pattern groups
    Phase 2: Specialist Agents (parallel) → focused vulnerability analysis
    Phase 3: Merger Agent (1) → deduplicate, triage, severity, report

Usage:
    # Standalone
    python3 audit_orchestrator.py <source-dir> [--platform codearena] [--timeout 900]

    # As EVMBench benchmark
    python3 audit_orchestrator.py <source-dir> --benchmark --audit-id 2024-04-noya
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from claude_cli import ClaudeCliUnavailable, run_claude_prompt

# Force unbuffered stdout for real-time progress
sys.stdout.reconfigure(line_buffering=True)

# ─── CONFIG ────────────────────────────────────────────────────────────────

TIMEOUT_PER_AGENT = 600      # 10 min per specialist agent
MERGE_TIMEOUT = 300          # 5 min for merge
MAX_PARALLEL_AGENTS = 5
REPORTS_DIR = Path("audit-reports")

# Per-agent budget caps (USD) — prevents runaway output tokens
DEFAULT_SPECIALIST_BUDGET = 0.50
DEFAULT_MERGER_BUDGET = 0.80

# Model assignments — deep reasoning for semantic analysis, fast model for grep/pattern work
DEFAULT_MODEL_DEEP = "claude-opus-4-6"
DEFAULT_MODEL_FAST = "claude-sonnet-4-6"

# ─── TOKEN TRACKING ───────────────────────────────────────────────────────

# Best-effort fallback pricing for Opus 4.6. The canonical run cost comes from
# `total_cost_usd` returned by the CLI, which is what the benchmark uses.
_PRICE_INPUT = 5.0
_PRICE_OUTPUT = 25.0
_PRICE_CACHE_CREATE = 6.25
_PRICE_CACHE_READ = 0.50


class TokenTracker:
    """Tracks token usage and cost across all agent calls."""

    def __init__(self):
        self.calls: list[dict] = []   # per-call records
        self.total_input = 0
        self.total_output = 0
        self.total_cache_create = 0
        self.total_cache_read = 0
        self.total_cost = 0.0

    def reset(self):
        self.calls.clear()
        self.total_input = 0
        self.total_output = 0
        self.total_cache_create = 0
        self.total_cache_read = 0
        self.total_cost = 0.0

    def record(self, agent_name: str, usage: dict, cost: float):
        inp = usage.get("input_tokens", 0)
        out = usage.get("output_tokens", 0)
        cache_create = usage.get("cache_creation_input_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)

        self.total_input += inp
        self.total_output += out
        self.total_cache_create += cache_create
        self.total_cache_read += cache_read
        self.total_cost += cost

        self.calls.append({
            "agent": agent_name,
            "input_tokens": inp,
            "output_tokens": out,
            "cache_create": cache_create,
            "cache_read": cache_read,
            "cost_usd": round(cost, 4),
        })

        print(f"      tokens: {inp:,} in + {out:,} out | cache: {cache_create:,} write, {cache_read:,} read | ${cost:.4f}")

    def estimate_cost(self, input_tokens: int, output_tokens: int,
                      cache_create: int = 0, cache_read: int = 0) -> float:
        return (
            input_tokens * _PRICE_INPUT
            + output_tokens * _PRICE_OUTPUT
            + cache_create * _PRICE_CACHE_CREATE
            + cache_read * _PRICE_CACHE_READ
        ) / 1_000_000

    def summary(self) -> str:
        lines = [
            f"\n{'─'*60}",
            f"  TOKEN USAGE SUMMARY",
            f"{'─'*60}",
        ]
        for c in self.calls:
            lines.append(
                f"  {c['agent']:30s}  {c['input_tokens']:>8,} in  {c['output_tokens']:>7,} out  ${c['cost_usd']:.4f}"
            )
        lines.append(f"{'─'*60}")
        lines.append(
            f"  {'TOTAL':30s}  {self.total_input:>8,} in  {self.total_output:>7,} out  ${self.total_cost:.4f}"
        )
        lines.append(f"  Cache: {self.total_cache_create:,} created, {self.total_cache_read:,} read")
        lines.append(f"{'─'*60}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "total_input_tokens": self.total_input,
            "total_output_tokens": self.total_output,
            "total_cache_create_tokens": self.total_cache_create,
            "total_cache_read_tokens": self.total_cache_read,
            "total_cost_usd": round(self.total_cost, 4),
        }


# Global tracker instance
token_tracker = TokenTracker()


# ─── AGENT RUNNER ──────────────────────────────────────────────────────────

def run_claude(
    prompt: str,
    cwd: str,
    timeout: int = 600,
    agent_name: str = "unknown",
    model: str | None = None,
    max_budget: float | None = None,
) -> str:
    """Run a single claude -p call, track tokens, and return text output."""
    try:
        result = run_claude_prompt(
            prompt,
            cwd=cwd,
            timeout=timeout,
            output_format="json",
            model=model,
            max_budget=max_budget,
            auto_accept_permissions=True,
        )
        raw = result.get("raw", "")
        if result.get("timed_out"):
            return "[TIMEOUT]"

        # Parse JSON to extract usage + text
        try:
            data = result.get("parsed") or json.loads(raw)
            text = data.get("result", "")
            usage = result.get("usage", {}) or data.get("usage", {})
            cost = result.get("total_cost_usd", 0.0) or data.get("total_cost_usd", 0.0)
            token_tracker.record(agent_name, usage, cost)
            return text
        except (json.JSONDecodeError, KeyError):
            # Fallback: treat raw output as text (old claude versions)
            return raw

    except ClaudeCliUnavailable as e:
        return f"[ERROR: Claude CLI unavailable: {e}]"
    except Exception as e:
        return f"[ERROR: {e}]"


# ─── PHASE 1: RECON (pure Python — no LLM call) ─────────────────────────

# Known DeFi protocol keywords for external integration detection
_EXTERNAL_KEYWORDS: dict[str, list[str]] = {
    "aave": ["Aave"], "compound": ["Compound"], "morpho": ["Morpho"],
    "balancer": ["Balancer"], "aura": ["Aura"], "curve": ["Curve"],
    "convex": ["Convex"], "pendle": ["Pendle"], "lido": ["Lido"],
    "uniswap": ["Uniswap"], "sushiswap": ["SushiSwap"], "yearn": ["Yearn"],
    "maker": ["Maker"], "dolomite": ["Dolomite"], "gmx": ["GMX"],
    "camelot": ["Camelot"], "radiant": ["Radiant"], "silo": ["Silo"],
    "fluid": ["Fluid"], "euler": ["Euler"], "gearbox": ["Gearbox"],
    "chainlink": ["Chainlink"], "synthetix": ["Synthetix"],
}

# Pattern group classification by filename suffix/keyword
_GROUP_PATTERNS: dict[str, list[str]] = {
    "connectors": ["connector"],
    "strategies": ["strategy"],
    "adapters": ["adapter"],
    "vaults": ["vault"],
    "oracles": ["oracle"],
    "handlers": ["handler"],
    "core": ["manager", "registry", "factory", "router", "controller", "governance"],
}

_SKIP_DIRS = {"test", "tests", "lib", "node_modules", "out", "cache", "mock", "mocks", "script", "scripts"}


def _classify_file(name_lower: str) -> str:
    """Classify a .sol file into a pattern group."""
    for group, keywords in _GROUP_PATTERNS.items():
        if any(kw in name_lower for kw in keywords):
            return group
    return "other"


def _detect_externals(source_text: str) -> list[str]:
    """Detect external DeFi protocol references from import paths and identifiers."""
    source_lower = source_text.lower()
    found = []
    for keyword, names in _EXTERNAL_KEYWORDS.items():
        if keyword in source_lower:
            found.extend(names)
    return sorted(set(found))


def run_recon(source_dir: str, platform: str) -> dict:
    """Phase 1: Recon — pure Python codebase mapping. Zero LLM tokens."""
    print("\n[Phase 1] Recon — mapping codebase (Python)...")
    start = time.time()

    root = Path(source_dir)
    sol_files = sorted(root.rglob("*.sol"))

    # Filter out test/lib/etc directories
    in_scope = []
    for f in sol_files:
        try:
            parts = [p.lower() for p in f.relative_to(root).parts]
        except ValueError:
            continue
        if any(p in _SKIP_DIRS for p in parts):
            continue
        in_scope.append(f)

    files_info = []
    pattern_groups: dict[str, list[str]] = {}
    external_protocols: dict[str, list[str]] = {}

    for f in in_scope:
        rel = str(f.relative_to(root))
        name = f.stem
        try:
            text = f.read_text(errors="replace")
        except OSError:
            text = ""
        loc = text.count("\n") + 1 if text else 0

        group = _classify_file(name.lower())
        files_info.append({
            "path": rel,
            "name": name,
            "loc": loc,
            "group": group,
        })

        # Accumulate pattern groups
        pattern_groups.setdefault(group, []).append(rel)

        # Detect external protocol integrations
        externals = _detect_externals(text)
        if externals:
            external_protocols[name] = externals

    # Remove empty/singleton "other" from pattern groups (not useful)
    pattern_groups.pop("other", None)

    elapsed = time.time() - start
    print(f"    Recon completed in {elapsed:.1f}s — {len(in_scope)} files, {len(pattern_groups)} pattern groups")

    return {
        "total_files": len(in_scope),
        "files": files_info,
        "pattern_groups": pattern_groups,
        "external_protocols": external_protocols,
    }


# ─── PHASE 2: SPECIALIST AGENTS ───────────────────────────────────────────

_OUTPUT_FMT = """
OUTPUT FORMAT — for each finding:
FINDING: [title]
FILE: [exact path:line]
ROOT_CAUSE: [2-3 sentences]
IMPACT: [specific impact]
SEVERITY: High|Medium
CODE: [relevant code snippet]
FIX: [1-3 line fix]

If no bugs found in a file, say: CLEAN: [filename] — [what you verified]
""".strip()


def _file_summary(files: list[dict]) -> str:
    """Compact one-line-per-file summary (path + group only). Saves ~60% tokens vs full list."""
    by_group: dict[str, list[str]] = {}
    for f in files:
        by_group.setdefault(f["group"], []).append(f["path"])
    lines = []
    for group, paths in sorted(by_group.items()):
        lines.append(f"  [{group}] {', '.join(paths)}")
    return "\n".join(lines)


def _tvl_prompt(source_dir: str, assigned_files: list[str], file_summary: str) -> str:
    """Shared TVL/accounting prompt template. Assigned files get full analysis."""
    return f"""You are a smart contract security specialist focused on TVL/ACCOUNTING BUGS.

SOURCE: {source_dir}
YOUR FILES TO ANALYZE: {json.dumps(assigned_files)}
CODEBASE MAP (read these only if you need context for cross-references):
{file_summary}

For EACH assigned file:
1. Read the ENTIRE file.
2. Find every function that calculates TVL, balance, value, price, or shares.
3. Trace: what's ADDED (supply, collateral, staked, pending rewards) vs SUBTRACTED (debt, borrowed, fees).
   - Common bug: adding debt instead of subtracting.
   - Common bug: ignoring staked LP in gauge.
4. For every EXTERNAL CALL returning a value used in math: verify UNITS and function variant.
5. Check for underflow when position goes underwater (debt > collateral).

{_OUTPUT_FMT}
"""


def build_specialist_prompts(
    source_dir: str,
    recon: dict,
    platform: str,
    model_deep: str,
    model_fast: str,
) -> list[dict]:
    """Build prompts for each specialist agent based on recon results."""
    files = recon.get("files", [])
    groups = recon.get("pattern_groups", {})
    external = recon.get("external_protocols", {})
    summary = _file_summary(files)

    connectors = groups.get("connectors", [])
    core_files = groups.get("core", [])

    specialists = []

    # ── TVL/Accounting agents (split connectors across agents) ──
    mid = max(len(connectors) // 2, 1)
    conn_a = connectors[:mid]
    conn_b = connectors[mid:]

    if conn_a:
        specialists.append({
            "name": "tvl_accounting_A",
            "focus": "TVL and accounting analysis",
            "model": model_deep,
            "prompt": _tvl_prompt(source_dir, conn_a, summary),
        })
    if conn_b:
        specialists.append({
            "name": "tvl_accounting_B",
            "focus": "TVL and accounting analysis (second group)",
            "model": model_deep,
            "prompt": _tvl_prompt(source_dir, conn_b, summary),
        })

    # ── Position Lifecycle (Sonnet — pattern matching heavy, grep-based) ──
    specialists.append({
        "name": "position_lifecycle",
        "focus": "Position registry and lifecycle tracking",
        "model": model_fast,
        "prompt": f"""You are a smart contract security specialist focused on POSITION LIFECYCLE BUGS.

SOURCE: {source_dir}
CODEBASE MAP:
{summary}

Grep the entire codebase for position-modifying calls (updateHoldingPosition, addHoldingPosition, removePosition, _updateTokenInRegistry).

For EACH call: FILE:LINE | FUNCTION | ACTION (add/remove) | BOOLEAN FLAG VALUE | CORRECT?

Key bugs: inverted boolean flags, wrong connector type in position ID, state change without registry update, isEmpty() missing token locations.

Trace round-trip per connector: deposit → register → TVL → withdraw → deregister. Verify same position ID throughout.

{_OUTPUT_FMT}
""",
    })

    # ── Access Control + Fund Flow (Sonnet — grep + pattern matching) ──
    specialists.append({
        "name": "access_control_fundflow",
        "focus": "Access control, fund flow, and cross-contract attacks",
        "model": model_fast,
        "prompt": f"""You are a smart contract security specialist focused on ACCESS CONTROL and FUND FLOW BUGS.

SOURCE: {source_dir}
CODEBASE MAP:
{summary}

1. MAP TOKEN TRANSFERS: grep for transfer/transferFrom/safeTransfer/send/call{{value:. For each: who triggers, where tokens go, access control.
2. FLASH LOANS: find integrations, check cross-contract state changes, 1-wei griefing on strict balance checks.
3. TRUSTED ADDRESSES: find sendTokensToTrustedAddress patterns, verify destination validation.
4. TOKEN EDGE CASES: blacklist tokens blocking loops, fee-on-transfer mismatches, oracle decimal scaling.
5. CROSS-CONTRACT: map trust relationships, find chained call bypasses.

{_OUTPUT_FMT}
""",
    })

    # ── External Protocol Semantics ──
    if external:
        ext_summary = "\n".join(f"  - {k}: integrates {', '.join(v)}" for k, v in external.items())
    else:
        ext_summary = "  (check connector files for external protocol calls)"

    specialists.append({
        "name": "external_semantics",
        "focus": "External protocol integration correctness",
        "model": model_deep,
        "prompt": f"""You are a smart contract security specialist focused on EXTERNAL PROTOCOL INTEGRATION BUGS.

SOURCE: {source_dir}
KNOWN INTEGRATIONS:
{ext_summary}
CONNECTOR FILES: {json.dumps(connectors)}

For EACH connector, verify every external call:
a) TOKEN DESTINATION: where do tokens go? Read the interface for receiver/to param.
b) RETURN VALUE SEMANTICS: what does the function actually return? Verify units.
c) FUNCTION VARIANT: right variant for pool type? (totalSupply vs getActualSupply, etc.)
d) MISSING LIFECYCLE: recovery mode, claim surplus, claim rewards — all handled?

Build a table: | Connector | External Call | Expected | Actual | Match? |

{_OUTPUT_FMT}
""",
    })

    # ── Core Contract Logic ──
    if core_files:
        specialists.append({
            "name": "core_logic",
            "focus": "Core contract logic (manager, registry, governance)",
            "model": model_deep,
            "prompt": f"""You are a smart contract security specialist focused on CORE CONTRACT LOGIC BUGS.

SOURCE: {source_dir}
YOUR FILES: {json.dumps(core_files)}
CODEBASE MAP:
{summary}

Focus: queue/batch accounting (stuck queues, share miscalculation), modifier/access control consistency, oracle decimal handling, ERC4626 share pricing (zero-share, first depositor inflation), fee logic manipulation.

{_OUTPUT_FMT}
""",
        })

    return specialists


def run_specialist(spec: dict, source_dir: str, timeout: int, budget_specialist: float) -> dict:
    """Run a single specialist agent."""
    name = spec["name"]
    model = spec.get("model", DEFAULT_MODEL_DEEP)
    print(f"    [{name}] Starting — {spec['focus']} [{model}]")
    start = time.time()

    output = run_claude(
        spec["prompt"], source_dir, timeout=timeout,
        agent_name=name, model=model, max_budget=budget_specialist,
    )
    elapsed = time.time() - start

    # Count findings
    finding_count = len(re.findall(r'^FINDING:', output, re.MULTILINE))
    clean_count = len(re.findall(r'^CLEAN:', output, re.MULTILINE))

    print(f"    [{name}] Done in {elapsed:.0f}s — {finding_count} findings, {clean_count} clean files")

    return {
        "name": name,
        "focus": spec["focus"],
        "output": output,
        "elapsed": round(elapsed, 1),
        "finding_count": finding_count,
    }


def run_specialists(
    source_dir: str,
    recon: dict,
    platform: str,
    timeout: int,
    model_deep: str,
    model_fast: str,
    budget_specialist: float,
) -> list[dict]:
    """Phase 2: Run all specialist agents in parallel."""
    specs = build_specialist_prompts(source_dir, recon, platform, model_deep, model_fast)

    print(f"\n[Phase 2] Running {len(specs)} specialist agents in parallel...")

    results = []
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_AGENTS) as executor:
        futures = {
            executor.submit(run_specialist, spec, source_dir, timeout, budget_specialist): spec
            for spec in specs
        }
        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                spec = futures[future]
                print(f"    [{spec['name']}] FAILED: {e}")
                results.append({
                    "name": spec["name"],
                    "focus": spec["focus"],
                    "output": f"[ERROR: {e}]",
                    "elapsed": 0,
                    "finding_count": 0,
                })

    total_findings = sum(r["finding_count"] for r in results)
    if results and all(r["output"].startswith("[ERROR:") for r in results):
        raise RuntimeError(results[0]["output"])
    print(f"\n    Total findings from specialists: {total_findings}")
    return results


# ─── PHASE 3: MERGER ──────────────────────────────────────────────────────

def _extract_findings(output: str) -> str:
    """Extract only FINDING: blocks from specialist output. Drops noise/reasoning."""
    findings = []
    current: list[str] = []
    in_finding = False
    for line in output.splitlines():
        if line.startswith("FINDING:"):
            if current:
                findings.append("\n".join(current))
            current = [line]
            in_finding = True
        elif line.startswith("CLEAN:"):
            if current:
                findings.append("\n".join(current))
                current = []
            in_finding = False
            findings.append(line)
        elif in_finding:
            # End of finding block: next FINDING/CLEAN or double blank
            if line.strip() == "" and current and current[-1].strip() == "":
                findings.append("\n".join(current))
                current = []
                in_finding = False
            else:
                current.append(line)
    if current:
        findings.append("\n".join(current))
    return "\n\n".join(findings)


def run_merger(
    source_dir: str,
    specialist_results: list[dict],
    platform: str,
    model_deep: str,
    budget_merger: float,
    merge_timeout: int,
) -> str:
    """Phase 3: Merge, deduplicate, triage, and produce final report."""
    print("\n[Phase 3] Merging findings...")
    start = time.time()

    # Extract only findings from each specialist (skip reasoning/noise)
    combined = ""
    for r in specialist_results:
        extracted = _extract_findings(r["output"])
        if not extracted.strip():
            continue
        combined += f"\n--- {r['name']} ({r['focus']}) ---\n{extracted}\n"

    if not combined.strip():
        print("    No findings from any specialist — skipping merger")
        return "(no findings)"

    total_findings = sum(r["finding_count"] for r in specialist_results)
    print(f"    Feeding {total_findings} findings to merger ({len(combined):,} chars, was raw ~{sum(len(r['output']) for r in specialist_results):,})")

    prompt = f"""You are the lead auditor merging findings from {len(specialist_results)} specialist agents.

SOURCE: {source_dir}
PLATFORM: {platform}

1. DEDUPLICATE: merge same bug found by different agents.
2. TRIAGE: verify each finding by reading the actual code.
3. SEVERITY: High (direct fund loss, permanent freeze) or Medium (conditional, temporary, value leak).
4. REPORT: write to audit-reports/summary.md.

FINDINGS:
{combined}

RULES: Only merge what's here — don't add new findings. Drop false positives. Verify code before including.

Report format:
# [Protocol] - Audit Report
## Findings Summary
| ID | Severity | Title |
## [H-01] Title
**Lines of code:** `file.sol:XX-YY`
**Root cause:** ...
**Impact:** ...
**Recommended fix:** ...
"""

    output = run_claude(
        prompt, source_dir, timeout=merge_timeout,
        agent_name="merger", model=model_deep, max_budget=budget_merger,
    )
    elapsed = time.time() - start
    print(f"    Merge completed in {elapsed:.0f}s")

    # Also read the report file if it was written
    report_path = Path(source_dir) / "audit-reports" / "summary.md"
    report = ""
    if report_path.exists():
        report = report_path.read_text()

    return output + "\n\n" + report


# ─── MAIN ORCHESTRATOR ────────────────────────────────────────────────────

def orchestrate(
    source_dir: str,
    platform: str = "codearena",
    timeout_per_agent: int = TIMEOUT_PER_AGENT,
    model_deep: str = DEFAULT_MODEL_DEEP,
    model_fast: str = DEFAULT_MODEL_FAST,
    specialist_budget: float = DEFAULT_SPECIALIST_BUDGET,
    merger_budget: float = DEFAULT_MERGER_BUDGET,
    merge_timeout: int = MERGE_TIMEOUT,
) -> dict:
    """Run the full multi-agent audit orchestration."""
    total_start = time.time()
    token_tracker.reset()

    print(f"\n{'='*70}")
    print(f"Multi-Agent Audit Orchestrator")
    print(f"Source: {source_dir}")
    print(f"Platform: {platform}")
    print(f"Deep model: {model_deep}")
    print(f"Fast model: {model_fast}")
    print(f"Budgets: specialists=${specialist_budget:.2f}, merger=${merger_budget:.2f}")
    print(f"{'='*70}")

    # Phase 1: Recon
    recon = run_recon(source_dir, platform)
    total_files = recon.get("total_files", 0)
    groups = recon.get("pattern_groups", {})
    print(f"    Files: {total_files} | Groups: {list(groups.keys())}")

    if not recon.get("files"):
        print("    WARNING: No .sol files found in scope")

    # Phase 2: Specialists
    specialist_results = run_specialists(
        source_dir,
        recon,
        platform,
        timeout_per_agent,
        model_deep,
        model_fast,
        specialist_budget,
    )

    # Phase 3: Merge
    final_output = run_merger(
        source_dir,
        specialist_results,
        platform,
        model_deep,
        merger_budget,
        merge_timeout,
    )

    total_elapsed = time.time() - total_start

    print(f"\n{'='*70}")
    print(f"Orchestration complete in {total_elapsed:.0f}s")
    print(token_tracker.summary())
    print(f"{'='*70}")

    return {
        "source_dir": source_dir,
        "platform": platform,
        "total_elapsed": round(total_elapsed, 1),
        "total_cost_usd": round(token_tracker.total_cost, 4),
        "token_usage": token_tracker.to_dict(),
        "recon": recon,
        "specialist_results": [
            {"name": r["name"], "focus": r["focus"], "finding_count": r["finding_count"], "elapsed": r["elapsed"]}
            for r in specialist_results
        ],
        "final_output": final_output,
        "specialist_outputs": {r["name"]: r["output"] for r in specialist_results},
    }


# ─── EVMBENCH INTEGRATION ─────────────────────────────────────────────────

def run_benchmark_orchestrated(
    audit_id: str,
    timeout: int = 600,
    deep_model: str = DEFAULT_MODEL_DEEP,
    fast_model: str = DEFAULT_MODEL_FAST,
    specialist_budget: float = DEFAULT_SPECIALIST_BUDGET,
    merger_budget: float = DEFAULT_MERGER_BUDGET,
    merge_timeout: int = MERGE_TIMEOUT,
    label: str | None = None,
    use_llm_judge: bool = True,
):
    """Run the orchestrator on an EVMBench audit and score results."""
    # Import scoring from skill runner
    sys.path.insert(0, str(Path(__file__).parent / "benchmark"))
    from evmbench_skill_runner import (
        clone_audit_source,
        load_audit_config,
        load_finding_details,
        normalize_model_id,
        score_audit_result,
        RESULTS_DIR,
    )

    print(f"\nEVMBench Orchestrated Benchmark — {audit_id}")

    audit_config = load_audit_config(audit_id)
    vulns = audit_config.get("vulnerabilities", [])
    finding_details = load_finding_details(audit_id)

    # Clone source
    source_dir = clone_audit_source(audit_id, audit_config)
    if not source_dir.exists():
        print("Clone failed")
        return

    # Run orchestrator
    deep_model = normalize_model_id(deep_model)
    fast_model = normalize_model_id(fast_model)
    result = orchestrate(
        str(source_dir),
        platform="codearena",
        timeout_per_agent=timeout,
        model_deep=deep_model,
        model_fast=fast_model,
        specialist_budget=specialist_budget,
        merger_budget=merger_budget,
        merge_timeout=merge_timeout,
    )

    # Combine all outputs for scoring
    combined_output = result["final_output"]
    for name, output in result.get("specialist_outputs", {}).items():
        combined_output += f"\n\n--- {name} ---\n{output}"

    # Save output
    output_dir = RESULTS_DIR / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{audit_id}_orchestrated.txt"
    output_file.write_text(combined_output)

    # Score
    score = score_audit_result(
        audit_id, combined_output, audit_config, finding_details,
        use_llm_judge=use_llm_judge,
    )

    print(f"\n{'='*70}")
    print(f"ORCHESTRATED RESULTS — {audit_id}")
    print(f"{'='*70}")
    print(f"Detected: {score['detected']}/{score['total_vulns']} (recall: {score['recall']:.1%})")
    print(f"Award:    ${score['detected_award']:.2f} / ${score['total_award']:.2f}")
    print(f"Time:     {result['total_elapsed']:.0f}s")
    print(f"{'='*70}")

    for v in score["vulns"]:
        status = "✓" if v["detected"] else "✗"
        conf = v.get("confidence", 0)
        print(f"  {status} {v['vuln_id']}: {v['title'][:55]}.. [conf:{conf:.0%}]")

    # Save results
    summary = {
        "timestamp": datetime.now().isoformat(),
        "label": label or "orchestrated",
        "model": deep_model,
        "deep_model": deep_model,
        "fast_model": fast_model,
        "mode": "orchestrated",
        "audit_id": audit_id,
        "total_elapsed": result["total_elapsed"],
        "total_cost_usd": result.get("total_cost_usd", 0.0),
        "specialist_budget": specialist_budget,
        "merger_budget": merger_budget,
        "token_usage": result.get("token_usage", {}),
        "agents": result["specialist_results"],
        **score,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"orchestrated_{audit_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nResults saved to {path}")

    return summary


# ─── CLI ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Multi-agent audit orchestrator")
    parser.add_argument("source_dir", help="Path to source code directory")
    parser.add_argument("--platform", default="codearena", help="Platform (codearena, sherlock, cantina, etc.)")
    parser.add_argument("--timeout", type=int, default=TIMEOUT_PER_AGENT, help=f"Timeout per agent in seconds (default: {TIMEOUT_PER_AGENT})")
    parser.add_argument("--deep-model", default=DEFAULT_MODEL_DEEP, help="Model for deep semantic agents and merger")
    parser.add_argument("--fast-model", default=DEFAULT_MODEL_FAST, help="Model for fast pattern-matching agents")
    parser.add_argument("--specialist-budget", type=float, default=DEFAULT_SPECIALIST_BUDGET, help="Budget cap per specialist agent")
    parser.add_argument("--merger-budget", type=float, default=DEFAULT_MERGER_BUDGET, help="Budget cap for merger agent")
    parser.add_argument("--merge-timeout", type=int, default=MERGE_TIMEOUT, help="Timeout for the merger stage")
    parser.add_argument("--benchmark", action="store_true", help="Run as EVMBench benchmark")
    parser.add_argument("--audit-id", type=str, help="EVMBench audit ID (for --benchmark)")
    args = parser.parse_args()

    if args.benchmark:
        if not args.audit_id:
            print("Error: --audit-id required with --benchmark")
            sys.exit(1)
        run_benchmark_orchestrated(
            args.audit_id,
            timeout=args.timeout,
            deep_model=args.deep_model,
            fast_model=args.fast_model,
            specialist_budget=args.specialist_budget,
            merger_budget=args.merger_budget,
            merge_timeout=args.merge_timeout,
        )
    else:
        result = orchestrate(
            args.source_dir,
            platform=args.platform,
            timeout_per_agent=args.timeout,
            model_deep=args.deep_model,
            model_fast=args.fast_model,
            specialist_budget=args.specialist_budget,
            merger_budget=args.merger_budget,
            merge_timeout=args.merge_timeout,
        )
        print(f"\nFinal output length: {len(result['final_output'])} chars")


if __name__ == "__main__":
    main()
