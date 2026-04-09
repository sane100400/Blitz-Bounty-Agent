#!/usr/bin/env python3
"""
Autonomous bug bounty hunt loop orchestrator.

Runs /bounty-hunt in a subprocess loop, feeding drop reasons back
into each iteration until a valid finding is confirmed.

Usage:
    python3 hunt_loop.py <immunefi-url> <rpc-url> [max_iterations] [max_cost_usd]

Example:
    python3 hunt_loop.py "https://immunefi.com/bug-bounty/balancer" "https://1rpc.io/eth" 10 3.00
"""

import subprocess
import sys
import json
import os
import re
import time
from pathlib import Path
from datetime import datetime

from claude_cli import ClaudeCliUnavailable, run_claude_prompt

# ─── CONFIG ──────────────────────────────────────────────────────────────────

MAX_ITERATIONS_DEFAULT = 10
MAX_COST_DEFAULT = 3.00      # USD — auto-stop when exceeded
CONSECUTIVE_DRY_LIMIT = 3   # stop after N iterations with no GO candidate
STATE_FILE = "hunt-state.json"
LOG_DIR = Path("hunt-logs")

# Signals Claude must emit in its output for the loop to detect status
SIGNAL_FOUND    = "HUNT_SIGNAL:FOUND:"       # e.g. HUNT_SIGNAL:FOUND:eth-stuck-clr
SIGNAL_DROP     = "HUNT_SIGNAL:DROP:"        # e.g. HUNT_SIGNAL:DROP:reentrancy:no-guard-bypassed
SIGNAL_CONTINUE = "HUNT_SIGNAL:CONTINUE"     # all candidates dropped, try next iteration
SIGNAL_EXHAUST  = "HUNT_SIGNAL:EXHAUSTED"    # no new attack surface remains

# ─── STATE ───────────────────────────────────────────────────────────────────

def load_state() -> dict:
    if Path(STATE_FILE).exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {
        "target": "",
        "rpc": "",
        "status": "HUNTING",
        "iteration": 0,
        "recon_done": False,
        "drop_history": [],        # [{iteration, candidate, reason, lesson}]
        "poc_failures": [],        # [{iteration, candidate, failure_reason}]
        "lessons": [],             # accumulated lessons for next iteration
        "active_finding": None,
        "gist_url": None,
        "report_file": None,
        "total_cost_usd": 0.0,
        "start_time": datetime.now().isoformat(),
    }

def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

# ─── CLAUDE INVOCATION ───────────────────────────────────────────────────────

