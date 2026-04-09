#!/usr/bin/env python3
"""
Multi-agent audit orchestrator (CLI-only).

Coordinates parallel Claude CLI specialists for smart contract analysis.
All agents run via `claude -p` subprocess with native filesystem access
(0 token cost for file reads).

Two architectures:
    classic:      Phase 0 recon → 4 specialists → Haiku triage → Opus verify
    adversarial:  Phase 0 recon → 6 perspectives → Python dedup → Defender/Judge → Forge PoC → report
                  (Sonnet-only, designed to match Opus raw performance through structure)

Usage:
    python3 audit_orchestrator.py <source-dir> [--platform codearena]
    python3 audit_orchestrator.py <source-dir> --architecture adversarial
    python3 audit_orchestrator.py <source-dir> --architecture adversarial --model claude-sonnet-4-6
"""

import argparse
import json
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from claude_cli import ClaudeCliUnavailable, run_claude_prompt

sys.stdout.reconfigure(line_buffering=True)

# ─── CONFIG ────────────────────────────────────────────────────────────────

TIMEOUT_PER_AGENT = 600
MERGE_TIMEOUT = 300
MAX_PARALLEL_AGENTS = 6
REPORTS_DIR = Path("audit-reports")  # written inside source_dir, not repo root

DEFAULT_SPECIALIST_BUDGET = 0.50
DEFAULT_MERGER_BUDGET = 0.80
DEFAULT_MODEL = "claude-opus-4-6"

# Adversarial mode defaults
ADV_SWEEP_MODEL = "claude-sonnet-4-6"
ADV_COURT_MODEL = "claude-sonnet-4-6"
ADV_REPORT_MODEL = "claude-haiku-4-5"
ADV_COURT_BUDGET = 0.40
ADV_POC_BUDGET = 0.30
ADV_COURT_TIMEOUT = 180
ADV_POC_TIMEOUT = 120


# ─── TOKEN TRACKING ───────────────────────────────────────────────────────

class TokenTracker:
    def __init__(self):
        self.calls: list[dict] = []
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
        inp = int(usage.get("input_tokens", 0) or 0)
        out = int(usage.get("output_tokens", 0) or 0)
        cc = int(usage.get("cache_creation_input_tokens", 0) or 0)
        cr = int(usage.get("cache_read_input_tokens", 0) or usage.get("cached_tokens", 0) or 0)

        self.total_input += inp
        self.total_output += out
        self.total_cache_create += cc
        self.total_cache_read += cr
        self.total_cost += cost

        self.calls.append({
            "agent": agent_name, "input_tokens": inp, "output_tokens": out,
            "cache_create": cc, "cache_read": cr, "cost_usd": round(cost, 4),
        })
        print(f"      tokens: {inp:,} in + {out:,} out | cache: {cc:,}w {cr:,}r | ${cost:.4f}")

    def summary(self) -> str:
        lines = [f"\n{'─'*60}", "  TOKEN USAGE SUMMARY", f"{'─'*60}"]
        for c in self.calls:
            lines.append(f"  {c['agent']:30s}  {c['input_tokens']:>8,} in  {c['output_tokens']:>7,} out  ${c['cost_usd']:.4f}")
        lines.append(f"{'─'*60}")
        lines.append(f"  {'TOTAL':30s}  {self.total_input:>8,} in  {self.total_output:>7,} out  ${self.total_cost:.4f}")
        cache_pct = round(self.total_cache_read / max(self.total_input, 1) * 100, 1)
        lines.append(f"  Cache: {self.total_cache_create:,} created, {self.total_cache_read:,} read ({cache_pct}% hit)")
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


token_tracker = TokenTracker()


# ─── PHASE 0: RECON (pure Python, 0 LLM tokens) ──────────────────────────

_EXTERNAL_KEYWORDS = {
    "aave": ["Aave"], "compound": ["Compound"], "morpho": ["Morpho"],
    "balancer": ["Balancer"], "aura": ["Aura"], "curve": ["Curve"],
    "convex": ["Convex"], "pendle": ["Pendle"], "lido": ["Lido"],
    "uniswap": ["Uniswap"], "sushiswap": ["SushiSwap"], "yearn": ["Yearn"],
    "maker": ["Maker"], "dolomite": ["Dolomite"], "gmx": ["GMX"],
    "chainlink": ["Chainlink"], "synthetix": ["Synthetix"],
}

_GROUP_PATTERNS = {
    "connectors": ["connector"], "strategies": ["strategy"],
    "adapters": ["adapter"], "vaults": ["vault"], "oracles": ["oracle"],
    "handlers": ["handler"],
    "core": ["manager", "registry", "factory", "router", "controller", "governance"],
}

_SKIP_DIRS = {"test", "tests", "lib", "node_modules", "out", "cache", "mock", "mocks", "script", "scripts"}


def _classify_file(name_lower: str) -> str:
    for group, keywords in _GROUP_PATTERNS.items():
        if any(kw in name_lower for kw in keywords):
            return group
    return "other"


def _detect_externals(source_text: str) -> list[str]:
    source_lower = source_text.lower()
    found = []
    for keyword, names in _EXTERNAL_KEYWORDS.items():
        if keyword in source_lower:
            found.extend(names)
    return sorted(set(found))


def run_recon(source_dir: str, platform: str, quiet_roi: bool = False) -> dict:
    """Phase 0: pure Python codebase mapping. Zero LLM tokens.

    Args:
        quiet_roi: suppress ROI warnings (e.g. during benchmarks).
    """
    print("\n[Phase 0] Recon (Python)...")
    start = time.time()

    root = Path(source_dir)
    in_scope = []
    for f in sorted(root.rglob("*.sol")):
        if not f.is_file():
            continue
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
        files_info.append({"path": rel, "name": name, "loc": loc, "group": group})
        pattern_groups.setdefault(group, []).append(rel)

        externals = _detect_externals(text)
        if externals:
            external_protocols[name] = externals

    pattern_groups.pop("other", None)
    elapsed = time.time() - start

    total_loc = sum(f["loc"] for f in files_info)
    recon = {
        "total_files": len(in_scope),
        "total_loc": total_loc,
        "files": files_info,
        "pattern_groups": pattern_groups,
        "external_protocols": external_protocols,
    }

    roi = compute_roi(recon)
    recon["roi_score"] = roi["score"]
    recon["roi_details"] = roi["details"]

    print(f"    {len(in_scope)} files, {total_loc:,} LOC, {len(pattern_groups)} pattern groups ({elapsed:.1f}s)")
    if not quiet_roi:
        print(f"    ROI score: {roi['score']}/100 — {roi['verdict']}")
        if roi["score"] < 30:
            print(f"    ⚠ LOW ROI TARGET — consider skipping. Details: {roi['details']}")

    return recon


