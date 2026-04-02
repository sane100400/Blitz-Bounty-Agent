---
description: "Autonomous Immunefi bug bounty loop on live protocols: hunts until a valid, non-duplicate, PoC-confirmed finding is found. Feeds drop reasons back into each iteration."
argument-hint: "[immunefi-url-or-protocol] [rpc-url] [optional: max-iterations=10]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, WebFetch, WebSearch, Agent
---

Run the autonomous bug bounty hunt loop for: **$ARGUMENTS**

Parse arguments:
- `$1` = target (Immunefi URL or protocol name)
- `$2` = RPC URL (e.g. https://1rpc.io/eth). If not provided, use https://1rpc.io/eth
- `$3` = max iterations (default: 10)

---

# LOOP ARCHITECTURE

You will manage a persistent state file `hunt-state.md` in the current directory.
This file is the memory across all iterations — it records every attempt, every drop reason, and every lesson learned.

The loop structure:
```
[Phase A] RECON + UNAUDITED DIFF  ← runs ONCE, results reused forever
     ↓
[Phase B] ATTACK SURFACE          ← generates NEW candidates each iteration
     ↓                              using drop history to avoid repeating mistakes
[Phase C] TRIAGE                  ← 4 checks per candidate (code trace, intent,
     ↓                              dup check, impact threshold)
     ↓── all DROP → update state, increment iteration, go back to Phase B
     ↓── any GO  →
[Phase D] PoC VERIFICATION        ← mainnet fork + Python script
     ↓── PoC fails  → update state with WHY, go back to Phase B
     ↓── PoC passes →
[Phase E] REPORT                  ← write and stop
```

---

# INITIALIZATION

Create `hunt-state.md`:

```markdown
# Hunt State

## Target
[target]

## RPC
[rpc]

## Status: HUNTING

## Iteration: 0

## Recon (filled in Phase A)
- Last audit date:
- WONTFIX items:
- In-scope contracts:
- Unaudited files (post-audit):

## Drop History
(empty — first iteration)

## Lessons Learned
(empty — first iteration)

## Active Finding
none
```

---

# PHASE A: RECON + UNAUDITED DIFF (runs once)

Spawn an agent to do the following. Save results into `hunt-state.md` under "Recon":

**Agent task:**
1. Fetch Immunefi scope page for the target. Extract:
   - All in-scope contract addresses and names
   - Exact reward tier impact language (verbatim)
   - Out-of-scope exclusions

2. Find the GitHub repository. Fetch:
   - `audits/WONTFIX.md` — read in full
   - `audits/README.md` or audit index — find most recent audit date
   - List all files in scope directories

3. Find post-audit code:
   - Identify commits / PRs merged after the last audit date
   - List files changed after last audit, sorted by priority (new files > new functions > logic changes)

4. Return a structured summary:
   ```
   LAST_AUDIT_DATE: YYYY-MM-DD
   WONTFIX: [list items]
   IN_SCOPE_CONTRACTS: [list with addresses]
   UNAUDITED_FILES: [prioritized list]
   REWARDS: [exact verbatim tier language]
   ```

Update `hunt-state.md` with this data.

---

# PHASE B–D LOOP

Run iterations until a valid finding is confirmed or max iterations reached.

**For each iteration:**

## Read current state

Read `hunt-state.md`. Note:
- What attack classes have already been tried (and dropped)
- What drop reasons were recorded
- What "lessons learned" have accumulated
- Current iteration number

**This context MUST inform Phase B. Do not repeat dropped attack classes without new evidence.**

## Phase B: Attack Surface → Candidates

Spawn an agent with:
- The recon data from Phase A
- The full drop history and lessons from `hunt-state.md`
- Instruction: generate 3-5 NEW attack scenario candidates, avoiding previously dropped classes

Agent task prompt:
```
Target: [target]
Recon summary: [from hunt-state.md]

Previously dropped candidates and reasons:
[from hunt-state.md drop history]

Generate 3-5 NEW attack scenarios that have NOT been tried yet.
For each candidate:
  - Attack class (be specific: not just "reentrancy" but "reentrancy in X via Y callback")
  - Step-by-step attack scenario
  - Affected function(s) with file:line if known
  - Estimated impact tier

Focus on:
1. Unaudited files listed in recon (post-audit code)
2. Attack scenarios starting from "what can an attacker DO" not "what looks wrong"
3. Cross-component interactions (what happens when contract A calls contract B?)
4. Asymmetric safety (does add/deposit have a guard that remove/withdraw lacks? WHY?)
```

Return candidate list. If agent cannot generate new candidates (all attack surface exhausted), mark iteration as EXHAUSTED.

## Phase C: Triage (all 5 checks, per candidate)

For each candidate, spawn a triage agent:

```
Candidate: [description]
Target contracts: [addresses]
RPC: [rpc]

Run all 5 triage checks:

CHECK 1 — Code trace:
  - Trace exact code path step by step
  - Identify every require/modifier/guard that could block the attack
  - Verdict: PASSABLE / BLOCKED (if blocked, state which guard and line)

CHECK 2 — Intentional design:
  - Find the analogous pattern elsewhere in the same codebase
  - Does function A have a safety mechanism that function B lacks? WHY?
  - Fetch the PR that introduced this code. What do comments say?
  - Verdict: LIKELY_BUG / LIKELY_DESIGN / UNCERTAIN

CHECK 3 — Duplicate check:
  - Search GitHub issues/PRs for this function/pattern
  - Check audit reports (read relevant sections)
  - Check WONTFIX.md
  - Verdict: NOVEL / DUPLICATE / UNCERTAIN

CHECK 4 — Impact threshold:
  - Map to exact Immunefi impact language: [paste verbatim tiers]
  - Is impact realistic, not just theoretical?
  - Verdict: QUALIFIES (tier: X) / BELOW_THRESHOLD

CHECK 5 — Economic feasibility (CRITICAL — projects reject valid bugs on this):
  DO NOT estimate gas costs. Use measured data from fork + read-only on-chain calls:
  - `cast gas-price --rpc-url [rpc]` → real gas price (read-only, safe)
  - `cast call [CHAINLINK_FEED] "latestAnswer()(int256)" --rpc-url [rpc]` → real ETH/USD (read-only)
  - Gas units: from `test_economic_paramSweep()` in poc.t.sol (mainnet FORK, not mainnet)
  Then:
  - gas_cost_usd = gas_units_from_forge × gas_price_wei / 1e18 × eth_usd
  - Find OPTIMAL attack parameters (sweep full valid range, not just obvious case)
  - Profit ratio = value_extracted / gas_cost_usd (must be > 1)
  - Is the attack repeatable? Total extractable = value_per_tx × max_repetitions
  - Build parameter sweep table with MEASURED numbers:
    | Params | Value/tx | Gas from forge | Gas USD | Profit Ratio | Repeatable? |
  - Verdict: PROFITABLE (ratio Nx, measured) / UNPROFITABLE
  - If UNPROFITABLE at ALL param combinations → DROP

Final verdict: GO / DROP (reason: ...) / UNCERTAIN (need: ...)
```

Collect verdicts. If any candidate is GO, proceed to Phase D for that candidate.
If all DROP, record reasons in `hunt-state.md` and proceed to next iteration.
If any UNCERTAIN, note what would resolve it — if resolvable quickly, resolve; otherwise treat as DROP.

## Phase D: PoC Verification (GO candidates only)

Spawn a PoC agent for each GO candidate:

```
Finding: [description]
Contract: [address]
RPC: [rpc]
Attack path: [step-by-step from triage]

Build TWO files:

1. poc.t.sol — Foundry mainnet fork test
   Requirements:
   - Fork at current head: vm.createSelectFork("[rpc]")
   - Use real deployed addresses
   - Use real on-chain holders (find via eth_call or known addresses)
   - CONTROL test: same operation without the bug → must PASS
   - EXPLOIT test: the vulnerability → assert impact
   - Run: forge test --match-contract <Name> --fork-url [rpc] -vv

2. poc.py — Python web3.py on-chain verification
   Requirements:
   - Check vulnerable function selector in deployed bytecode
   - Check no recovery path exists
   - Print block number, balances, TVL
   - Run against mainnet right now

Run both. Report:
  - CONFIRMED: both pass, paste actual output
  - FAILED: explain why (revert reason, wrong assumption, guard hit)
    Include: what the failure reveals about why this might not be a bug
```

If CONFIRMED:
  - Upload both files to gist: `gh gist create poc.t.sol poc.py --desc "..." --public`
  - Update `hunt-state.md`: Status = FOUND, Active Finding = [name], Gist = [url]
  - **EXIT THE LOOP, proceed to Phase E**

If FAILED:
  - Record in `hunt-state.md`: what assumption was wrong, why the attack doesn't work
  - This is a DROP with technical reason — add to drop history
  - Continue loop

## Update hunt-state.md after each iteration

```markdown
## Iteration [N] — [date]

### Candidates tried
- [candidate 1]: DROP — [reason]
- [candidate 2]: DROP — [reason]
- [candidate 3]: GO → PoC FAILED — [technical reason]

### New lessons learned
- [What this iteration taught us about the codebase]
- [What attack classes are now ruled out]
- [What the PoC failure revealed]
```

Increment iteration counter.

---

# PHASE E: REPORT (runs once, after PoC confirmed)

Read `hunt-state.md` for the confirmed finding and gist URL.

Write the final report to `report-[finding-name].md`:

```markdown
# [Title: one sentence bug description]

[One sentence: what is wrong. One sentence: what happens if triggered.]

---

## Vulnerability

### Root cause

[2-3 sentences. Exact code that is wrong and why.]

```solidity
// Paste the vulnerable code (10-20 lines)
```

### Why this is a bug, not intentional design

[Show the analogous safe pattern in the same codebase.
Cite the PR/commit that introduced the gap.
Quote the PR comment if it reveals the oversight.]

### Attack path

1. [step]
2. [step]
3. Result: [quantified impact]

---

## On-chain evidence (block [N], [date])

| Item | Value |
|------|-------|
| Vulnerable contract | `0x...` |
| Balance / TVL at risk | X ETH / $XM |
| Vulnerable selector | `0xXXXXXXXX` |
| Recovery function | None (bytecode confirmed) |
| Real actor for PoC | `0x...` |

---

## Impact

**Severity**: [Critical / High / Medium]
**Immunefi classification**: "[exact verbatim text]"

[1-2 sentences on realistic trigger.]

---

## Proof of Concept

**Gist**: [URL]

```bash
forge test --match-contract <Name> --fork-url [rpc] -vv
python3 poc.py
```

```
[paste actual test output]
```

---

## Fix

```solidity
// Add this line to [function] in [file]:
+ [one-line fix]
```

---

## Due diligence

| Check | Result |
|-------|--------|
| WONTFIX.md | Not listed |
| Post-audit code | Yes — added [date], last audit [date] |
| GitHub issues/PRs | None found |
| Intentional design | No — [reason] |
| In-scope | Yes — [asset name] |
```

Print: **HUNT COMPLETE — [finding name] — report written to report-[name].md**

---

# STOPPING CONDITIONS

Stop the loop if:
1. ✅ **FOUND**: PoC passes → write report, stop
2. ❌ **EXHAUSTED**: N consecutive iterations produce zero new candidates not already tried
3. ⏱️ **MAX**: iteration count exceeds the specified maximum (default 10)

If EXHAUSTED or MAX reached without a finding:
- Print the full `hunt-state.md` as a summary
- Highlight any UNCERTAIN items that could not be resolved — these are candidates for manual investigation
- Suggest: try a different protocol, or wait for new code to be deployed

---

# ANTI-PATTERNS (avoid these)

- **Do not retry a dropped attack class** without new evidence from the codebase
- **Do not assume asymmetry = bug** — always check if there's a technical reason
- **Do not synthetic the PoC** — if you can't find a real on-chain holder, look harder
- **Do not submit a finding where PoC reverts** — a passing PoC is required
- **Do not count UNCERTAIN items as findings** — resolve or discard