def build_prompt(phase: str, state: dict, extra: str = "") -> str:
    """Build a phase-specific prompt that includes full state context."""

    drop_summary = ""
    if state["drop_history"]:
        drop_summary = "\n\nPREVIOUSLY DROPPED CANDIDATES (do not repeat these):\n"
        for d in state["drop_history"]:
            drop_summary += f"  - Iteration {d['iteration']}: {d['candidate']} → {d['reason']}\n"
            if d.get("lesson"):
                drop_summary += f"    Lesson: {d['lesson']}\n"

    poc_failure_summary = ""
    if state["poc_failures"]:
        poc_failure_summary = "\n\nPoC FAILURES (these seemed valid but PoC failed):\n"
        for p in state["poc_failures"]:
            poc_failure_summary += f"  - Iteration {p['iteration']}: {p['candidate']} → {p['failure_reason']}\n"

    lessons = ""
    if state["lessons"]:
        lessons = "\n\nACCUMULATED LESSONS:\n" + "\n".join(f"  - {l}" for l in state["lessons"])

    base_context = f"""
TARGET: {state['target']}
RPC: {state['rpc']}
ITERATION: {state['iteration']}
{drop_summary}
{poc_failure_summary}
{lessons}
""".strip()

    phase_prompts = {

        "recon": f"""
You are running Phase A (Recon + Unaudited Diff) of an Immunefi bug bounty hunt.

{base_context}

Tasks:
1. Fetch the Immunefi scope page. Extract in-scope contracts (addresses+names),
   exact reward tier language (verbatim), and exclusions.
2. Find the GitHub repo. Fetch audits/WONTFIX.md (read fully) and last audit date.
3. Find files modified after the last audit date (post-audit = unaudited = your target).
   Sort by priority: new files > new functions > logic changes.

Output your findings in this exact format so the orchestrator can parse it:
RECON_DONE
LAST_AUDIT_DATE: YYYY-MM-DD
WONTFIX_COUNT: N
IN_SCOPE_COUNT: N
UNAUDITED_FILES_COUNT: N

Then continue with detailed findings.
""",

        "hunt": f"""
You are running Phase B+C (Attack Surface + Triage) of an Immunefi bug bounty hunt.
This is iteration {state['iteration']}.

{base_context}

Your task:
1. Generate 3-5 NEW attack scenario candidates for {state['target']}.
   DO NOT repeat any previously dropped candidate or attack class unless you have
   new evidence from the codebase that changes the analysis.
   Focus on POST-AUDIT code (unaudited = never reviewed by a paid auditor).

2. For each candidate, run all 5 triage checks:
   CHECK 1 - Code trace: exact path, find every guard
   CHECK 2 - Intentional design: find analogous pattern elsewhere, read PR
   CHECK 3 - Duplicate check: GitHub issues/PRs, audit reports, WONTFIX
   CHECK 4 - Impact threshold: maps to which Immunefi tier exactly?
   CHECK 5 - Economic feasibility (CRITICAL — measured data only, no estimates):
     - Gas price + ETH/USD: read-only calls (safe on mainnet)
       cast gas-price --rpc-url {state['rpc']}
       cast call <CHAINLINK_FEED> "latestAnswer()(int256)" --rpc-url {state['rpc']}
     - Gas units: from test_economic_paramSweep() in poc.t.sol (mainnet FORK, not live)
     - Calculate: gas_cost_usd = gas_units_from_forge * gas_price / 1e18 * eth_usd
     - Find OPTIMAL attack params (sweep full valid range, not just obvious case)
     - Profit ratio = value_extracted / gas_cost_usd (must be > 1)
     - Is attack repeatable? Total extractable = value_per_tx * max_reps
     - Build param sweep table with MEASURED data. If UNPROFITABLE at ALL params → DROP

3. For each candidate emit ONE of:
   {SIGNAL_DROP}<candidate-name>:<reason>
   or proceed to PoC.

4. If any candidate passes all 4 checks, emit:
   GO:<candidate-name>:<one-line description>

5. If no candidates pass, emit: {SIGNAL_CONTINUE}
6. If no new candidates exist: {SIGNAL_EXHAUST}

{extra}
""",

        "poc": f"""
You are running Phase D (PoC Verification) of an Immunefi bug bounty hunt.
This is iteration {state['iteration']}.

{base_context}

GO candidate: {extra}
RPC: {state['rpc']}

Tasks:
1. Write poc.t.sol — Foundry mainnet fork test
   - vm.createSelectFork("{state['rpc']}")
   - Real deployed addresses only
   - Control test (normal operation, no ETH): must PASS
   - Exploit test: assert the impact
   Run: forge test --match-contract <Name> --fork-url {state['rpc']} -vv

2. Write poc.py — Python web3.py on-chain verification
   - Check vulnerable selector in bytecode
   - Check no recovery path
   - Print block, balances, TVL
   Run: python3 poc.py

3. Run both. Report:
   - If BOTH pass: emit {SIGNAL_FOUND}<finding-name>
     Then upload gist: gh gist create poc.t.sol poc.py --desc "..." --public
     Print the gist URL.
   - If EITHER fails: emit {SIGNAL_DROP}<candidate-name>:<technical-failure-reason>
     Explain what the failure reveals about why this is NOT a bug.
""",

    }

    return phase_prompts.get(phase, "")