# ─── ROI SCORING ─────────────────────────────────────────────────────────

def compute_roi(recon: dict) -> dict:
    """Estimate ROI from recon metadata. Returns {score, details, verdict}."""
    total_files = recon.get("total_files", 0)
    total_loc = recon.get("total_loc", 0)
    n_groups = len(recon.get("pattern_groups", {}))
    n_externals = sum(len(v) for v in recon.get("external_protocols", {}).values())

    score = 0
    details = []

    # File count scoring
    if total_files <= 20:
        score += 30
        details.append(f"files={total_files} (+30)")
    elif total_files <= 50:
        score += 20
        details.append(f"files={total_files} (+20)")
    elif total_files <= 100:
        score += 10
        details.append(f"files={total_files} (+10)")
    else:
        details.append(f"files={total_files} (+0, very large)")

    # LOC scoring
    if total_loc <= 5000:
        score += 20
        details.append(f"LOC={total_loc:,} (+20)")
    elif total_loc <= 15000:
        score += 15
        details.append(f"LOC={total_loc:,} (+15)")
    else:
        details.append(f"LOC={total_loc:,} (+0, very large)")

    # Pattern groups (cross-compare opportunity)
    if n_groups >= 2:
        score += 15
        details.append(f"groups={n_groups} (+15)")
    else:
        score += 5
        details.append(f"groups={n_groups} (+5)")

    # External protocols (integration bug opportunity)
    if 1 <= n_externals <= 3:
        score += 15
        details.append(f"externals={n_externals} (+15)")
    elif n_externals > 5:
        score += 10
        details.append(f"externals={n_externals} (+10, complex)")
    else:
        score += 5
        details.append(f"externals={n_externals} (+5)")

    # Solidity baseline
    score += 20
    details.append("solidity (+20)")

    if score >= 70:
        verdict = "HIGH ROI — good target"
    elif score >= 45:
        verdict = "MEDIUM ROI — proceed with cost awareness"
    else:
        verdict = "LOW ROI — consider skipping or setting tight cost cap"

    return {"score": score, "details": ", ".join(details), "verdict": verdict}


# ─── PHASE 1: SPECIALIST AGENTS ──────────────────────────────────────────

_OUTPUT_FMT = """Report each finding as:
FINDING: [title]
FILE: [exact path:line]
ROOT_CAUSE: [2-3 sentences]
IMPACT: [specific impact]
SEVERITY: High|Medium
FIX: [1-3 line fix]

If no bugs in a file: CLEAN: [filename] — [what you verified]
Only report real exploitable bugs."""


_LLM_WARNINGS = """
VERIFICATION RULES (mandatory before reporting any finding):
- Read ALL modifiers and parent contracts before claiming "missing check". Inherited guards are invisible at first glance.
- High severity requires concrete attack: exact steps, preconditions satisfied, quantified profit. No "could potentially".
- "transfer() → reentrancy" requires showing: no nonReentrant, specific dirty state variable, profitable callback path.
- Confirm every file:line is from the correct contract. Do not confuse similarly-named functions across different contracts.
- Check preconditions are satisfiable: caller has the right role, pool has liquidity, oracle returns expected value.
- If funcA has check but funcB doesn't, find the TECHNICAL REASON before reporting. It may be intentional."""


def _build_base_context(recon: dict) -> str:
    """Shared codebase context for all specialist prompts."""
    files = recon.get("files", [])
    external = recon.get("external_protocols", {})
    total_files = recon.get("total_files", 0)

    by_group: dict[str, list[str]] = {}
    for f in files:
        by_group.setdefault(f["group"], []).append(f["path"])
    file_map = "\n".join(f"  [{g}] {', '.join(ps)}" for g, ps in sorted(by_group.items()))

    ext_hint = ""
    if external:
        ext_hint = "External: " + ", ".join(f"{k}({','.join(v)})" for k, v in external.items())

    vuln_ref = ""
    vuln_path = Path(__file__).parent / "prompts" / "vuln_examples.md"
    if vuln_path.exists():
        vuln_ref = f"\nREFERENCE: Read {vuln_path} for real CVE diffs and vulnerable/fixed pairs. Match your findings against these known patterns.\n"

    return f"""CODEBASE: {total_files} .sol files. Read every file with Read tool.
{file_map}
{ext_hint}
{vuln_ref}
{_OUTPUT_FMT}"""


def build_specialist_prompts(recon: dict, platform: str) -> list[dict]:
    """Build classic specialist prompts (4 agents)."""
    base = _build_base_context(recon)

    return [
        {
            "name": "sweep",
            "focus": "Full security analysis",
            "prompt": f"You are an expert smart contract auditor. Analyze ALL files.\n\n{base}\n\nCheck: reentrancy, access control, arithmetic, oracle, flash loan, logic errors.\n{_LLM_WARNINGS}",
        },
        {
            "name": "value_math",
            "focus": "Value calculations, math, precision",
            "prompt": f"You are a smart contract auditor specializing in VALUE and MATH bugs.\n\n{base}\n\nFOCUS: trace every value calc (balanceOf, totalSupply, totalAssets, convertToShares, getPrice). Division before multiplication. Decimal mismatches. Rounding direction. ERC4626 edge cases. deposit(0)/withdraw(0)/self-transfer.\n{_LLM_WARNINGS}",
        },
        {
            "name": "access_reentry",
            "focus": "Access control, reentrancy",
            "prompt": f"You are a smart contract auditor specializing in ACCESS CONTROL and REENTRANCY.\n\n{base}\n\nFOCUS: map every token transfer and its guard. Compare similar functions for missing auth. State updates after external calls. Signature replay, ecrecover(0). Unprotected initialize(). Fee-on-transfer.\n{_LLM_WARNINGS}",
        },
        {
            "name": "cross_contract",
            "focus": "Cross-contract, integration",
            "prompt": f"You are a smart contract auditor specializing in CROSS-CONTRACT bugs.\n\n{base}\n\nFOCUS: trace value flows end-to-end (deposit→...→withdraw). Find asymmetry (forward≠reverse). External protocol calls: correct variant? Return value? Oracle: spot vs TWAP? Stale? Flash loan amplification.\n{_LLM_WARNINGS}",
        },
    ]


