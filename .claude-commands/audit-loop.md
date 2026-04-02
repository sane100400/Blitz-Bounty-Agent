---
description: "Autonomous audit competition loop (Cantina/Sherlock/Code4rena/CodeHawks): generates candidates → triages → PoC → iterates until valid finding confirmed. Speed-optimized for contest windows."
argument-hint: "[contest-url or repo-url] [platform: cantina|sherlock|codearena|codehawks] [optional: max-iterations=10]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, WebFetch, WebSearch, Agent
---

Run the autonomous audit competition hunt loop for: **$ARGUMENTS**

Parse arguments:
- `$1` = contest URL or GitHub repo URL
- `$2` = platform (`cantina`, `sherlock`, `codearena`, `codehawks`) — infer from URL if omitted
- `$3` = max iterations (default: 10)

---

# LOOP ARCHITECTURE

Persistent state in `audit-state.md`. Each iteration learns from previous drops.

```
[Phase A] CONTEST RECON + CODEBASE MAP  ← runs ONCE
     ↓
[Phase B] ATTACK SURFACE                ← NEW candidates each iteration
     ↓                                    (avoids previously dropped classes)
[Phase C] TRIAGE                        ← validity + severity per candidate
     ↓── all DROP  → update state, next iteration
     ↓── any GO    →
[Phase D] PoC VERIFICATION              ← Foundry test (mock or fork)
     ↓── PoC fails → record why, go back to Phase B
     ↓── PoC passes →
[Phase E] REPORT                        ← write platform-specific report, stop
```

---

# INITIALIZATION

Create `audit-state.md`:

```markdown
# Audit Hunt State

## Target
[target]

## Platform
[platform]

## Status: HUNTING

## Iteration: 0

## Recon (filled in Phase A)
- Protocol type:
- Prize pool:
- Contest deadline:
- Severity tiers:
- In-scope contracts:
- Key invariants:
- External dependencies:

## Drop History
(empty — first iteration)

## Lessons Learned
(empty — first iteration)

## Active Finding
none
```

---

# PHASE A: CONTEST RECON + CODEBASE MAP (runs once)

Spawn an agent to do the following. Save results into `audit-state.md` under "Recon".

**Agent task:**
1. Fetch the contest page. Extract:
   - Prize pool, deadline, severity payout structure
   - In-scope file list (exact paths)
   - Out-of-scope exclusions and judging rules
   - Any "admin is trusted" / "known issues" notes

2. Fetch the GitHub repo. Read:
   - `README.md` — protocol overview and architecture
   - Any `/docs` directory
   - NatSpec on all in-scope contracts

3. Build codebase map:
   - Contract inventory (name, role, LOC, entry points)
   - Trust model (permissionless vs privileged functions)
   - Asset flow (where tokens/ETH enter and exit)
   - External dependencies (oracles, DEXs, vaults)

4. Return structured summary:
   ```
   PROTOCOL_TYPE: [AMM/lending/yield/bridge/etc]
   PRIZE_POOL: $X
   DEADLINE: YYYY-MM-DD
   SEVERITY_TIERS: [exact payout structure]
   IN_SCOPE: [file paths]
   EXCLUDED: [OOS rules]
   KEY_INVARIANTS: [list]
   EXTERNAL_DEPS: [list with risk notes]
   COMPLEXITY_HOTSPOTS: [files/functions needing deep attention]
   ```

Update `audit-state.md` with this data.

---

# PHASE B–D LOOP

Run iterations until valid finding confirmed or max iterations reached.

**For each iteration:**

## Read current state

Read `audit-state.md`. Note:
- What attack classes have been tried and dropped
- What specific drop reasons were recorded
- What "lessons learned" accumulated
- Current iteration number

**This context MUST inform Phase B. Do not repeat dropped attack classes.**

## Phase B: Attack Surface → Candidates

Spawn an agent with:
- Recon data from Phase A
- Full drop history and lessons from `audit-state.md`
- Instruction: generate 3-5 NEW attack scenario candidates

Agent prompt:
```
Target: [target]
Platform: [platform]
Recon: [from audit-state.md]

Previously dropped candidates and reasons:
[from audit-state.md drop history]

Generate 3-5 NEW attack scenarios NOT previously tried.
For each:
  - Specific attack class (e.g. "cross-contract reentrancy via ERC4626 callback in Vault.deposit()")
  - Exact file:line of the vulnerable code
  - Step-by-step attack scenario from attacker's perspective
  - Estimated severity (Critical/High/Medium)
  - Why this is a bug, not intentional design

Focus on:
1. Permissionless entry points and callback functions
2. Cross-contract interactions (where trust is assumed but shouldn't be)
3. Economic attacks (rounding, share price, flash loan)
4. The complexity hotspots identified in recon
5. Attack classes NOT yet exhausted from the drop history
```