def run_claude(prompt: str, iteration: int, phase: str, state: dict | None = None) -> str:
    """Run claude CLI with the given prompt, return stdout. Tracks cost if state provided."""
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"iter{iteration:02d}_{phase}_{int(time.time())}.log"

    print(f"\n{'─'*60}")
    print(f"  [iter {iteration}] [{phase}] running claude...")
    print(f"  log → {log_file}")
    print(f"{'─'*60}")

    # Write prompt to temp file (avoids shell escaping issues)
    prompt_file = LOG_DIR / f"prompt_{iteration}_{phase}.txt"
    prompt_file.write_text(prompt)

    try:
        result = run_claude_prompt(
            prompt,
            timeout=600,
            output_format="json",
        )
        parsed = result.get("parsed", {})
        output = (parsed.get("result", "") if isinstance(parsed, dict) else "") or result.get("raw", "")
        cost = float(result.get("total_cost_usd", 0.0) or (parsed.get("total_cost_usd", 0.0) if isinstance(parsed, dict) else 0.0))
        if state is not None and cost > 0:
            state["total_cost_usd"] = state.get("total_cost_usd", 0.0) + cost
            print(f"  cost: ${cost:.4f} (cumulative: ${state['total_cost_usd']:.4f})")
        if result.get("timed_out"):
            output = f"[TIMEOUT] Phase {phase} exceeded 10 minutes"
    except ClaudeCliUnavailable as exc:
        print(f"\n[ERROR] Claude CLI unavailable: {exc}")
        sys.exit(2)

    log_file.write_text(output)
    print(output[:2000])  # show first 2000 chars live
    if len(output) > 2000:
        print(f"  ... (truncated, full output in {log_file})")

    return output

# ─── SIGNAL PARSING ──────────────────────────────────────────────────────────

def parse_output(output: str, state: dict) -> dict:
    """Parse Claude's output for control signals. Returns action dict."""

    lines = output.split("\n")

    # Check for FOUND
    for line in lines:
        if SIGNAL_FOUND in line:
            name = line.split(SIGNAL_FOUND)[-1].strip().split()[0]
            # Extract gist URL if present
            gist_url = None
            gist_match = re.search(r'https://gist\.github\.com/\S+', output)
            if gist_match:
                gist_url = gist_match.group(0)
            return {"action": "FOUND", "finding": name, "gist_url": gist_url}

    # Check for EXHAUSTED
    if SIGNAL_EXHAUST in output:
        return {"action": "EXHAUSTED"}

    # Check for GO (candidate ready for PoC)
    for line in lines:
        if line.startswith("GO:"):
            parts = line.split(":", 2)
            if len(parts) >= 3:
                return {"action": "GO", "candidate": parts[1], "description": parts[2]}
            elif len(parts) == 2:
                return {"action": "GO", "candidate": parts[1], "description": ""}

    # Collect all DROPs
    drops = []
    for line in lines:
        if SIGNAL_DROP in line:
            rest = line.split(SIGNAL_DROP)[-1].strip()
            if ":" in rest:
                candidate, reason = rest.split(":", 1)
                drops.append({"candidate": candidate.strip(), "reason": reason.strip()})
            else:
                drops.append({"candidate": rest, "reason": "unspecified"})

    if drops:
        return {"action": "DROP", "drops": drops}

    # CONTINUE signal or no clear signal
    if SIGNAL_CONTINUE in output:
        return {"action": "CONTINUE"}

    # Fallback: treat as continue with note
    return {"action": "CONTINUE", "note": "no signal detected in output"}