def build_adversarial_prompts(recon: dict, platform: str) -> list[dict]:
    """Build adversarial sweep prompts (4 attacker perspectives).

    4 agents, each covering 2 merged attack vectors.
    Designed to cost < 1 Opus equivalent while maximizing recall via diversity.
    """
    base = _build_base_context(recon)

    return [
        {
            "name": "state_flow",
            "focus": "Reentrancy + cross-contract flow asymmetry",
            "prompt": f"""You are an attacker. Hunt REENTRANCY and CROSS-CONTRACT FLOW bugs.

{base}

ATTACK CHECKLIST:
- Every external call: state updated BEFORE or AFTER? Read-only reentrancy via view functions?
- Cross-function reentrancy: A calls external, B reads dirty state during callback
- ERC777/ERC1155 hooks as reentry vectors
- Trace deposit→...→withdraw end-to-end. Forward path == reverse path?
- If multiple contracts implement same interface: compare ALL. Bug = the one that differs.
- Multi-hop: A→B→C. What if B reverts? Partial execution handled?
- Return values of external calls: checked or silently ignored?

TRACE-BACK PROCESS:
1. List every external call (call, transfer, safeTransfer, external function)
2. For each: what state is updated BEFORE vs AFTER the call?
3. Check: is there nonReentrant? Does it cover ALL entry points?
4. Check cross-function: can callback enter a DIFFERENT function that reads dirty state?
5. Check read-only reentrancy: can a view function return wrong values during callback?
6. Trace deposit→withdraw end-to-end: forward path == reverse path?
{_LLM_WARNINGS}""",
        },
        {
            "name": "value_oracle",
            "focus": "Value math + oracle/price manipulation",
            "prompt": f"""You are an attacker. Hunt VALUE MATH and ORACLE MANIPULATION bugs.

{base}

ATTACK CHECKLIST:
- Trace every value function: balanceOf, totalSupply, totalAssets, convertToShares, getPrice
- What's ADDED vs SUBTRACTED? Missing component = free money
- Division before multiplication → precision loss. Who profits from rounding?
- First depositor: inflate share price with dust?
- Fee-on-transfer tokens: amount_in assumed == amount_received?
- Oracle: spot price (manipulable) vs TWAP? Staleness check? price==0 handled?
- Chainlink on L2: sequencer uptime checked?
- LP token pricing: raw reserves (flashloan-manipulable) or fair pricing?
- Can attacker sandwich a price update?

TRACE-BACK PROCESS:
1. List every function returning balance/shares/TVL/price
2. For each: what is added, subtracted? Is debt handled correctly?
3. Compare deposit path math vs withdraw path math — are they inverse?
4. Check rounding direction: does it ALWAYS favor the protocol?
5. Identify every price source → spot (manipulable) or TWAP?
6. Check staleness, decimals (8 vs 18), L2 sequencer uptime
{_LLM_WARNINGS}""",
        },
        {
            "name": "access_edge",
            "focus": "Access control + edge case griefing",
            "prompt": f"""You are an attacker. Hunt ACCESS CONTROL and EDGE CASE bugs.

{base}

ATTACK CHECKLIST:
- Map every auth pattern (onlyOwner, require(msg.sender==X), modifiers)
- Compare similar functions: funcA has auth, funcB doesn't — why?
- Unprotected initialize() — front-runnable?
- ecrecover: returns address(0) on bad sig — checked?
- Delegatecall with attacker-controlled target?
- deposit(0), withdraw(0), self-transfer — break invariants?
- Empty pool, zero shares — division by zero?
- type(uint256).max — overflow in unchecked blocks?
- Dust amounts blocking withdrawals or corrupting accounting?
- DoS: can attacker make a function permanently revert?

TRACE-BACK PROCESS:
1. List every external/public state-changing function
2. For each: what modifier? what require? what inherited check?
3. Compare pairs: if funcA has onlyOwner and funcB doesn't, find WHY
4. Check initialize(): has initializer modifier? Can be called twice?
5. Check signature functions: does ecrecover result get checked against address(0)?
6. Test edge cases: deposit(0), withdraw(max), self-transfer, empty pool
{_LLM_WARNINGS}""",
        },
        {
            "name": "economic",
            "focus": "Flash loans, MEV, economic exploits",
            "prompt": f"""You are an attacker. Hunt ECONOMIC EXPLOIT and FLASH LOAN bugs.

{base}

ATTACK CHECKLIST:
- Every balance/reserve read: can flash loan inflate it in same tx?
- Liquidation: self-liquidate profitably? Bad debt conditions?
- Reward distribution: can attacker claim disproportionate rewards?
- Voting/governance: flash loan to pass proposal in one block?
- Sandwich attacks: any state-changing tx profitable to front/back-run?
- Fee bypass: any path that avoids intended fees?
- Donation attacks: direct transfer to contract to manipulate accounting?
- Cross-protocol composability: combine with other DeFi to amplify?

TRACE-BACK PROCESS:
1. Identify every balanceOf/totalSupply/reserve read in value-critical functions
2. For each: can a flash loan inflate it in the same tx?
3. Map fee collection points: is there any code path that bypasses fees?
4. Check reward distribution: can attacker stake→claim→unstake in one tx?
5. Check liquidation: can attacker self-liquidate profitably?
6. Check donation: does direct transfer to contract affect accounting?
{_LLM_WARNINGS}""",
        },
    ]