If agent cannot generate new candidates (surface exhausted), mark EXHAUSTED.

## Phase C: Triage

For each candidate, spawn a triage agent:

```
Candidate: [description]
Platform: [platform]
Severity tiers: [from recon]
In-scope: [file list]

Run all 4 checks:

CHECK 1 — Exploitability:
  - Trace exact code path step by step
  - Identify every require/modifier/guard that could block
  - Verdict: EXPLOITABLE / BLOCKED (which guard, file:line) / UNCERTAIN

CHECK 2 — In scope:
  - Is the affected file in the scope list?
  - Does platform judging exclude this issue type?
  - Verdict: IN_SCOPE / OUT_OF_SCOPE / UNCERTAIN

CHECK 3 — Not known design:
  - Check NatSpec and README for any documentation of this behavior
  - Check if same pattern exists elsewhere with a guard (asymmetry check)
  - Verdict: LIKELY_BUG / KNOWN_DESIGN / UNCERTAIN

CHECK 4 — Severity threshold:
  - Map to platform's severity criteria (verbatim)
  - Does this clearly meet Medium or above?
  - Verdict: QUALIFIES (tier: X) / BELOW_THRESHOLD

Final: GO / DROP (reason) / UNCERTAIN (what would resolve it)
```

If any GO: proceed to Phase D.
If all DROP: record in `audit-state.md`, next iteration.
UNCERTAIN: attempt to resolve quickly; otherwise treat as DROP.

## Phase D: PoC Verification

Spawn a PoC agent for each GO candidate:

```
Finding: [description]
Attack path: [step-by-step from triage]
Platform: [platform]

Build poc.t.sol:

Requirements:
- Determine if code is deployed (check contest README/scope)
  - If NOT deployed: use mock setup in setUp()
  - If deployed: fork at current head
- CONTROL test: normal operation must PASS
- EXPLOIT test: assert the impact explicitly
- Run: forge test --match-contract [Name]_PoC -vv

Report:
  - CONFIRMED: both tests pass, paste output
  - FAILED: exact revert reason, what assumption was wrong
```

If CONFIRMED:
- Write PoC to `poc-[finding-name].t.sol`
- Update `audit-state.md`: Status = FOUND, Active Finding = [name]
- **EXIT LOOP, proceed to Phase E**

If FAILED:
- Record in `audit-state.md`: why it failed, what was wrong
- Treat as DROP with technical reason
- Continue loop

## Update audit-state.md after each iteration

```markdown
## Iteration [N] — [date]

### Candidates tried
- [candidate 1]: DROP — [reason]
- [candidate 2]: DROP — [reason]
- [candidate 3]: GO → PoC FAILED — [technical reason]

### New lessons learned
- [What this iteration taught about the codebase]
- [Attack classes now ruled out]
- [What PoC failure revealed about the actual behavior]
```

Increment iteration counter.

---

# PHASE E: REPORT (runs once, after PoC confirmed)

Read `audit-state.md` for the confirmed finding.

Write `report-[finding-name].md` using the platform-specific format:

## Sherlock format
```markdown
## Summary
[One sentence: what is vulnerable and impact.]

## Vulnerability Detail
[Root cause, exact code, why it's a bug not a design decision.]

```solidity
// The vulnerable code
```

## Impact
[Precise impact. "Attacker can steal X% of deposited Y from Z.sol"]

## Code Snippet
[GitHub permalink]

## Tool Used
Manual Review

## Recommendation
```solidity
// Before / After
```

## Proof of Concept
[Paste test output or describe steps]
```

## Code4rena format
```markdown
## Lines of code
[GitHub permalink]

## Vulnerability details
### Impact
### Proof of Concept
### Tools Used
### Recommended Mitigation Steps
```

## Cantina / CodeHawks
- Use platform submission UI
- Title: `[Severity] ContractName — Short description`

---

Print: **HUNT COMPLETE — [finding name] — report written to report-[name].md**

---

# STOPPING CONDITIONS

1. ✅ **FOUND**: PoC passes → write report, stop
2. ❌ **EXHAUSTED**: N consecutive iterations produce zero new candidates
3. ⏱️ **MAX**: iteration count exceeds maximum (default 10)

If EXHAUSTED or MAX:
- Print full `audit-state.md` as summary
- Highlight UNCERTAIN items for manual investigation
- Note remaining unexplored attack classes

---

# ANTI-PATTERNS

- **Do not retry a dropped attack class** without new evidence
- **Do not submit borderline-Low findings** as Medium — it hurts your reputation
- **Do not skip the control test** — it proves your PoC setup is correct
- **Do not use wrong report format** — judges will penalize or reject
- **Do not count UNCERTAIN as GO** — resolve or drop