# ─── MAIN LOOP ───────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    target       = sys.argv[1]
    rpc          = sys.argv[2] if len(sys.argv) > 2 else "https://1rpc.io/eth"
    max_iter     = int(sys.argv[3]) if len(sys.argv) > 3 else MAX_ITERATIONS_DEFAULT
    max_cost     = float(sys.argv[4]) if len(sys.argv) > 4 else MAX_COST_DEFAULT

    state = load_state()
    state["target"] = target
    state["rpc"]    = rpc
    save_state(state)

    print(f"""
╔══════════════════════════════════════════════════════════╗
  BOUNTY HUNT LOOP
  Target:   {target}
  RPC:      {rpc}
  Max iter: {max_iter}
  Cost cap: ${max_cost:.2f}
  State:    {STATE_FILE}
╚══════════════════════════════════════════════════════════╝
""")

    # ── Phase A: Recon (once) ────────────────────────────────────────────────
    if not state["recon_done"]:
        print("\n[PHASE A] RECON + UNAUDITED DIFF")
        prompt = build_prompt("recon", state)
        output = run_claude(prompt, 0, "recon", state)

        if "RECON_DONE" in output:
            state["recon_done"] = True
            # Parse key fields
            for line in output.split("\n"):
                if line.startswith("LAST_AUDIT_DATE:"):
                    state["last_audit_date"] = line.split(":", 1)[1].strip()
                elif line.startswith("WONTFIX_COUNT:"):
                    try:
                        state["wontfix_count"] = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif line.startswith("IN_SCOPE_COUNT:"):
                    try:
                        state["in_scope_count"] = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif line.startswith("UNAUDITED_FILES_COUNT:"):
                    try:
                        state["unaudited_count"] = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
            save_state(state)
            print(f"\n[✓] Recon complete. Last audit: {state.get('last_audit_date', 'unknown')}")

            # ROI screening for Immunefi targets
            n_scope = state.get("in_scope_count", 0)
            n_unaudited = state.get("unaudited_count", 0)
            n_wontfix = state.get("wontfix_count", 0)
            if n_scope > 50:
                print(f"    ⚠ LOW ROI: {n_scope} in-scope contracts — heavily audited protocol")
            if n_unaudited == 0:
                print(f"    ⚠ LOW ROI: 0 unaudited files — all code has been reviewed")
            if n_wontfix > 20:
                print(f"    ⚠ LOW ROI: {n_wontfix} WONTFIX items — mature, well-defended codebase")
        else:
            print("\n[!] Recon did not emit RECON_DONE. Continuing anyway.")
            state["recon_done"] = True
            save_state(state)

    # ── Phase B–D: Hunt loop ─────────────────────────────────────────────────
    consecutive_dry = 0

    while state["iteration"] < max_iter:
        state["iteration"] += 1
        save_state(state)

        # ── Cost cap check ─────────────────────────────────────────────────
        if state.get("total_cost_usd", 0) >= max_cost:
            print(f"\n[$$] Cost cap reached: ${state['total_cost_usd']:.2f} >= ${max_cost:.2f}")
            break

        print(f"\n{'═'*60}")
        print(f"  ITERATION {state['iteration']} / {max_iter}")
        print(f"  Drops so far:     {len(state['drop_history'])}")
        print(f"  PoC failures:     {len(state['poc_failures'])}")
        print(f"  Cost so far:      ${state.get('total_cost_usd', 0):.4f} / ${max_cost:.2f}")
        print(f"  Consecutive dry:  {consecutive_dry} / {CONSECUTIVE_DRY_LIMIT}")
        print(f"{'═'*60}")

        # ── Phase B+C: Hunt + Triage ─────────────────────────────────────
        prompt = build_prompt("hunt", state)
        output = run_claude(prompt, state["iteration"], "hunt", state)
        result = parse_output(output, state)

        if result["action"] == "EXHAUSTED":
            print("\n[✗] Attack surface exhausted. No new candidates.")
            break

        if result["action"] == "FOUND":
            # Shouldn't happen here (found is in PoC phase) but handle it
            _handle_found(result, state)
            break

        if result["action"] == "DROP":
            consecutive_dry += 1
            for d in result["drops"]:
                state["drop_history"].append({
                    "iteration": state["iteration"],
                    "candidate": d["candidate"],
                    "reason": d["reason"],
                    "lesson": "",
                })
            # Extract lessons from output
            lesson_match = re.findall(r'Lesson:\s*(.+)', output)
            state["lessons"].extend(lesson_match)
            save_state(state)
            print(f"\n[→] All candidates dropped. Feeding {len(result['drops'])} reasons into next iteration.")
            if consecutive_dry >= CONSECUTIVE_DRY_LIMIT:
                print(f"\n[✗] {CONSECUTIVE_DRY_LIMIT} consecutive dry iterations — early exit.")
                break
            continue

        if result["action"] == "GO":
            consecutive_dry = 0  # reset on GO
            candidate  = result["candidate"]
            desc       = result["description"]
            print(f"\n[GO] Candidate: {candidate} — {desc}")
            print("[PHASE D] Running PoC verification...")

            # ── Phase D: PoC ─────────────────────────────────────────────
            poc_prompt = build_prompt("poc", state, extra=f"{candidate}: {desc}")
            poc_output = run_claude(poc_prompt, state["iteration"], "poc", state)
            poc_result = parse_output(poc_output, state)

            if poc_result["action"] == "FOUND":
                _handle_found(poc_result, state)
                return  # EXIT

            else:
                # PoC failed
                failure_reason = "unknown"
                if poc_result.get("drops"):
                    failure_reason = poc_result["drops"][0].get("reason", "unknown")
                state["poc_failures"].append({
                    "iteration": state["iteration"],
                    "candidate": candidate,
                    "failure_reason": failure_reason,
                })
                # Also add to drop history
                state["drop_history"].append({
                    "iteration": state["iteration"],
                    "candidate": candidate,
                    "reason": f"PoC failed: {failure_reason}",
                    "lesson": f"Seemed valid at triage but PoC revealed: {failure_reason}",
                })
                save_state(state)
                print(f"\n[✗] PoC failed for '{candidate}': {failure_reason}")
                print("[→] Feeding failure reason into next iteration.")
                continue

        # CONTINUE or unknown
        consecutive_dry += 1
        print(f"\n[→] No GO candidates this iteration. Next.")
        if consecutive_dry >= CONSECUTIVE_DRY_LIMIT:
            print(f"\n[✗] {CONSECUTIVE_DRY_LIMIT} consecutive dry iterations — early exit.")
            break

    # ── Loop ended without finding ───────────────────────────────────────────
    if state["status"] != "FOUND":
        print(f"""
{'═'*60}
  HUNT ENDED — No valid finding confirmed
  Iterations: {state['iteration']}
  Total drops: {len(state['drop_history'])}
  PoC failures: {len(state['poc_failures'])}
  Total cost:  ${state.get('total_cost_usd', 0):.4f}
  State saved: {STATE_FILE}

  UNCERTAIN items for manual review:
{'═'*60}""")

        # Print any unresolved UNCERTAIN items from last iteration
        with open(LOG_DIR / sorted(LOG_DIR.iterdir())[-1]) as f:
            last_log = f.read()
        uncertain = re.findall(r'UNCERTAIN[:\s]+(.+)', last_log)
        for u in uncertain:
            print(f"  ? {u}")

        print(f"\n  Suggestion: try a different protocol or wait for new post-audit deployments.")


def _handle_found(result: dict, state: dict):
    finding   = result.get("finding", "unknown")
    gist_url  = result.get("gist_url", "not uploaded")

    state["status"]          = "FOUND"
    state["active_finding"]  = finding
    state["gist_url"]        = gist_url
    save_state(state)

    print(f"""
{'═'*60}
  ✅ VALID FINDING CONFIRMED
  Finding:    {finding}
  Gist:       {gist_url}
  Iteration:  {state['iteration']}
  Report:     write report-{finding}.md  (run /bounty-loop again to write report)
{'═'*60}""")


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