def _run_specialist(spec: dict, source_dir: str, timeout: int, model: str, budget: float) -> dict:
    """Run a single specialist via `claude -p`."""
    name = spec["name"]
    print(f"    [{name}] Starting [{model}]")
    start = time.time()

    try:
        result = run_claude_prompt(
            spec["prompt"],
            cwd=source_dir,
            timeout=timeout,
            output_format="json",
            model=model,
            max_budget=budget,
            add_dir=source_dir,
            permission_mode="bypassPermissions",
            auto_accept_permissions=True,
        )
        raw = result.get("raw", "")
        parsed = result.get("parsed", {})
        text = (parsed.get("result", "") if isinstance(parsed, dict) else raw) or raw
        usage = result.get("usage", {}) or (parsed.get("usage", {}) if isinstance(parsed, dict) else {})
        cost = float(result.get("total_cost_usd", 0.0) or (parsed.get("total_cost_usd", 0.0) if isinstance(parsed, dict) else 0.0))
        token_tracker.record(name, usage, cost)
    except Exception as e:
        print(f"    [{name}] ERROR: {e}")
        text, usage, cost = f"[ERROR: {e}]", {}, 0.0

    elapsed = time.time() - start
    finding_count = len(re.findall(r"(?:^|\n)(?:FINDING:|## \[H|## \[M)", text))
    print(f"    [{name}] {elapsed:.0f}s — {finding_count} findings | ${cost:.4f}")

    return {
        "name": name, "focus": spec.get("focus", name),
        "output": text, "elapsed": round(elapsed, 1),
        "finding_count": finding_count, "usage": usage, "cost": cost,
    }


def run_specialists(
    source_dir: str, recon: dict, platform: str,
    timeout: int, model: str, budget: float,
) -> list[dict]:
    """Phase 1: Run specialists. First one warms cache, rest run in parallel."""
    specs = build_specialist_prompts(recon, platform)
    print(f"\n[Phase 1] {len(specs)} specialists...")
    results = []

    # Cache-warm: first specialist alone
    first, rest = specs[0], specs[1:]
    print(f"    [cache-warm] {first['name']}...")
    results.append(_run_specialist(first, source_dir, timeout, model, budget))

    # Fork: remaining in parallel
    if rest:
        print(f"    [fork] {len(rest)} parallel...")
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_AGENTS) as executor:
            futures = {
                executor.submit(_run_specialist, s, source_dir, timeout, model, budget): s
                for s in rest
            }
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as e:
                    s = futures[future]
                    print(f"    [{s['name']}] FAILED: {e}")
                    results.append({
                        "name": s["name"], "focus": s.get("focus", ""),
                        "output": f"[ERROR: {e}]", "elapsed": 0,
                        "finding_count": 0, "usage": {}, "cost": 0,
                    })

    total_findings = sum(r["finding_count"] for r in results)
    total_cost = sum(r.get("cost", 0) for r in results)
    print(f"\n    Total: {total_findings} findings | ${total_cost:.4f}")
    return results


# ─── PHASE 2: MERGE ──────────────────────────────────────────────────────

def _extract_findings(output: str) -> str:
    """Extract FINDING: blocks from specialist output. Strips reasoning noise."""
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
            if line.strip() == "" and current and current[-1].strip() == "":
                findings.append("\n".join(current))
                current = []
                in_finding = False
            else:
                current.append(line)
    if current:
        findings.append("\n".join(current))
    return "\n\n".join(findings)


def _run_claude(prompt: str, source_dir: str, timeout: int,
                model: str, budget: float, agent_name: str) -> str:
    """Run a single Claude CLI call, track tokens, return text."""
    try:
        result = run_claude_prompt(
            prompt, cwd=source_dir, timeout=timeout,
            output_format="json", model=model, max_budget=budget,
            add_dir=source_dir, permission_mode="bypassPermissions",
            auto_accept_permissions=True,
        )
        raw = result.get("raw", "")
        parsed = result.get("parsed", {})
        text = (parsed.get("result", "") if isinstance(parsed, dict) else raw) or raw
        usage = result.get("usage", {}) or (parsed.get("usage", {}) if isinstance(parsed, dict) else {})
        cost = float(result.get("total_cost_usd", 0.0) or (parsed.get("total_cost_usd", 0.0) if isinstance(parsed, dict) else 0.0))
        token_tracker.record(agent_name, usage, cost)
        return text
    except Exception as e:
        return f"[ERROR: {e}]"


def _compress_findings(specialist_results: list[dict]) -> str:
    """Compress specialist outputs: extract findings only, strip reasoning."""
    combined = ""
    for r in specialist_results:
        extracted = _extract_findings(r["output"])
        if extracted.strip():
            combined += f"\n--- {r['name']} ({r['focus']}) ---\n{extracted}\n"
    return combined


# ─── PHASE 1.5: PYTHON DEDUP (free) ────────────────────────────────────

def _dedup_findings(combined: str) -> tuple[str, int, int]:
    """Deduplicate findings by file:line. Pure Python, zero LLM tokens.

    Returns (deduped_text, unique_count, original_count).
    """
    findings: list[dict] = []
    current_lines: list[str] = []
    current_file = ""

    for line in combined.splitlines():
        if line.startswith("FINDING:"):
            if current_lines:
                findings.append({"file": current_file, "text": "\n".join(current_lines)})
            current_lines = [line]
            current_file = ""
        elif line.startswith("FILE:") and not current_file:
            current_file = line.split(":", 1)[1].strip().split(":")[0].strip()  # extract path
            current_lines.append(line)
        elif line.startswith("CLEAN:"):
            if current_lines:
                findings.append({"file": current_file, "text": "\n".join(current_lines)})
            current_lines = []
            current_file = ""
        elif current_lines:
            current_lines.append(line)

    if current_lines:
        findings.append({"file": current_file, "text": "\n".join(current_lines)})

    original_count = len(findings)

    # Dedup by file + first line of FINDING (title)
    seen: dict[str, dict] = {}
    for f in findings:
        title_line = ""
        for l in f["text"].splitlines():
            if l.startswith("FINDING:"):
                title_line = l.strip().lower()
                break
        key = f"{f['file']}|{title_line}"
        if key not in seen:
            seen[key] = f
        else:
            # Keep the longer (more detailed) version
            if len(f["text"]) > len(seen[key]["text"]):
                seen[key] = f

    unique = list(seen.values())
    deduped_text = "\n\n".join(f["text"] for f in unique)
    return deduped_text, len(unique), original_count


