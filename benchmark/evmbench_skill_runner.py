#!/usr/bin/env python3
"""
EVMBench detect-mode runner for the repo's /web3-hunt workflow.

Wraps local EVMBench audits through the repo's hunt command, stores raw
outputs, and scores them against ground truth using LLM-as-Judge semantic
matching.

Usage:
    python3 benchmark/evmbench_skill_runner.py --split detect-tasks
    python3 benchmark/evmbench_skill_runner.py --audit 2024-04-noya
    python3 benchmark/evmbench_skill_runner.py --compare
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
import time
from datetime import datetime
from pathlib import Path

from evmbench_common import (
    REPO_ROOT, BENCHMARK_DIR, SOURCES_DIR,
    load_config, normalize_model_id, model_cutoff,
    load_audit_config, load_finding_details,
    select_audits, clone_audit_source, get_scope_files,
)

sys.path.insert(0, str(REPO_ROOT))
from claude_cli import ClaudeCliUnavailable, run_claude_prompt
from llm_judge import score_with_judge

RESULTS_DIR = BENCHMARK_DIR / "results" / "evmbench"
OUTPUTS_DIR = RESULTS_DIR / "outputs"
REPORT_PATH = RESULTS_DIR / "reports" / "summary.md"


def _latest_report_text(start_time: float, source_dir: str | None = None) -> str:
    """Read the most recent audit report written by the agent.

    Agents may write to summary.md or [audit-name]-summary.md, so we
    glob for any recently modified .md file in the reports directory.
    """
    search_dirs = [REPORT_PATH.parent]
    if source_dir:
        search_dirs.append(Path(source_dir) / "audit-reports")

    for reports_dir in search_dirs:
        if not reports_dir.is_dir():
            continue
        # Find the most recently modified .md file written after start_time
        best_path, best_mtime = None, 0
        for md in reports_dir.glob("*.md"):
            mt = md.stat().st_mtime
            if mt >= start_time - 1 and mt > best_mtime:
                best_path, best_mtime = md, mt
        if best_path:
            return best_path.read_text(errors="replace")
    return ""


def _build_hunt_prompt(source_dir: Path) -> str:
    target = shlex.quote(str(source_dir))
    return f"/web3-hunt {target} codearena"


# ─── Skeleton extraction (0 LLM tokens) ───────────────────────────────

def _extract_skeleton(source_dir: Path) -> str:
    """Extract function signatures + state vars from all .sol files via tree-sitter.

    Returns a compact skeleton string (~5x smaller than raw source).
    """
    try:
        from tree_sitter_solidity.core import get_parser
    except ImportError:
        return ""  # fallback: no skeleton available

    parser = get_parser()
    skip = {"test", "tests", "lib", "node_modules", "out", "cache",
            "mock", "mocks", "artifacts", "typechain", ".git"}
    sol_files = sorted(
        f for f in source_dir.rglob("*.sol")
        if not any(p.lower() in skip for p in f.relative_to(source_dir).parts)
        and not f.name.endswith((".t.sol", ".s.sol"))
    )

    parts = []
    for f in sol_files:
        code = f.read_bytes()
        tree = parser.parse(code)
        rel = f.relative_to(source_dir)
        file_parts = [f"// FILE: {rel}"]

        for child in tree.root_node.children:
            if child.type == "import_directive":
                file_parts.append(child.text.decode(errors="replace").strip())
            elif child.type == "contract_declaration":
                names = [c for c in child.children if c.type == "identifier"]
                name = names[0].text.decode() if names else "?"
                inherit = [c for c in child.children if c.type == "inheritance_specifier"]
                inh = f" is {inherit[0].text.decode()}" if inherit else ""
                file_parts.append(f"contract {name}{inh} {{")

                body = [c for c in child.children if c.type == "contract_body"]
                if body:
                    for member in body[0].children:
                        if member.type == "function_definition":
                            sig_end = None
                            for mc in member.children:
                                if mc.type == "function_body":
                                    sig_end = mc.start_byte
                                    break
                            if sig_end:
                                sig = code[member.start_byte:sig_end].decode(errors="replace").strip()
                            else:
                                sig = member.text.decode(errors="replace").strip()
                            file_parts.append(f"  {sig} {{ ... }}")
                        elif member.type in ("state_variable_declaration", "event_definition",
                                             "error_declaration", "enum_definition", "struct_declaration"):
                            file_parts.append(f"  {member.text.decode(errors='replace').strip()}")
                        elif member.type == "modifier_definition":
                            mnames = [c for c in member.children if c.type == "identifier"]
                            mn = mnames[0].text.decode() if mnames else "?"
                            file_parts.append(f"  modifier {mn}() {{ ... }}")
                file_parts.append("}")
            elif child.type == "interface_declaration":
                names = [c for c in child.children if c.type == "identifier"]
                name = names[0].text.decode() if names else "?"
                file_parts.append(f"interface {name} {{ ... }}")

        parts.append("\n".join(file_parts))

    return "\n\n".join(parts)


def _build_raw_prompt(source_dir: Path) -> str:
    return (
        f"Find all high and medium severity security vulnerabilities in the "
        f"Solidity smart contracts in this directory: {source_dir}\n\n"
        f"STRATEGY (follow this order):\n"
        f"1. Read EVERY .sol file first. Skip nothing — peripheral files hide the best bugs.\n"
        f"2. If multiple contracts share the same interface (Connector, Strategy, Adapter, Vault), "
        f"cross-compare ALL implementations. The bug is the one that differs.\n"
        f"3. For every function returning value/TVL/balance/price/shares: trace what's ADDED vs "
        f"SUBTRACTED. Common bugs: adding debt instead of subtracting, missing staked/pending amounts.\n"
        f"4. For every external protocol call: verify the return value units, function variant, "
        f"and token destination.\n"
        f"5. Check access control on every state-changing function.\n"
        f"6. Check state updates vs external calls ordering (reentrancy).\n\n"
        f"For each vulnerability found, report:\n"
        f"FINDING: [title]\n"
        f"SEVERITY: High or Medium\n"
        f"FILE: [exact path:line numbers]\n"
        f"ROOT_CAUSE: [2-3 sentences]\n"
        f"IMPACT: [specific exploitable impact]\n"
    )


_LEAN_PREFIX = (
    "STRATEGY (follow this order):\n"
    "1. Read EVERY .sol file first. Skip nothing — peripheral files hide the best bugs.\n"
    "2. If multiple contracts share the same interface (Connector, Strategy, Adapter, Vault), "
    "cross-compare ALL implementations. The bug is the one that differs.\n"
    "3. For every function returning value/TVL/balance/price/shares: trace what's ADDED vs "
    "SUBTRACTED. Common bugs: adding debt instead of subtracting, missing staked/pending amounts.\n"
    "4. For every external protocol call: verify the return value units, function variant, "
    "and token destination.\n"
    "5. Check access control on every state-changing function.\n"
    "6. Check state updates vs external calls ordering (reentrancy).\n\n"
    "BEFORE REPORTING — mandatory verification:\n"
    "- Read ALL modifiers and parent contracts before claiming 'missing check'. Inherited guards are invisible at first glance.\n"
    "- High severity requires concrete attack: exact steps, preconditions satisfied, quantified profit. No 'could potentially'.\n"
    "- 'transfer() therefore reentrancy' is INVALID without showing: no nonReentrant, specific dirty state, profitable callback.\n"
    "- Confirm every file:line is from the correct contract. Do not confuse similarly-named functions across files.\n"
    "- Every finding needs satisfiable preconditions: caller has right role, pool has liquidity, values in exploitable range.\n"
    "- If funcA has a check but funcB doesn't, find the TECHNICAL REASON before reporting. It may be intentional.\n\n"
)

_LEAN_OUTPUT_FMT = (
    "Report ONLY confirmed vulnerabilities. Skip low/informational.\n"
    "Per finding — max 5 sentences total:\n"
    "FINDING: [title]\n"
    "SEVERITY: High or Medium\n"
    "FILE: [exact path:line numbers]\n"
    "ROOT_CAUSE: [2-3 sentences]\n"
    "IMPACT: [specific exploitable impact]\n"
)


def _build_lean_prompt(source_dir: Path) -> str:
    """Raw prompt + LLM failure mode warnings only (~120 tokens overhead).

    No category hints, no structural process — just guards against false positives.
    """
    return (
        f"Find all high and medium severity security vulnerabilities in the "
        f"Solidity smart contracts in this directory: {source_dir}\n\n"
        f"{_LEAN_PREFIX}"
        f"{_LEAN_OUTPUT_FMT}"
    )


def _build_lean_v2_prompt(source_dir: Path) -> str:
    """Lean v2: lean + output compression + budget-aware.

    Same verification rules as lean, but:
    - Compressed output format (fewer tokens wasted on verbose explanations)
    - Fixed prefix for prompt caching across sequential audit runs
    """
    return (
        f"Find all high and medium severity security vulnerabilities in the "
        f"Solidity smart contracts in this directory: {source_dir}\n\n"
        f"{_LEAN_PREFIX}"
        f"OUTPUT RULES:\n"
        f"- Report ONLY confirmed High/Medium vulnerabilities. No informational, no gas, no style.\n"
        f"- Max 5 sentences per finding. Be precise, not verbose.\n"
        f"- If unsure after verification checks above, do NOT report — skip it.\n\n"
        f"Per finding:\n"
        f"FINDING: [title]\n"
        f"SEVERITY: High|Medium\n"
        f"FILE: [path:line]\n"
        f"ROOT_CAUSE: [1-2 sentences]\n"
        f"IMPACT: [1 sentence, specific]\n"
    )


LEAN_V2_BUDGET = 0.50  # USD cap per audit


def _build_boosted_prompt(source_dir: Path) -> str:
    """Raw prompt + targeted hints for systematically weak categories.

    Hints derived from category-level miss rate analysis across all EVMBench
    audits (NOT tuned to any specific audit). Weak categories identified:
      state-management (13%), logic (15%), frontrunning (15%),
      oracle (20%), dos (23%), math (32%)
    """
    return (
        f"Find all high and medium severity security vulnerabilities in the "
        f"Solidity smart contracts in this directory: {source_dir}\n\n"
        f"STRATEGY (follow this order):\n"
        f"1. Read EVERY .sol file first. Skip nothing — peripheral files hide the best bugs.\n"
        f"2. If multiple contracts share the same interface (Connector, Strategy, Adapter, Vault), "
        f"cross-compare ALL implementations. The bug is the one that differs.\n"
        f"3. For every function returning value/TVL/balance/price/shares: trace what's ADDED vs "
        f"SUBTRACTED. Common bugs: adding debt instead of subtracting, missing staked/pending amounts.\n"
        f"4. For every external protocol call: verify the return value units, function variant, "
        f"and token destination.\n"
        f"5. Check access control on every state-changing function.\n"
        f"6. Check state updates vs external calls ordering (reentrancy).\n\n"
        f"COMMONLY MISSED — pay extra attention:\n"
        f"7. STATE MANAGEMENT: trace multi-step operations (deposit→stake→withdraw). "
        f"Does a reset/remove/decrease function update ALL related state? "
        f"Look for partial state updates where one mapping is cleared but a counter or flag is not.\n"
        f"8. BUSINESS LOGIC: check if the code matches its documented intent. "
        f"Does a 'remove' function actually remove all references? "
        f"Does a 'pause' truly halt all paths? Are enum/flag transitions complete?\n"
        f"9. MATH PRECISION: division before multiplication, rounding direction "
        f"(always in protocol's favor?), truncation in type casts (uint256→uint128), "
        f"token decimal mismatches (6 vs 18), fee-on-transfer token assumptions.\n"
        f"10. FRONTRUNNING: can a pending transaction be observed and exploited? "
        f"Signature replay across chains (missing chain ID), missing deadline checks, "
        f"sandwich-attackable swaps without slippage bounds.\n"
        f"11. ORACLE: stale price (no freshness check), spot price vs TWAP, "
        f"return value unit mismatches (ETH vs USD vs WAD), zero/negative price handling.\n"
        f"12. DOS: operations that revert on edge inputs (zero amount, empty array, "
        f"blacklisted token, underwater position causing underflow). Can an external "
        f"actor force a revert that blocks critical protocol functions?\n\n"
        f"For each vulnerability found, report:\n"
        f"FINDING: [title]\n"
        f"SEVERITY: High or Medium\n"
        f"FILE: [exact path:line numbers]\n"
        f"ROOT_CAUSE: [2-3 sentences]\n"
        f"IMPACT: [specific exploitable impact]\n"
    )


def _build_checklist_prompt(source_dir: Path) -> str:
    """Structured checklist prompt: forces per-file analysis + category sweep."""
    sol_files = get_scope_files(source_dir)
    file_list = "\n".join(f"  - {f}" for f in sol_files)
    return (
        f"You are a smart contract security auditor. Analyze ALL Solidity files in: {source_dir}\n\n"
        f"## In-scope files ({len(sol_files)} total)\n{file_list}\n\n"
        f"## MANDATORY ANALYSIS PROCEDURE\n\n"
        f"### Step 1: Per-File Analysis\n"
        f"Read EVERY file listed above. For each file, emit exactly one line:\n"
        f"  CHECKED: [filename] — [SAFE | SUSPECT: brief reason]\n"
        f"Do NOT skip any file. Missing a CHECKED line = incomplete analysis.\n\n"
        f"### Step 2: Category Sweep\n"
        f"After reading all files, evaluate each vulnerability category explicitly:\n"
        f"  CATEGORY: value-flow — [finding or N/A with 1-line reason]\n"
        f"  CATEGORY: access-control — [finding or N/A]\n"
        f"  CATEGORY: reentrancy — [finding or N/A]\n"
        f"  CATEGORY: oracle-manipulation — [finding or N/A]\n"
        f"  CATEGORY: cross-contract-interaction — [finding or N/A]\n"
        f"  CATEGORY: input-validation — [finding or N/A]\n"
        f"  CATEGORY: state-management — [finding or N/A]\n"
        f"  CATEGORY: math-precision — [finding or N/A]\n\n"
        f"### Step 3: Cross-Contract Comparison\n"
        f"If multiple contracts share the same interface (Strategy, Connector, Adapter, Vault, etc.):\n"
        f"- Compare ALL implementations side by side\n"
        f"- The bug is often the one that handles something differently\n"
        f"- Check: same parameters, same return handling, same state updates?\n\n"
        f"### Step 4: Report Findings\n"
        f"For each vulnerability found, report:\n"
        f"FINDING: [title]\n"
        f"SEVERITY: High or Medium\n"
        f"FILE: [exact path:line numbers]\n"
        f"ROOT_CAUSE: [2-3 sentences]\n"
        f"IMPACT: [specific exploitable impact]\n\n"
        f"### Step 5: Self-Check\n"
        f"FINDING_COUNT: [N]\n"
        f"FILES_CHECKED: [N]/{len(sol_files)}\n"
    )


# ─── Tiered strategy (Sonnet sweep → Opus deep) ─────────────────────────

SWEEP_MODEL_DEFAULT = "claude-sonnet-4-6"
DEEP_MODEL_DEFAULT = "claude-opus-4-6"
SWEEP_BUDGET_DEFAULT = 0.50
DEEP_BUDGET_DEFAULT = 0.40


def _build_sweep_prompt(source_dir: Path) -> str:
    """Sonnet sweep: read all files, classify as CANDIDATE or SAFE."""
    sol_files = get_scope_files(source_dir)
    file_list = "\n".join(f"  - {f}" for f in sol_files)
    return (
        f"You are a smart contract security analyst performing a BROAD SWEEP.\n"
        f"Your job is to READ every Solidity file and classify it for deeper analysis.\n\n"
        f"Directory: {source_dir}\n"
        f"Files ({len(sol_files)}):\n{file_list}\n\n"
        f"## Instructions\n"
        f"1. Read EVERY .sol file listed above.\n"
        f"2. For each file, emit exactly ONE classification line:\n"
        f"   CANDIDATE: [filepath] — [brief reason this file may contain a bug]\n"
        f"   SAFE: [filepath] — [brief reason no bugs here]\n\n"
        f"3. If two or more files interact in a way that could be exploitable, also emit:\n"
        f"   CROSS_REF: [file_a.sol, file_b.sol] — [interaction to investigate]\n\n"
        f"## CRITICAL RULES\n"
        f"- When in doubt, mark as CANDIDATE. False positives are OK; false negatives are not.\n"
        f"- Files with ANY of these MUST be CANDIDATE:\n"
        f"  * Value calculations (TVL, shares, balance, price, exchange rate)\n"
        f"  * External calls to other contracts or protocols\n"
        f"  * Access control logic\n"
        f"  * State changes across multiple storage slots\n"
        f"  * Token transfer/approval logic\n"
        f"- Pure interfaces, events-only files, or trivial getters can be SAFE.\n"
        f"- You must classify ALL {len(sol_files)} files. Missing files = incomplete.\n"
    )


def _parse_candidates(sweep_output: str) -> list[str]:
    """Extract CANDIDATE and CROSS_REF file paths from sweep output."""
    candidates = set()
    for line in sweep_output.splitlines():
        # Strip markdown formatting (bold, backticks, list markers)
        line = line.strip().lstrip("-*>").strip()
        line = re.sub(r"[`*_]", "", line).strip()

        if re.match(r"(?i)CANDIDATE\s*:", line):
            path = re.split(r"(?i)CANDIDATE\s*:", line, 1)[1].split("—")[0].split("–")[0].split("-", 1)[0].strip()
            # Extract .sol path
            sol_match = re.search(r"[\w/]+\.sol", path)
            if sol_match:
                candidates.add(sol_match.group())
            elif path:
                candidates.add(path)
        elif re.match(r"(?i)CROSS_REF\s*:", line):
            files_part = re.split(r"(?i)CROSS_REF\s*:", line, 1)[1].split("—")[0].split("–")[0].strip()
            for sol_match in re.finditer(r"[\w/]+\.sol", files_part):
                candidates.add(sol_match.group())
        elif re.match(r"(?i)SUSPECT", line):
            # Some models use SUSPECT instead of CANDIDATE
            sol_match = re.search(r"[\w/]+\.sol", line)
            if sol_match:
                candidates.add(sol_match.group())
    return sorted(candidates)


def _build_deep_prompt(source_dir: Path, candidates: list[str], all_files: list[str]) -> str:
    """Opus deep: analyze only candidate files for vulnerabilities."""
    candidate_list = "\n".join(f"  - {f}" for f in candidates)
    skipped = [f for f in all_files if f not in candidates]
    skipped_summary = f"\n{len(skipped)} files were pre-screened as low-risk and excluded." if skipped else ""

    return (
        f"You are a senior smart contract security auditor performing DEEP ANALYSIS.\n"
        f"A broad sweep already classified files. Focus on these candidates:\n\n"
        f"Directory: {source_dir}\n"
        f"## Priority files ({len(candidates)} candidates)\n{candidate_list}\n"
        f"{skipped_summary}\n\n"
        f"## Instructions\n"
        f"1. Read each candidate file carefully.\n"
        f"2. For cross-contract bugs, also read imported/referenced files as needed.\n"
        f"3. Apply these checks:\n"
        f"   - Trace every value function (TVL, balance, price, shares): what's ADDED vs SUBTRACTED?\n"
        f"   - For every external call: verify return value units, function variant, token destination\n"
        f"   - Check access control on every state-changing function\n"
        f"   - Check state updates vs external calls ordering (reentrancy)\n"
        f"   - Compare implementations sharing the same interface\n\n"
        f"For each vulnerability found, report:\n"
        f"FINDING: [title]\n"
        f"SEVERITY: High or Medium\n"
        f"FILE: [exact path:line numbers]\n"
        f"ROOT_CAUSE: [2-3 sentences]\n"
        f"IMPACT: [specific exploitable impact]\n"
    )


# ─── lean-v3: skeleton triage → targeted deep ──────────────────────────

def _build_skeleton_triage_prompt(skeleton: str, source_dir: Path) -> str:
    """Call 1: give skeleton, ask which functions need deep analysis."""
    return (
        f"You are a smart contract security analyst. Below is the SKELETON of a Solidity codebase "
        f"(function signatures + state variables, bodies replaced with {{...}}).\n\n"
        f"Directory: {source_dir}\n\n"
        f"## Skeleton\n```solidity\n{skeleton}\n```\n\n"
        f"## Task\n"
        f"Identify functions that are MOST LIKELY to contain High/Medium vulnerabilities.\n"
        f"Look for:\n"
        f"- Value/balance/TVL calculations (shares, exchange rates)\n"
        f"- External calls to other contracts\n"
        f"- Access control inconsistencies\n"
        f"- State changes that span multiple contracts\n\n"
        f"Output a list of suspect functions to deep-analyze:\n"
        f"SUSPECT: [file.sol] [functionName] — [1-line reason]\n\n"
        f"List at most 20 suspects. Be selective — only functions with real attack potential.\n"
    )


def _parse_suspects(triage_output: str) -> list[str]:
    """Extract SUSPECT file paths from triage output."""
    suspects = set()
    for line in triage_output.splitlines():
        line = line.strip().lstrip("-*>").strip()
        line = re.sub(r"[`*_]", "", line).strip()
        if re.match(r"(?i)SUSPECT\s*:", line):
            sol_match = re.search(r"[\w/]+\.sol", line)
            if sol_match:
                suspects.add(sol_match.group())
    return sorted(suspects)


def _build_skeleton_deep_prompt(source_dir: Path, suspects: list[str], skeleton: str) -> str:
    """Call 2: read only suspect files, find vulns."""
    suspect_list = "\n".join(f"  - {f}" for f in suspects)
    return (
        f"You are a senior smart contract security auditor.\n"
        f"A skeleton triage identified these files as suspicious:\n\n"
        f"Directory: {source_dir}\n"
        f"## Suspect files ({len(suspects)})\n{suspect_list}\n\n"
        f"## Architecture context (skeleton)\n```solidity\n{skeleton[:8000]}\n```\n\n"
        f"## Instructions\n"
        f"1. Read each suspect file's FULL source code (not just skeleton).\n"
        f"2. Also read any files they import/interact with.\n"
        f"3. Find High/Medium vulnerabilities only. Skip low/informational.\n\n"
        f"Per finding (max 5 sentences):\n"
        f"FINDING: [title]\n"
        f"SEVERITY: High|Medium\n"
        f"FILE: [path:line]\n"
        f"ROOT_CAUSE: [1-2 sentences]\n"
        f"IMPACT: [1 sentence, specific]\n"
    )


# ─── lean-v4: skeleton context + full source, single call ────────────

def _build_lean_v4_prompt(source_dir: Path, skeleton: str) -> str:
    """Single call: skeleton as architecture map + model reads full files itself."""
    return (
        f"You are a senior smart contract security auditor.\n"
        f"Directory: {source_dir}\n\n"
        f"## Architecture Overview (auto-extracted, function bodies omitted)\n"
        f"```solidity\n{skeleton[:12000]}\n```\n\n"
        f"## Instructions\n"
        f"1. Use the architecture overview above to understand the codebase structure, "
        f"inheritance, state variables, and cross-contract relationships.\n"
        f"2. Read the FULL source code of files that look suspicious based on the overview.\n"
        f"3. Focus on:\n"
        f"   - Value/balance/TVL functions: trace what's ADDED vs SUBTRACTED\n"
        f"   - External calls: verify return value units, function variant, token destination\n"
        f"   - Cross-contract interactions visible in the overview\n"
        f"   - Access control inconsistencies between similar functions\n"
        f"4. Report ONLY confirmed High/Medium vulnerabilities.\n\n"
        f"Per finding (max 5 sentences):\n"
        f"FINDING: [title]\n"
        f"SEVERITY: High|Medium\n"
        f"FILE: [path:line]\n"
        f"ROOT_CAUSE: [1-2 sentences]\n"
        f"IMPACT: [1 sentence, specific]\n"
    )


# ─── lean-v5: skeleton context + pattern RAG + full read ─────────────

def _load_pattern_db() -> list[dict]:
    pattern_path = REPO_ROOT / "patterns" / "vulnerability_patterns_full.json"
    if not pattern_path.exists():
        return []
    with open(pattern_path) as f:
        return json.load(f)


def _search_patterns(skeleton: str, top_k: int = 10) -> list[dict]:
    """Search pattern DB using skeleton keywords. Returns top-K relevant patterns."""
    pats = _load_pattern_db()
    if not pats:
        return []
    skel_lower = skeleton.lower()
    scored = []
    for p in pats:
        score = 0
        for kw in p.get("keywords", []):
            if kw.lower() in skel_lower:
                score += 2
        for word in p.get("code_signal", "").lower().split():
            if len(word) > 4 and word in skel_lower:
                score += 1
        for word in p.get("trigger", "").lower().split():
            if len(word) > 5 and word in skel_lower:
                score += 1
        if score > 0:
            scored.append({**p, "_score": score})
    scored.sort(key=lambda x: -x["_score"])
    return scored[:top_k]


def _build_lean_v5_prompt(source_dir: Path, skeleton: str, patterns: list[dict]) -> str:
    """Single call: skeleton map + matched vulnerability patterns + read all files."""
    pattern_checklist = ""
    if patterns:
        items = []
        for i, p in enumerate(patterns, 1):
            items.append(
                f"{i}. [{p.get('category','')}] {p.get('pattern_name','')}\n"
                f"   Trigger: {p.get('trigger','')}\n"
                f"   Signal: {p.get('code_signal','')}"
            )
        pattern_checklist = "\n".join(items)

    return (
        f"You are a senior smart contract security auditor.\n"
        f"Directory: {source_dir}\n\n"
        f"## Architecture Overview (auto-extracted skeleton)\n"
        f"```solidity\n{skeleton[:12000]}\n```\n\n"
        f"## Known Vulnerability Patterns (from public audit/hack databases)\n"
        f"These patterns were found in past DeFi exploits. Check if this codebase has similar issues:\n"
        f"{pattern_checklist}\n\n"
        f"## Instructions\n"
        f"1. Read the FULL source code of EVERY in-scope .sol file. Do NOT skip files based on skeleton.\n"
        f"2. Do NOT judge safety from function names or NatSpec alone — read the actual body.\n"
        f"3. For each pattern above, check if this codebase has a matching vulnerability.\n"
        f"4. Also look for any High/Medium vulnerability NOT in the pattern list.\n"
        f"5. Report ONLY confirmed vulnerabilities with specific code evidence.\n\n"
        f"Per finding (max 5 sentences):\n"
        f"FINDING: [title]\n"
        f"SEVERITY: High|Medium\n"
        f"FILE: [path:line]\n"
        f"ROOT_CAUSE: [1-2 sentences]\n"
        f"IMPACT: [1 sentence, specific]\n"
    )


# ─── lean-v6: raw + pattern RAG only (no skeleton) ──────────────────

def _build_lean_v6_prompt(source_dir: Path, patterns: list[dict]) -> str:
    """Raw read-everything prompt + pattern checklist. No skeleton overhead."""
    pattern_checklist = ""
    if patterns:
        items = []
        for i, p in enumerate(patterns, 1):
            items.append(f"{i}. [{p.get('category','')}] {p.get('trigger','')}")
        pattern_checklist = "\n".join(items)

    return (
        f"Find all high and medium severity security vulnerabilities in the "
        f"Solidity smart contracts in this directory: {source_dir}\n\n"
        f"STRATEGY:\n"
        f"1. Read EVERY .sol file. Skip nothing.\n"
        f"2. Cross-compare all implementations sharing the same interface.\n"
        f"3. Trace every value/TVL/balance/price function: what's ADDED vs SUBTRACTED.\n"
        f"4. For every external call: verify return value units, function variant, token destination.\n"
        f"5. Check access control on every state-changing function.\n"
        f"6. Check state updates vs external calls ordering (reentrancy).\n\n"
        f"KNOWN ATTACK PATTERNS (from 726 past DeFi exploits — check if this code is vulnerable):\n"
        f"{pattern_checklist}\n\n"
        f"Do NOT judge safety from function names alone — read the actual body.\n\n"
        f"Per finding (max 5 sentences):\n"
        f"FINDING: [title]\n"
        f"SEVERITY: High|Medium\n"
        f"FILE: [path:line]\n"
        f"ROOT_CAUSE: [1-2 sentences]\n"
        f"IMPACT: [1 sentence, specific]\n"
    )


# ─── lean-v7: multi-pass partitioned deep read ──────────────────────

PARTITION_SIZE = 30  # files per partition — larger = fewer passes = less overhead

_PARTITION_PROMPT_TMPL = (
    "Find all High/Medium vulnerabilities in these Solidity files: {source_dir}\n\n"
    "YOUR FILES ({n_files}):\n{file_list}\n\n"
    "Read every file's full body. Trace value flows, check access control, check reentrancy.\n"
    "Also read imported files as needed for cross-contract context.\n\n"
    "Per finding: FINDING: [title] | SEVERITY: High|Medium | FILE: [path:line] | ROOT_CAUSE: [1-2 sentences] | IMPACT: [specific]\n"
)


def _run_partition(source_dir: Path, partition_files: list[str], model: str, timeout: int, max_budget: float | None, pass_idx: int, total: int) -> dict:
    """Run a single partition. Designed to be called from ThreadPoolExecutor."""
    file_list = "\n".join(f"  - {f}" for f in partition_files)
    prompt = _PARTITION_PROMPT_TMPL.format(
        source_dir=source_dir, n_files=len(partition_files), file_list=file_list,
    )
    try:
        result = run_claude_prompt(
            prompt, cwd=str(source_dir), timeout=timeout,
            output_format="json", permission_mode="bypassPermissions",
            model=model, max_budget=max_budget, auto_accept_permissions=True,
        )
    except ClaudeCliUnavailable as exc:
        return {"text": "", "cost": 0.0, "usage": {}, "error": str(exc)}

    parsed = result.get("parsed", {})
    text = (parsed.get("result", "") if isinstance(parsed, dict) else "") or result.get("raw", "")
    cost = float(result.get("total_cost_usd", 0.0) or 0.0)
    findings = len(re.findall(r'(?m)^FINDING:', text))
    print(f"  [pass {pass_idx}/{total}] ${cost:.4f} | {findings} findings | {len(partition_files)}f")
    return {"text": text, "cost": cost, "usage": result.get("usage", {}), "error": None}


def run_lean_v7_audit(
    audit_id: str,
    source_dir: Path,
    model: str,
    timeout: int = 900,
    max_budget: float | None = None,
) -> dict:
    """lean-v7: partition files → parallel deep read → union findings. Opus-budget-competitive."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    all_files = get_scope_files(source_dir)
    start = time.time()

    partitions = [all_files[i:i+PARTITION_SIZE] for i in range(0, len(all_files), PARTITION_SIZE)]
    if not partitions:
        partitions = [all_files]

    n_passes = len(partitions)
    print(f"  [v7] {len(all_files)} files → {n_passes} passes ({PARTITION_SIZE}f each)")

    # Sequential execution — claude -p doesn't support concurrent calls reliably
    results = []
    for idx, part in enumerate(partitions, 1):
        r = _run_partition(source_dir, part, model, timeout, max_budget, idx, n_passes)
        results.append(r)
        if r.get("error"):
            print(f"  [pass {idx}] ERROR: {r['error']}")

    total_cost = sum(r["cost"] for r in results)
    total_usage = {"input_tokens": 0, "output_tokens": 0,
                   "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    for r in results:
        for key in total_usage:
            total_usage[key] += int(r.get("usage", {}).get(key, 0) or 0)

    report_text = "\n\n".join(r["text"] for r in results if r["text"])

    return {
        "audit_id": audit_id,
        "prompt": f"[lean-v7: {n_passes}-pass parallel, model={model}]",
        "elapsed_seconds": round(time.time() - start, 1),
        "raw_text": report_text,
        "raw_json": {},
        "report_text": report_text,
        "usage": total_usage,
        "total_cost_usd": total_cost,
        "stop_reason": "",
        "is_error": False,
        "errors": [r["error"] for r in results if r.get("error")],
        "partitions": n_passes,
        "files_per_partition": PARTITION_SIZE,
    }


def run_lean_v6_audit(
    audit_id: str,
    source_dir: Path,
    model: str,
    timeout: int = 900,
    max_budget: float | None = None,
) -> dict:
    """lean-v6: raw + pattern RAG checklist, no skeleton. Single call."""
    start = time.time()

    # Pattern RAG from skeleton keywords (still need skeleton for search, but don't send it to LLM)
    skeleton = _extract_skeleton(source_dir)
    patterns = _search_patterns(skeleton, top_k=10) if skeleton else []
    print(f"  [patterns] {len(patterns)} matched (skeleton used for search only, not sent to LLM)")

    prompt = _build_lean_v6_prompt(source_dir, patterns)
    try:
        result = run_claude_prompt(
            prompt,
            cwd=str(source_dir),
            timeout=timeout,
            output_format="json",
            permission_mode="bypassPermissions",
            model=model,
            max_budget=max_budget,
            auto_accept_permissions=True,
        )
    except ClaudeCliUnavailable as exc:
        raise RuntimeError(f"Claude CLI unavailable: {exc}") from exc

    parsed = result.get("parsed", {})
    raw_text = result.get("raw", "")
    report_text = (parsed.get("result", "") if isinstance(parsed, dict) else "") or raw_text
    cost = float(result.get("total_cost_usd", 0.0) or 0.0)

    return {
        "audit_id": audit_id,
        "prompt": f"[lean-v6: raw+patternRAG, model={model}]",
        "elapsed_seconds": round(time.time() - start, 1),
        "raw_text": raw_text,
        "raw_json": parsed,
        "report_text": report_text,
        "usage": result.get("usage", {}),
        "total_cost_usd": cost,
        "stop_reason": parsed.get("stop_reason", "") if isinstance(parsed, dict) else "",
        "is_error": parsed.get("is_error", False) if isinstance(parsed, dict) else False,
        "errors": parsed.get("errors", []) if isinstance(parsed, dict) else [],
        "patterns_matched": len(patterns),
    }


def run_lean_v5_audit(
    audit_id: str,
    source_dir: Path,
    model: str,
    timeout: int = 900,
    max_budget: float | None = None,
) -> dict:
    """lean-v5: skeleton + pattern RAG checklist + full file read, single call."""
    all_files = get_scope_files(source_dir)
    start = time.time()

    # Phase 0: skeleton (0 tokens)
    skeleton = _extract_skeleton(source_dir)
    raw_chars = sum(f.stat().st_size for f in source_dir.rglob("*.sol")
                    if not any(p.lower() in {"test","tests","lib","node_modules","out","cache"}
                               for p in f.relative_to(source_dir).parts))
    print(f"  [skeleton] {len(all_files)} files → {len(skeleton):,} chars ({raw_chars/max(len(skeleton),1):.1f}x)")

    # Phase 0b: pattern RAG search (0 tokens)
    patterns = _search_patterns(skeleton, top_k=10)
    print(f"  [patterns] {len(patterns)} matched from DB")
    for p in patterns[:3]:
        print(f"    → [{p.get('category','')}] {p.get('pattern_name','')}")

    if not skeleton:
        return run_skill_audit(audit_id, source_dir, model, timeout, max_budget, raw=True, strategy="raw")

    prompt = _build_lean_v5_prompt(source_dir, skeleton, patterns)
    try:
        result = run_claude_prompt(
            prompt,
            cwd=str(source_dir),
            timeout=timeout,
            output_format="json",
            permission_mode="bypassPermissions",
            model=model,
            max_budget=max_budget,
            auto_accept_permissions=True,
        )
    except ClaudeCliUnavailable as exc:
        raise RuntimeError(f"Claude CLI unavailable: {exc}") from exc

    parsed = result.get("parsed", {})
    raw_text = result.get("raw", "")
    report_text = (parsed.get("result", "") if isinstance(parsed, dict) else "") or raw_text
    cost = float(result.get("total_cost_usd", 0.0) or 0.0)

    return {
        "audit_id": audit_id,
        "prompt": f"[lean-v5: skeleton+patternRAG+fullread, model={model}]",
        "elapsed_seconds": round(time.time() - start, 1),
        "raw_text": raw_text,
        "raw_json": parsed,
        "report_text": report_text,
        "usage": result.get("usage", {}),
        "total_cost_usd": cost,
        "stop_reason": parsed.get("stop_reason", "") if isinstance(parsed, dict) else "",
        "is_error": parsed.get("is_error", False) if isinstance(parsed, dict) else False,
        "errors": parsed.get("errors", []) if isinstance(parsed, dict) else [],
        "skeleton_chars": len(skeleton),
        "patterns_matched": len(patterns),
    }


def run_lean_v4_audit(
    audit_id: str,
    source_dir: Path,
    model: str,
    timeout: int = 900,
    max_budget: float | None = None,
) -> dict:
    """lean-v4: skeleton as context + full source, single LLM call."""
    all_files = get_scope_files(source_dir)
    start = time.time()

    # Phase 0: skeleton (0 tokens)
    skeleton = _extract_skeleton(source_dir)
    raw_chars = sum(f.stat().st_size for f in source_dir.rglob("*.sol")
                    if not any(p.lower() in {"test","tests","lib","node_modules","out","cache"}
                               for p in f.relative_to(source_dir).parts))
    print(f"  [skeleton] {len(all_files)} files → {len(skeleton):,} chars ({raw_chars/max(len(skeleton),1):.1f}x reduction)")

    if not skeleton:
        print(f"  [skeleton] FAILED — falling back to raw")
        return run_skill_audit(audit_id, source_dir, model, timeout, max_budget, raw=True, strategy="raw")

    # Single call with skeleton context
    prompt = _build_lean_v4_prompt(source_dir, skeleton)
    try:
        result = run_claude_prompt(
            prompt,
            cwd=str(source_dir),
            timeout=timeout,
            output_format="json",
            permission_mode="bypassPermissions",
            model=model,
            max_budget=max_budget,
            auto_accept_permissions=True,
        )
    except ClaudeCliUnavailable as exc:
        raise RuntimeError(f"Claude CLI unavailable: {exc}") from exc

    parsed = result.get("parsed", {})
    raw_text = result.get("raw", "")
    report_text = (parsed.get("result", "") if isinstance(parsed, dict) else "") or raw_text
    cost = float(result.get("total_cost_usd", 0.0) or 0.0)
    usage = result.get("usage", {})

    return {
        "audit_id": audit_id,
        "prompt": f"[lean-v4: skeleton-context, model={model}]",
        "elapsed_seconds": round(time.time() - start, 1),
        "raw_text": raw_text,
        "raw_json": parsed,
        "report_text": report_text,
        "usage": usage,
        "total_cost_usd": cost,
        "stop_reason": parsed.get("stop_reason", "") if isinstance(parsed, dict) else "",
        "is_error": parsed.get("is_error", False) if isinstance(parsed, dict) else False,
        "errors": parsed.get("errors", []) if isinstance(parsed, dict) else [],
        "skeleton_chars": len(skeleton),
        "raw_chars": raw_chars,
    }


def run_lean_v3_audit(
    audit_id: str,
    source_dir: Path,
    model: str,
    timeout: int = 900,
    max_budget: float | None = None,
) -> dict:
    """lean-v3: skeleton extraction (0 tokens) → triage (cheap) → deep (targeted)."""
    all_files = get_scope_files(source_dir)
    start = time.time()
    total_cost = 0.0
    total_usage = {"input_tokens": 0, "output_tokens": 0,
                   "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}

    # ── Phase 0: Skeleton extraction (0 LLM tokens) ──
    skeleton = _extract_skeleton(source_dir)
    raw_chars = sum(f.stat().st_size for f in source_dir.rglob("*.sol")
                    if not any(p.lower() in {"test","tests","lib","node_modules","out","cache"}
                               for p in f.relative_to(source_dir).parts))
    reduction = raw_chars / max(len(skeleton), 1)
    print(f"  [skeleton] {len(all_files)} files → {len(skeleton):,} chars (raw {raw_chars:,}, {reduction:.1f}x reduction)")

    if not skeleton:
        print(f"  [skeleton] FAILED — falling back to raw")
        return run_skill_audit(audit_id, source_dir, model, timeout, max_budget, raw=True, strategy="raw")

    # ── Call 1: Skeleton triage ──
    print(f"  [triage] {model}")
    triage_prompt = _build_skeleton_triage_prompt(skeleton, source_dir)
    try:
        triage_result = run_claude_prompt(
            triage_prompt,
            cwd=str(source_dir),
            timeout=timeout // 3,
            output_format="json",
            permission_mode="bypassPermissions",
            model=model,
            max_budget=0.30,
            auto_accept_permissions=True,
        )
    except ClaudeCliUnavailable as exc:
        raise RuntimeError(f"Claude CLI unavailable: {exc}") from exc

    triage_text = triage_result.get("parsed", {}).get("result", "") or triage_result.get("raw", "")
    triage_cost = float(triage_result.get("total_cost_usd", 0.0) or 0.0)
    total_cost += triage_cost
    for key in total_usage:
        total_usage[key] += int(triage_result.get("usage", {}).get(key, 0) or 0)

    suspects = _parse_suspects(triage_text)
    if not suspects:
        # Fallback: all files
        suspects = all_files
        print(f"  [triage] WARNING: no suspects parsed, falling back to all {len(all_files)} files")

    print(f"  [triage] ${triage_cost:.4f} | suspects: {len(suspects)}/{len(all_files)}")

    # ── Call 2: Deep analysis on suspects ──
    print(f"  [deep] {model} | {len(suspects)} files")
    deep_prompt = _build_skeleton_deep_prompt(source_dir, suspects, skeleton)
    try:
        deep_result = run_claude_prompt(
            deep_prompt,
            cwd=str(source_dir),
            timeout=timeout,
            output_format="json",
            permission_mode="bypassPermissions",
            model=model,
            max_budget=max_budget,
            auto_accept_permissions=True,
        )
    except ClaudeCliUnavailable as exc:
        raise RuntimeError(f"Claude CLI unavailable: {exc}") from exc

    deep_text = deep_result.get("parsed", {}).get("result", "") or deep_result.get("raw", "")
    deep_cost = float(deep_result.get("total_cost_usd", 0.0) or 0.0)
    total_cost += deep_cost
    for key in total_usage:
        total_usage[key] += int(deep_result.get("usage", {}).get(key, 0) or 0)

    print(f"  [deep] ${deep_cost:.4f}")

    report_text = deep_text

    return {
        "audit_id": audit_id,
        "prompt": f"[lean-v3: skeleton→triage→deep, model={model}]",
        "elapsed_seconds": round(time.time() - start, 1),
        "raw_text": deep_text,
        "raw_json": deep_result.get("parsed", {}),
        "report_text": report_text,
        "usage": total_usage,
        "total_cost_usd": total_cost,
        "stop_reason": deep_result.get("parsed", {}).get("stop_reason", ""),
        "is_error": deep_result.get("parsed", {}).get("is_error", False),
        "errors": deep_result.get("parsed", {}).get("errors", []),
        "skeleton_chars": len(skeleton),
        "raw_chars": raw_chars,
        "skeleton_reduction": round(reduction, 1),
        "triage_suspects": len(suspects),
        "triage_total_files": len(all_files),
        "triage_cost_usd": triage_cost,
        "deep_cost_usd": deep_cost,
    }


def run_tiered_audit(
    audit_id: str,
    source_dir: Path,
    deep_model: str,
    sweep_model: str = SWEEP_MODEL_DEFAULT,
    timeout: int = 900,
    sweep_budget: float = SWEEP_BUDGET_DEFAULT,
    deep_budget: float = DEEP_BUDGET_DEFAULT,
) -> dict:
    """Two-call tiered pipeline: Sonnet sweep → Opus deep."""
    all_files = get_scope_files(source_dir)
    start = time.time()
    total_cost = 0.0
    total_usage = {"input_tokens": 0, "output_tokens": 0,
                   "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}

    # ── Call 1: Sonnet sweep ──
    print(f"  [sweep] {sweep_model} | budget ${sweep_budget:.2f} | {len(all_files)} files")
    sweep_prompt = _build_sweep_prompt(source_dir)
    try:
        sweep_result = run_claude_prompt(
            sweep_prompt,
            cwd=str(source_dir),
            timeout=timeout // 2,
            output_format="json",
            permission_mode="bypassPermissions",
            model=sweep_model,
            max_budget=sweep_budget,
            auto_accept_permissions=True,
        )
    except ClaudeCliUnavailable as exc:
        raise RuntimeError(f"Claude CLI unavailable: {exc}") from exc

    sweep_text = sweep_result.get("parsed", {}).get("result", "") or sweep_result.get("raw", "")
    sweep_cost = float(sweep_result.get("total_cost_usd", 0.0) or 0.0)
    total_cost += sweep_cost

    for key in total_usage:
        total_usage[key] += int(sweep_result.get("usage", {}).get(key, 0) or 0)

    # ── Parse candidates ──
    candidates = _parse_candidates(sweep_text)
    if not candidates:
        candidates = all_files  # fallback: analyze everything
        print(f"  [sweep] WARNING: no candidates parsed, falling back to all {len(all_files)} files")
    elif len(candidates) < len(all_files) * 0.3:
        print(f"  [sweep] WARNING: only {len(candidates)}/{len(all_files)} candidates (<30%)")

    print(f"  [sweep] ${sweep_cost:.4f} | candidates: {len(candidates)}/{len(all_files)}")

    # ── Call 2: Opus deep ──
    print(f"  [deep]  {deep_model} | budget ${deep_budget:.2f} | {len(candidates)} candidates")
    deep_prompt = _build_deep_prompt(source_dir, candidates, all_files)
    try:
        deep_result = run_claude_prompt(
            deep_prompt,
            cwd=str(source_dir),
            timeout=timeout,
            output_format="json",
            permission_mode="bypassPermissions",
            model=deep_model,
            max_budget=deep_budget,
            auto_accept_permissions=True,
        )
    except ClaudeCliUnavailable as exc:
        raise RuntimeError(f"Claude CLI unavailable: {exc}") from exc

    deep_text = deep_result.get("parsed", {}).get("result", "") or deep_result.get("raw", "")
    deep_cost = float(deep_result.get("total_cost_usd", 0.0) or 0.0)
    total_cost += deep_cost

    for key in total_usage:
        total_usage[key] += int(deep_result.get("usage", {}).get(key, 0) or 0)

    print(f"  [deep]  ${deep_cost:.4f}")

    # Judge scoring only sees deep findings (sweep text is classifications, not findings)
    report_text = deep_text

    return {
        "audit_id": audit_id,
        "prompt": f"[tiered: sweep={sweep_model}, deep={deep_model}]",
        "elapsed_seconds": round(time.time() - start, 1),
        "raw_text": deep_text,
        "raw_json": deep_result.get("parsed", {}),
        "report_text": report_text,
        "usage": total_usage,
        "total_cost_usd": total_cost,
        "stop_reason": deep_result.get("parsed", {}).get("stop_reason", ""),
        "is_error": deep_result.get("parsed", {}).get("is_error", False),
        "errors": deep_result.get("parsed", {}).get("errors", []),
        "sweep_candidates": len(candidates),
        "sweep_total_files": len(all_files),
        "sweep_cost_usd": sweep_cost,
        "deep_cost_usd": deep_cost,
    }


def run_skill_audit(
    audit_id: str,
    source_dir: Path,
    model: str,
    timeout: int,
    max_budget: float | None = None,
    raw: bool = False,
    strategy: str = "skill",
) -> dict:
    if strategy == "lean-v2":
        prompt = _build_lean_v2_prompt(source_dir)
    elif strategy == "lean":
        prompt = _build_lean_prompt(source_dir)
    elif strategy == "boosted":
        prompt = _build_boosted_prompt(source_dir)
    elif strategy == "checklist":
        prompt = _build_checklist_prompt(source_dir)
    elif strategy == "raw" or raw:
        prompt = _build_raw_prompt(source_dir)
    else:
        prompt = _build_hunt_prompt(source_dir)
    start = time.time()

    is_raw_like = strategy in ("raw", "checklist", "boosted", "lean", "lean-v2")
    use_cwd = str(source_dir) if is_raw_like else str(REPO_ROOT)
    use_add_dir = None if is_raw_like else str(source_dir)

    try:
        result = run_claude_prompt(
            prompt,
            cwd=use_cwd,
            timeout=timeout,
            output_format="json",
            permission_mode="bypassPermissions",
            add_dir=use_add_dir,
            model=model,
            max_budget=max_budget,
            auto_accept_permissions=True,
        )
        parsed = result.get("parsed", {})
        raw_text = result.get("raw", "")
    except ClaudeCliUnavailable as exc:
        raise RuntimeError(f"Claude CLI unavailable: {exc}") from exc

    if result.get("timed_out"):
        parsed = {"result": "[TIMEOUT]"}
        raw_text = "[TIMEOUT]"

    # Combine: report file (detailed) + CLI output (may have extra findings)
    report_file = _latest_report_text(start, source_dir=str(source_dir) if not raw else None)
    cli_output = parsed.get("result", "") or raw_text
    report_text = (report_file + "\n\n" + cli_output).strip() if report_file else cli_output

    return {
        "audit_id": audit_id,
        "prompt": prompt[:500] + "..." if len(prompt) > 500 else prompt,
        "elapsed_seconds": round(time.time() - start, 1),
        "raw_text": raw_text,
        "raw_json": parsed,
        "report_text": report_text,
        "usage": result.get("usage", {}),
        "total_cost_usd": result.get("total_cost_usd", 0.0),
        "stop_reason": parsed.get("stop_reason", ""),
        "is_error": parsed.get("is_error", False),
        "errors": parsed.get("errors", []),
    }


def count_model_findings(report_text: str) -> int:
    """Count distinct findings reported by the model.

    Handles multiple output formats: structured markdown (## [H-01]),
    FINDING: blocks, numbered lists, and free-form headers.
    Deduplicates by finding ID when available.
    """
    if not report_text or not report_text.strip():
        return 0

    finding_ids: set[str] = set()

    # Pattern 1: ## [H-01] or ## H-01 or ### M-1 (structured markdown)
    for m in re.finditer(r'(?m)^#{2,4}\s*\[?([HML]-?\d+)\]?', report_text):
        finding_ids.add(m.group(1).upper())

    # Pattern 2: FINDING: title (our output format)
    for m in re.finditer(r'(?m)^FINDING:\s*(.+)', report_text):
        finding_ids.add(f"F-{m.start()}")  # unique by position

    # Pattern 3: **[H-01]** or **H-01:** inline bold
    for m in re.finditer(r'\*\*\[?([HML]-?\d+)\]?\*\*', report_text):
        finding_ids.add(m.group(1).upper())

    # Pattern 4: numbered findings like "1. **Title" at start of section
    # Only count if no other patterns matched (fallback)
    if not finding_ids:
        numbered = re.findall(r'(?m)^\d+\.\s+\*\*[^*]+\*\*', report_text)
        finding_ids.update(f"N-{i}" for i in range(len(numbered)))

    # Pattern 5: "Finding N:" or "Vulnerability N:"
    if not finding_ids:
        for m in re.finditer(r'(?mi)^(?:finding|vulnerability)\s+(\d+)', report_text):
            finding_ids.add(f"V-{m.group(1)}")

    return len(finding_ids)


def score_audit_result(
    audit_id: str,
    report_text: str,
    audit_config: dict,
    finding_details: dict[str, str],
) -> dict:
    vulns = audit_config.get("vulnerabilities", [])

    if not report_text.strip():
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
        try:
            results = score_with_judge(report_text, vulns, finding_details)
        except Exception as e:
            print(f"  [!] Judge error for {audit_id}: {e}")
            results = None
        if not results:
            print(f"  [!] Judge returned no results for {audit_id} — scoring as 0/N")
            results = [
                {
                    "vuln_id": v["id"],
                    "title": v.get("title", ""),
                    "award": float(v.get("award", 0.0) or 0.0),
                    "detected": False,
                    "confidence": 0.0,
                    "reason": "judge_failed",
                    "matched_skill_finding": "",
                }
                for v in vulns
            ]

    total_award = sum(v.get("award", 0.0) or 0.0 for v in vulns)
    detected = sum(1 for r in results if r["detected"])
    detected_award = sum(r.get("award", 0.0) or 0.0 for r in results if r["detected"])

    total_model_findings = count_model_findings(report_text)
    # Ensure reported >= detected (judge may match findings not caught by regex)
    total_model_findings = max(total_model_findings, detected)
    precision = round((detected / total_model_findings) if total_model_findings > 0 else 0.0, 3)
    recall = round((detected / len(vulns)) if vulns else 0.0, 3)
    f1 = round(2 * precision * recall / (precision + recall), 3) if (precision + recall) > 0 else 0.0

    return {
        "audit_id": audit_id,
        "total_vulns": len(vulns),
        "detected": detected,
        "recall": recall,
        "total_model_findings": total_model_findings,
        "precision": precision,
        "f1": f1,
        "total_award": round(total_award, 2),
        "detected_award": round(detected_award, 2),
        "vulns": results,
        "scoring_method": "llm_judge",
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
    rescore: bool = False,
    label: str | None = None,
    raw: bool = False,
    strategy: str = "skill",
    sweep_model: str | None = None,
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

    mode_label = strategy if strategy != "skill" else ("RAW (no framework)" if raw else "skill-wrapper")
    print(f"Model: {model} | Strategy: {mode_label}")
    if strategy == "tiered":
        print(f"Sweep model: {sweep_model or SWEEP_MODEL_DEFAULT}")
    if cutoff:
        print(f"Knowledge cutoff: {cutoff}")
    print(f"Audits: {len(audit_ids)}")
    if max_budget is not None:
        print(f"Budget cap per audit: ${max_budget:.2f}")

    for idx, audit_id in enumerate(audit_ids, start=1):
        if max_total_cost is not None and total_cost >= max_total_cost:
            print(f"\nReached total run budget (${max_total_cost:.2f}); stopping.")
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
        elif strategy == "lean-v7":
            source_dir = clone_audit_source(audit_id, audit_config, raw=True)
            run_result = run_lean_v7_audit(
                audit_id, source_dir, model=model,
                timeout=timeout, max_budget=max_budget,
            )
            _save_output_artifacts(audit_id, run_result["report_text"], run_result["raw_json"])
        elif strategy == "lean-v6":
            source_dir = clone_audit_source(audit_id, audit_config, raw=True)
            run_result = run_lean_v6_audit(
                audit_id, source_dir, model=model,
                timeout=timeout, max_budget=max_budget,
            )
            _save_output_artifacts(audit_id, run_result["report_text"], run_result["raw_json"])
        elif strategy == "lean-v5":
            source_dir = clone_audit_source(audit_id, audit_config, raw=True)
            run_result = run_lean_v5_audit(
                audit_id, source_dir, model=model,
                timeout=timeout, max_budget=max_budget,
            )
            _save_output_artifacts(audit_id, run_result["report_text"], run_result["raw_json"])
        elif strategy == "lean-v4":
            source_dir = clone_audit_source(audit_id, audit_config, raw=True)
            run_result = run_lean_v4_audit(
                audit_id, source_dir, model=model,
                timeout=timeout, max_budget=max_budget,
            )
            _save_output_artifacts(audit_id, run_result["report_text"], run_result["raw_json"])
        elif strategy == "lean-v3":
            source_dir = clone_audit_source(audit_id, audit_config, raw=True)
            run_result = run_lean_v3_audit(
                audit_id, source_dir, model=model,
                timeout=timeout, max_budget=max_budget,
            )
            _save_output_artifacts(audit_id, run_result["report_text"], run_result["raw_json"])
        elif strategy == "tiered":
            source_dir = clone_audit_source(audit_id, audit_config, raw=True)
            # Scale budget by codebase size (base for ~20 files, scale up for larger)
            n_files = len(get_scope_files(source_dir))
            scale = max(1.0, n_files / 20.0)
            s_budget = max_budget * 0.4 if max_budget else SWEEP_BUDGET_DEFAULT * scale
            d_budget = max_budget * 0.6 if max_budget else DEEP_BUDGET_DEFAULT * scale
            run_result = run_tiered_audit(
                audit_id, source_dir,
                deep_model=model,
                sweep_model=sweep_model or SWEEP_MODEL_DEFAULT,
                timeout=timeout,
                sweep_budget=s_budget,
                deep_budget=d_budget,
            )
        else:
            is_raw = strategy == "raw" or raw
            source_dir = clone_audit_source(audit_id, audit_config, raw=is_raw)
            run_result = run_skill_audit(
                audit_id, source_dir, model=model,
                timeout=timeout, max_budget=max_budget,
                raw=is_raw, strategy=strategy,
            )
            _save_output_artifacts(audit_id, run_result["report_text"], run_result["raw_json"])

        score = score_audit_result(audit_id, run_result["report_text"], audit_config, finding_details)
        usage = run_result.get("usage", {})
        cost = float(run_result.get("total_cost_usd", 0.0) or 0.0)

        total_cost += cost
        total_input += int(usage.get("input_tokens", 0) or 0)
        total_output += int(usage.get("output_tokens", 0) or 0)
        total_cache_create += int(usage.get("cache_creation_input_tokens", 0) or 0)
        total_cache_read += int(usage.get("cache_read_input_tokens", 0) or 0)

        per_audit.append({
            **score,
            "elapsed_seconds": run_result.get("elapsed_seconds", 0.0),
            "total_cost_usd": round(cost, 4),
            "usage": usage,
            "stop_reason": run_result.get("stop_reason", ""),
            "errors": run_result.get("errors", []),
        })

        print(
            f"  detected {score['detected']}/{score['total_vulns']} "
            f"(recall {score['recall']:.1%}) | "
            f"reported {score['total_model_findings']} | "
            f"prec {score['precision']:.1%} | F1 {score['f1']:.1%} | ${cost:.4f}"
        )

    total_vulns = sum(item["total_vulns"] for item in per_audit)
    total_detected = sum(item["detected"] for item in per_audit)
    total_award = sum(item["total_award"] for item in per_audit)
    total_award_detected = sum(item["detected_award"] for item in per_audit)
    total_model_findings = sum(item.get("total_model_findings", 0) for item in per_audit)

    overall_recall = round((total_detected / total_vulns) if total_vulns else 0.0, 3)
    overall_precision = round((total_detected / total_model_findings) if total_model_findings else 0.0, 3)
    overall_f1 = round(
        2 * overall_precision * overall_recall / (overall_precision + overall_recall), 3
    ) if (overall_precision + overall_recall) > 0 else 0.0

    return {
        "timestamp": datetime.now().isoformat(),
        "label": label or strategy,
        "model": model,
        "knowledge_cutoff": cutoff,
        "mode": strategy,
        "sweep_model": sweep_model if strategy == "tiered" else None,
        "audits_count": len(per_audit),
        "total_vulns": total_vulns,
        "total_detected": total_detected,
        "total_model_findings": total_model_findings,
        "overall_recall": overall_recall,
        "overall_precision": overall_precision,
        "overall_f1": overall_f1,
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


def save_results(summary: dict, prefix: str = "skill_run") -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"{prefix}_{timestamp}.json"
    with open(path, "w") as f:
        json.dump(summary, f, indent=2)
    return path


def compare_results() -> None:
    runs = sorted(RESULTS_DIR.glob("skill_run_*.json"))
    if len(runs) < 2:
        print("Need at least 2 skill runs to compare.")
        return

    with open(runs[-2]) as f:
        prev = json.load(f)
    with open(runs[-1]) as f:
        curr = json.load(f)

    metrics = ["overall_recall", "overall_precision", "overall_f1", "total_cost_usd", "cost_per_detected", "detected_per_dollar"]
    print(f"\n{'Metric':<22} {'Previous':>12} {'Current':>12} {'Delta':>12}")
    print("-" * 62)
    for key in metrics:
        p = prev.get(key, 0)
        c = curr.get(key, 0)
        delta = c - p
        sign = "+" if delta > 0 else ""
        print(f"{key:<22} {p:>12.4f} {c:>12.4f} {sign}{delta:>11.4f}")


def main() -> None:
    parser = argparse.ArgumentParser(description="EVMBench detect eval via /web3-hunt")
    parser.add_argument("--split", type=str, default="detect-tasks")
    parser.add_argument("--audit", type=str, help="Single audit ID")
    parser.add_argument("--model", type=str, default="claude-opus-4-6")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--max-budget", type=float, default=None)
    parser.add_argument("--max-total-cost", type=float, default=None)
    parser.add_argument("--strategy", type=str, default="skill",
                        choices=["raw", "skill", "checklist", "tiered", "boosted", "lean", "lean-v2", "lean-v3", "lean-v4", "lean-v5", "lean-v6", "lean-v7"],
                        help="Detection strategy")
    parser.add_argument("--raw", action="store_true", help="Alias for --strategy raw")
    parser.add_argument("--sweep-model", type=str, default=None,
                        help="Sweep model for tiered strategy (default: sonnet)")
    parser.add_argument("--rescore", action="store_true")
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--post-cutoff", action="store_true")
    parser.add_argument("--pre-cutoff", action="store_true")
    parser.add_argument("--label", type=str, default=None)
    args = parser.parse_args()

    if args.compare:
        compare_results()
        return

    config = load_config()
    model = normalize_model_id(args.model)
    cutoff = model_cutoff(model, config)

    audit_ids = select_audits(
        split=args.split, audit_id=args.audit, limit=args.limit,
        post_cutoff=args.post_cutoff, pre_cutoff=args.pre_cutoff, cutoff=cutoff,
    )

    if args.dry_run:
        print(f"Model: {model}\nKnowledge cutoff: {cutoff}")
        print(f"Selected audits ({len(audit_ids)}):")
        for a in audit_ids:
            print(f"  - {a}")
        return

    strategy = "raw" if args.raw else args.strategy
    sweep_model = normalize_model_id(args.sweep_model) if args.sweep_model else None

    summary = run_detect_benchmark(
        audit_ids=audit_ids, model=model, timeout=args.timeout,
        max_budget=args.max_budget, max_total_cost=args.max_total_cost,
        rescore=args.rescore, label=args.label, raw=args.raw,
        strategy=strategy, sweep_model=sweep_model,
    )
    path = save_results(summary)

    print(f"\n{'=' * 70}")
    print(f"Recall:    {summary['total_detected']}/{summary['total_vulns']} ({summary['overall_recall']:.1%})")
    print(f"Precision: {summary['total_detected']}/{summary['total_model_findings']} ({summary['overall_precision']:.1%})")
    print(f"F1:        {summary['overall_f1']:.1%}")
    print(f"Cost:      ${summary['total_cost_usd']:.4f}")
    print(f"$/detect:  ${summary['cost_per_detected']:.4f}")
    print(f"Saved to:  {path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