# ─── ADVERSARIAL COURT (Phase 2 for adversarial mode) ───────────────────

def _parse_court_verdicts(judge_output: str) -> tuple[list[str], list[str]]:
    """Parse judge output into kept and dropped findings."""
    kept, dropped = [], []
    for line in judge_output.splitlines():
        line_s = line.strip()
        if line_s.startswith("KEEP") or line_s.startswith("[KEEP]"):
            kept.append(line_s)
        elif line_s.startswith("DROP") or line_s.startswith("[DROP]"):
            dropped.append(line_s)
        elif line_s.startswith("ESCALATE") or line_s.startswith("[ESCALATE]"):
            kept.append(line_s)  # escalate = keep for forge gate
    return kept, dropped


def run_adversarial_court(
    source_dir: str, specialist_results: list[dict],
    model: str, budget: float, timeout: int,
    judge_model: str = "claude-haiku-4-5",
) -> tuple[str, int, int]:
    """Phase 2 (adversarial): Dedup → Defender(Sonnet) → Judge(Haiku).

    Prosecutor eliminated — sweep agents already argue exploitability.
    Defender(Sonnet) reads code, tries to disprove. Judge(Haiku) rules on argument quality.
    Total: 1 Sonnet + 1 Haiku (not 3 Sonnet).

    Returns (judge_output, kept_count, total_count).
    """
    print("\n[Phase 1.5] Python dedup (free)...")

    combined = _compress_findings(specialist_results)
    if not combined.strip():
        print("    No findings — skipping court")
        return "(no findings)", 0, 0

    deduped, unique_count, raw_count = _dedup_findings(combined)
    print(f"    {raw_count} raw → {unique_count} unique (deduped {raw_count - unique_count})")

    if unique_count == 0:
        return "(no findings after dedup)", 0, 0

    # ── Defender: try to disprove each finding ──
    # Sweep agents already act as prosecutors (they argue exploitability).
    # We only need one Defender to challenge them.
    print(f"\n[Phase 2] Adversarial Court...")
    print(f"    [Defender] Challenging {unique_count} findings [{model}]...")

    # Collect referenced files so Defender knows which files to read
    referenced_files = set()
    for line in deduped.splitlines():
        if line.startswith("FILE:"):
            path_part = line.split(":", 1)[1].strip().split(":")[0].strip()
            if path_part:
                referenced_files.add(path_part)
    file_hint = "\n".join(f"  - {f}" for f in sorted(referenced_files))

    defender_prompt = f"""You are a smart contract DEVELOPER defending your code.
Specialist auditors found {unique_count} potential vulnerabilities. Your job: prove they are WRONG.

SOURCE CODE: {source_dir}
Read ONLY the files referenced in findings (listed below). Do NOT re-audit the whole codebase.

FILES TO CHECK:
{file_hint}

For EACH finding:
1. READ the code at the specified location AND surrounding context (±30 lines)
2. Find GUARDS: access controls, require(), modifiers, invariants that prevent the attack
3. Check DESIGN INTENT: is this behavior intentional? Look at comments, function naming
4. Challenge PREREQUISITES: are the attack conditions realistic?
5. If you CANNOT find a valid defense → CONCEDE: [title]

Output format:
DEFEND: [finding title]
GUARD: [specific code preventing attack, file:line]
REASON: [1-2 sentences why this is safe]

OR:
CONCEDE: [finding title]
REASON: [why you cannot defend this]

FINDINGS TO CHALLENGE:
{deduped}"""

    defender_out = _run_claude(
        defender_prompt, source_dir, timeout=timeout,
        model=model, budget=budget, agent_name="defender",
    )

    defended = len(re.findall(r"(?:^|\n)DEFEND:", defender_out))
    conceded = len(re.findall(r"(?:^|\n)CONCEDE:", defender_out))
    print(f"    Defender: {defended} defended, {conceded} conceded")

    # ── Judge (Haiku): rule based on defense quality — no code reading ──
    print(f"    [Judge] Rendering verdicts [{judge_model}]...")
    judge_prompt = f"""You are the JUDGE in a smart contract security court.
Auditors found vulnerabilities. A Defender tried to disprove each one.

DO NOT read source files. Judge purely on argument quality.

RULES:
- Defender CONCEDED → KEEP (confirmed vulnerability)
- Defender cited a specific guard with file:line → DROP
- Defender's argument is vague ("probably safe") with no code reference → KEEP
- Defender's guard exists but doesn't fully block the attack → ESCALATE

Output (one per finding):
[KEEP|DROP|ESCALATE] [SEVERITY:High|Medium] [finding title]
  Reason: [1-2 sentence verdict]

ORIGINAL FINDINGS:
{deduped}

DEFENSE:
{defender_out}"""

    judge_out = _run_claude(
        judge_prompt, source_dir, timeout=min(timeout, 90),
        model=judge_model, budget=0.05, agent_name="judge_haiku",
    )

    kept, dropped = _parse_court_verdicts(judge_out)
    print(f"    Judge: {len(kept)} kept, {len(dropped)} dropped (from {unique_count} unique)")

    return judge_out, len(kept), unique_count


# ─── FORGE PoC GATE (Phase 3 for adversarial mode) ──────────────────────

def _has_forge(source_dir: str) -> bool:
    """Check if source dir has a Foundry project."""
    return (Path(source_dir) / "foundry.toml").exists()


def _run_forge_test(source_dir: str, test_name: str) -> tuple[bool, str]:
    """Run a specific forge test, return (passed, output)."""
    try:
        result = subprocess.run(
            ["forge", "test", "--match-test", test_name, "-vv"],
            cwd=source_dir, capture_output=True, text=True, timeout=120,
        )
        output = result.stdout + result.stderr
        return result.returncode == 0, output
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return False, str(e)


def run_forge_gate(
    source_dir: str, judge_output: str,
    model: str, budget: float, timeout: int,
) -> str:
    """Phase 3 (adversarial): Forge PoC for ESCALATE-only findings.

    KEEP findings are already confirmed (Defender conceded or had weak defense).
    Only ESCALATE findings need mechanical proof — both sides were convincing.

    Returns updated verdict with PoC results.
    """
    if not _has_forge(source_dir):
        print("    [Forge Gate] No foundry.toml — skipping PoC validation")
        return judge_output

    print("\n[Phase 3] Forge PoC Gate (ESCALATE only)...")

    # Only ESCALATE findings need PoC — KEEP is already confirmed
    findings_for_poc = []
    for line in judge_output.splitlines():
        line_s = line.strip()
        if any(line_s.startswith(tag) for tag in ["ESCALATE", "[ESCALATE]"]):
            findings_for_poc.append(line_s)

    if not findings_for_poc:
        print("    No findings to PoC")
        return judge_output

    findings_text = "\n".join(findings_for_poc)
    print(f"    {len(findings_for_poc)} findings → PoC attempt")

    poc_prompt = f"""You are a smart contract exploit developer. Write Foundry PoC tests.

SOURCE CODE: {source_dir}

For each finding below, write a minimal Foundry test that demonstrates the vulnerability.
Each test should:
1. Set up the minimal required state
2. Execute the attack
3. Assert the impact (stolen funds, broken invariant, etc.)

Write ALL tests in a single file. Save it to: {source_dir}/test/AdversarialPoC.t.sol

If a finding cannot be demonstrated with a test (e.g., needs mainnet state), mark it:
SKIP_POC: [finding title] — [reason]

After writing the file, run: forge test --match-contract AdversarialPoC -vv
Report results as:
POC_PASS: [finding title] — test passed, vulnerability confirmed
POC_FAIL: [finding title] — test failed, reason: [why]

FINDINGS TO POC:
{findings_text}

Read the relevant source code before writing tests."""

    poc_out = _run_claude(
        poc_prompt, source_dir, timeout=timeout,
        model=model, budget=budget, agent_name="forge_poc",
    )

    passed = len(re.findall(r"(?:^|\n)POC_PASS:", poc_out))
    failed = len(re.findall(r"(?:^|\n)POC_FAIL:", poc_out))
    skipped = len(re.findall(r"(?:^|\n)SKIP_POC:", poc_out))
    print(f"    PoC results: {passed} passed, {failed} failed, {skipped} skipped")

    return f"{judge_output}\n\n--- FORGE PoC RESULTS ---\n{poc_out}"


# ─── ADVERSARIAL REPORT (Phase 4) ──────────────────────────────────────

def run_adversarial_report(
    source_dir: str, court_output: str,
    model: str, budget: float, timeout: int,
) -> str:
    """Phase 4 (adversarial): Format confirmed findings into final report."""
    print(f"\n[Phase 4] Report generation [{model}]...")

    report_prompt = f"""Format the following court verdicts and PoC results into a clean audit report.
Only include KEEP and POC_PASS findings. Drop everything else.

Write the report to: {source_dir}/audit-reports/summary.md

Format:
# Audit Report (Adversarial Cascade)
## Findings Summary
| ID | Severity | Title | PoC |
## [H-01] Title
**Lines of code:** `file.sol:XX-YY`
**Verdict:** [court reasoning]
**Root cause:** ...
**Impact:** ...
**PoC:** [pass/skip]
**Recommended fix:** ...

COURT + POC RESULTS:
{court_output}"""

    report_out = _run_claude(
        report_prompt, source_dir, timeout=min(timeout, 120),
        model=model, budget=0.10, agent_name="report",
    )

    report_path = Path(source_dir) / "audit-reports" / "summary.md"
    report = report_path.read_text() if report_path.exists() else ""
    return report_out + "\n\n" + report


# ─── CLASSIC MERGER (Phase 2 for classic mode) ─────────────────────────

def run_merger(
    source_dir: str, specialist_results: list[dict],
    platform: str, model: str, budget: float, timeout: int,
    triage_model: str = "claude-haiku-4-5",
) -> str:
    """Phase 2: Two-stage merge (3-Tier Routing).

    Stage A: Haiku triage — cheap dedup + false positive filter (no code reading)
    Stage B: Deep verify — read actual code, confirm exploitability, write report
    """
    print("\n[Phase 2] Merging (2-stage)...")

    combined = _compress_findings(specialist_results)
    if not combined.strip():
        print("    No findings — skipping merge")
        return "(no findings)"

    raw_size = sum(len(r["output"]) for r in specialist_results)
    total_findings = sum(r["finding_count"] for r in specialist_results)
    print(f"    {total_findings} findings ({len(combined):,} chars, compressed from {raw_size:,})")

    # ── Stage A: Haiku triage (cheap, no code reading) ──
    print("    [Stage A] Haiku triage...")
    triage_prompt = f"""You are a smart contract security triage analyst. Review these findings from {len(specialist_results)} specialist agents and:

1. DEDUPLICATE: group findings with the same root cause (same file + same bug = merge)
2. FILTER: mark each as KEEP or DROP with brief reason
   - DROP: obviously wrong, out of scope, gas optimization, style issue, or intentional design
   - KEEP: plausible security impact (fund loss, access bypass, state corruption)
3. Output a numbered list of KEPT findings only, each as one line:
   [N] SEVERITY | FILE:line | title | root_cause_summary

Do NOT read any files. Judge based solely on the finding descriptions below.

FINDINGS:
{combined}"""

    triage_result = _run_claude(
        triage_prompt, source_dir, timeout=min(timeout, 120),
        model=triage_model, budget=0.10, agent_name="triage_haiku",
    )

    kept_count = len(re.findall(r"^\s*\[\d+\]", triage_result, re.MULTILINE))
    print(f"    Haiku kept {kept_count}/{total_findings} findings")

    if kept_count == 0 and total_findings > 0:
        # Haiku was too aggressive — fall back to full findings
        print("    Haiku dropped everything — falling back to full merge")
        triage_result = combined

    # ── Stage B: Deep verification (Opus/Sonnet, reads code) ──
    print(f"    [Stage B] Deep verify [{model}]...")
    verify_prompt = f"""You are the lead auditor. Below are pre-triaged findings.

SOURCE: {source_dir}

For each finding:
1. READ the actual code at the specified file:line
2. Verify it's actually exploitable (not just theoretical)
3. Assign severity: High (direct fund loss, permanent freeze) or Medium (conditional, temporary)
4. Drop confirmed false positives

Write the final report to audit-reports/summary.md.

PRE-TRIAGED FINDINGS:
{triage_result}

Format:
# [Protocol] - Audit Report
## Findings Summary
| ID | Severity | Title |
## [H-01] Title
**Lines of code:** `file.sol:XX-YY`
**Root cause:** ...
**Impact:** ...
**Recommended fix:** ..."""

    final = _run_claude(
        verify_prompt, source_dir, timeout=timeout,
        model=model, budget=budget, agent_name="merger_deep",
    )
    print(f"    Merge done")

    report_path = Path(source_dir) / "audit-reports" / "summary.md"
    report = report_path.read_text() if report_path.exists() else ""
    return final + "\n\n" + report


# ─── CACHE MULTI-PASS ─────────────────────────────────────────────────────

MULTIPASS_LENSES = [
    {
        "name": "value_flow",
        "suffix": (
            "FOCUS: Trace every value calculation (TVL, shares, balance, price, exchange rate). "
            "Check: division before multiplication, decimal mismatches, rounding direction, "
            "ERC4626 edge cases, deposit(0)/withdraw(0), self-transfer. "
            "Trace what's ADDED vs SUBTRACTED in every accounting function."
        ),
    },
    {
        "name": "access_control",
        "suffix": (
            "FOCUS: Map every state-changing function and its access guard. "
            "Check: missing auth, wrong modifier, signature replay, ecrecover(0), "
            "unprotected initialize(), fee-on-transfer tokens, flash loan vectors. "
            "Compare similar functions for inconsistent protection."
        ),
    },
    {
        "name": "cross_contract",
        "suffix": (
            "FOCUS: Trace value flows end-to-end (deposit→...→withdraw). "
            "Check: forward path != reverse path (asymmetry), external protocol calls "
            "(correct variant? return value units?), oracle manipulation (spot vs TWAP, staleness), "
            "cross-function reentrancy, state updates after external calls. "
            "If multiple contracts share same interface, compare ALL implementations."
        ),
    },
]


def run_cache_multipass(
    source_dir: str,
    platform: str = "codearena",
    model: str = DEFAULT_MODEL,
    budget_per_pass: float = 0.50,
    timeout: int = TIMEOUT_PER_AGENT,
    lenses: list[dict] | None = None,
) -> dict:
    """Sequential multi-pass with identical prompt prefix for cache reuse.

    Each pass uses the same system context (codebase) but a different analysis
    lens as suffix. The first pass creates cache; subsequent passes should hit
    it, reducing input token cost by ~50%.

    Returns combined findings after Python dedup.
    """
    tracker = TokenTracker()
    recon = run_recon(source_dir, platform)
    base = _build_base_context(recon)
    lenses = lenses or MULTIPASS_LENSES

    all_outputs: list[str] = []
    prefix = (
        f"You are an expert smart contract auditor. Analyze ALL files.\n\n"
        f"{base}\n\n"
    )

    for i, lens in enumerate(lenses):
        print(f"\n[Cache Multi-Pass] Pass {i+1}/{len(lenses)}: {lens['name']}")
        prompt = prefix + lens["suffix"]

        try:
            result = run_claude_prompt(
                prompt,
                cwd=source_dir,
                timeout=timeout,
                output_format="json",
                model=model,
                max_budget=budget_per_pass,
                add_dir=source_dir,
                permission_mode="bypassPermissions",
                auto_accept_permissions=True,
            )
        except ClaudeCliUnavailable as exc:
            print(f"    FAILED: {exc}")
            continue

        text = result.get("parsed", {}).get("result", "") or result.get("raw", "")
        cost = float(result.get("total_cost_usd", 0.0) or 0.0)
        usage = result.get("usage", {})
        tracker.record(f"pass_{lens['name']}", usage, cost)

        all_outputs.append(f"=== PASS: {lens['name']} ===\n{text}")

    combined = "\n\n".join(all_outputs)
    deduped, unique, original = _dedup_findings(combined)

    print(f"\n  Findings: {original} raw → {unique} unique after dedup")
    print(tracker.summary())

    return {
        "report_text": deduped,
        "raw_combined": combined,
        "findings_raw": original,
        "findings_unique": unique,
        "token_usage": tracker.to_dict(),
        "total_cost_usd": tracker.total_cost,
        "recon": recon,
    }


# ─── MAIN ORCHESTRATOR ────────────────────────────────────────────────────

def orchestrate(
    source_dir: str,
    platform: str = "codearena",
    timeout_per_agent: int = TIMEOUT_PER_AGENT,
    model: str = DEFAULT_MODEL,
    triage_model: str = "claude-haiku-4-5",
    specialist_budget: float = DEFAULT_SPECIALIST_BUDGET,
    merger_budget: float = DEFAULT_MERGER_BUDGET,
    merge_timeout: int = MERGE_TIMEOUT,
    architecture: str = "classic",
    # Legacy compat kwargs (ignored)
    **kwargs,
) -> dict:
    """Run full multi-agent audit orchestration.

    Architectures:
        classic:        Tier 0 recon → 4 specialists → Haiku triage → Opus verify
        adversarial:    Tier 0 recon → 6 perspectives → Prosecutor/Defender/Judge → Forge PoC → Haiku report
        cache-multipass: Tier 0 recon → N sequential passes (same prefix, different lens) → Python dedup
    """
    total_start = time.time()
    token_tracker.reset()

    if architecture == "cache-multipass":
        result = run_cache_multipass(
            source_dir, platform=platform, model=model,
            budget_per_pass=specialist_budget, timeout=timeout_per_agent,
        )
        total_elapsed = time.time() - total_start
        print(f"\nDone in {total_elapsed:.0f}s (cache-multipass)")
        return {
            "source_dir": source_dir,
            "platform": platform,
            "architecture": architecture,
            "total_elapsed": round(total_elapsed, 1),
            **result,
        }

    sweep_model = model
    if architecture == "adversarial":
        sweep_model = ADV_SWEEP_MODEL if model == DEFAULT_MODEL else model

    print(f"\n{'='*70}")
    print(f"Audit Orchestrator ({architecture})")
    print(f"Source: {source_dir} | Sweep: {sweep_model}")
    if architecture == "adversarial":
        print(f"Court: {ADV_COURT_MODEL} | Report: {ADV_REPORT_MODEL}")
    print(f"Budgets: specialist=${specialist_budget:.2f}, merger=${merger_budget:.2f}")
    print(f"{'='*70}")

    recon = run_recon(source_dir, platform)

    # Phase 1: Sweep — use adversarial prompts if in adversarial mode
    if architecture == "adversarial":
        specs = build_adversarial_prompts(recon, platform)
    else:
        specs = build_specialist_prompts(recon, platform)

    # Override specialist list for run_specialists
    print(f"\n[Phase 1] {len(specs)} specialists ({architecture})...")
    specialist_results = []

    # Cache-warm: first specialist alone
    first, rest = specs[0], specs[1:]
    print(f"    [cache-warm] {first['name']}...")
    specialist_results.append(_run_specialist(first, source_dir, timeout_per_agent, sweep_model, specialist_budget))

    # Fork: remaining in parallel
    if rest:
        print(f"    [fork] {len(rest)} parallel...")
        with ThreadPoolExecutor(max_workers=MAX_PARALLEL_AGENTS) as executor:
            futures = {
                executor.submit(_run_specialist, s, source_dir, timeout_per_agent, sweep_model, specialist_budget): s
                for s in rest
            }
            for future in as_completed(futures):
                try:
                    specialist_results.append(future.result())
                except Exception as e:
                    s = futures[future]
                    print(f"    [{s['name']}] FAILED: {e}")
                    specialist_results.append({
                        "name": s["name"], "focus": s.get("focus", ""),
                        "output": f"[ERROR: {e}]", "elapsed": 0,
                        "finding_count": 0, "usage": {}, "cost": 0,
                    })

    total_findings = sum(r["finding_count"] for r in specialist_results)
    total_cost = sum(r.get("cost", 0) for r in specialist_results)
    print(f"\n    Total: {total_findings} findings | ${total_cost:.4f}")

    # Phase 2+: Architecture-specific pipeline
    if architecture == "adversarial":
        court_model = ADV_COURT_MODEL if model == DEFAULT_MODEL else model

        # Phase 2: Adversarial Court (Defender=Sonnet, Judge=Haiku)
        court_output, kept, total = run_adversarial_court(
            source_dir, specialist_results,
            model=court_model, budget=ADV_COURT_BUDGET, timeout=ADV_COURT_TIMEOUT,
            judge_model=ADV_REPORT_MODEL,  # Haiku for judge
        )

        # Phase 3: Forge PoC Gate
        court_with_poc = run_forge_gate(
            source_dir, court_output,
            model=court_model, budget=ADV_POC_BUDGET, timeout=ADV_POC_TIMEOUT,
        )

        # Phase 4: Report
        final_output = run_adversarial_report(
            source_dir, court_with_poc,
            model=ADV_REPORT_MODEL, budget=0.10, timeout=120,
        )
    else:
        final_output = run_merger(
            source_dir, specialist_results, platform,
            model, merger_budget, merge_timeout,
            triage_model=triage_model,
        )

    total_elapsed = time.time() - total_start

    print(f"\n{'='*70}")
    print(f"Done in {total_elapsed:.0f}s ({architecture})")
    print(token_tracker.summary())
    print(f"{'='*70}")

    return {
        "source_dir": source_dir,
        "platform": platform,
        "architecture": architecture,
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


def main():
    parser = argparse.ArgumentParser(description="Multi-agent audit orchestrator")
    parser.add_argument("source_dir", help="Path to source code directory")
    parser.add_argument("--platform", default="codearena")
    parser.add_argument("--timeout", type=int, default=TIMEOUT_PER_AGENT)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--triage-model", default="claude-haiku-4-5", help="Cheap model for triage stage")
    parser.add_argument("--specialist-budget", type=float, default=DEFAULT_SPECIALIST_BUDGET)
    parser.add_argument("--merger-budget", type=float, default=DEFAULT_MERGER_BUDGET)
    parser.add_argument("--merge-timeout", type=int, default=MERGE_TIMEOUT)
    parser.add_argument(
        "--architecture", choices=["classic", "adversarial", "cache-multipass"], default="classic",
        help="classic: 4 specialists + Haiku triage + Opus verify. "
             "adversarial: 6 perspectives + Prosecutor/Defender/Judge + Forge PoC (Sonnet-only). "
             "cache-multipass: sequential passes with shared cache prefix",
    )
    args = parser.parse_args()

    orchestrate(
        args.source_dir,
        platform=args.platform,
        timeout_per_agent=args.timeout,
        model=args.model,
        triage_model=args.triage_model,
        specialist_budget=args.specialist_budget,
        merger_budget=args.merger_budget,
        merge_timeout=args.merge_timeout,
        architecture=args.architecture,
    )


if __name__ == "__main__":
    main()
