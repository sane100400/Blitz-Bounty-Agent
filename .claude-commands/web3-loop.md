---
description: "Autonomous smart contract security loop: hunts until valid findings confirmed. Feeds coverage gaps + drop reasons back each iteration. Supports audit competitions and Immunefi."
argument-hint: "[target-url or path] [optional: platform] [optional: max-iterations=10] [optional: rpc-url]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, WebFetch, WebSearch, Agent
---

Run the autonomous vulnerability hunt loop for: **$ARGUMENTS**

Parse arguments:
- `$1` = target (URL, repo URL, or local path)
- `$2` = platform (cantina|sherlock|codearena|codehawks|immunefi — infer from URL if omitted)
- `$3` = max iterations (default: 10)
- `$4` = RPC URL (optional, default: https://1rpc.io/eth for Immunefi)

Detect mode:
- **Audit** if platform is cantina/sherlock/codearena/codehawks or URL/path suggests contest
- **Bounty** if platform is immunefi

---

# LOOP ARCHITECTURE

Persistent state in `hunt-state.md`. Each iteration learns from previous drops AND coverage gaps.

```
[Phase A] RECON + FULL CODEBASE MAP     ← runs ONCE
     ↓
[Phase B] COVERAGE GAP CHECK            ← which files haven't been analyzed?
     ↓
[Phase C] ATTACK SURFACE                ← NEW candidates from unanalyzed code
     ↓                                    + previously dropped classes with new angle
[Phase D] TRIAGE                        ← validity + severity per candidate
     ↓── all DROP  → update state, next iteration
     ↓── any GO    →
[Phase E] PoC VERIFICATION              ← Foundry test (mock or fork)
     ↓── PoC fails → record why, go back to Phase C
     ↓── PoC passes →
[Phase F] REPORT                        ← write to audit-reports/summary.md
     ↓── (Audit mode) KEEP HUNTING for more findings
     ↓── (Bounty mode) STOP on first confirmed finding
```

**Key difference from single-shot /web3-hunt:**
- Iteration 1: broad sweep, find obvious issues
- Iteration 2+: cover FILES that weren't read in previous iterations
- Each iteration narrows the gap toward 100% file coverage

---

# INITIALIZATION

Create `hunt-state.md`:

```markdown
# Web3 Hunt State

## Target: [target]
## Platform: [platform]
## Mode: [audit|bounty]
## Status: HUNTING
## Iteration: 0

## Recon
(filled in Phase A)

## File Coverage
(filled in Phase A — checklist of ALL .sol files)

## Confirmed Findings
(findings with passing PoCs)

## Drop History
(empty)

## Lessons Learned
(empty)
```

---

# PHASE A: RECON + CODEBASE MAP (runs once)

Run `/web3-hunt` Phase 1 + Phase 2 logic:

1. Fetch scope, rewards, economic model
2. Enumerate ALL .sol files
3. Build contract inventory, trust model, asset flow
4. Identify pattern groups (Connectors, Strategies, etc.)

**CRITICAL: Build the file coverage checklist.**
List every in-scope .sol file with status:
```
[ ] contracts/core/AccountingManager.sol
[ ] contracts/core/Registry.sol
[ ] contracts/connectors/AaveConnector.sol
[ ] contracts/connectors/BalancerConnector.sol
... (ALL files)
```

Save to `hunt-state.md`.

---

# PHASE B–E LOOP

For each iteration:

## Phase B: Coverage Gap Check

Read `hunt-state.md`. Check the file coverage list.

**Calculate:** How many files are still [ ] (unread)?

Priority for this iteration:
1. **Unread files in pattern groups** (highest priority — these have the best bug density)
2. **Unread files with value/TVL functions**
3. **Remaining unread files**
4. **Re-examine files where previous candidates were UNCERTAIN**

Tell the attack surface agent which specific files to focus on.

## Phase C: Attack Surface → Candidates

Spawn an agent with:
- Recon data from Phase A
- **Specific files to read this iteration** (from Phase B gap check)
- Full drop history and lessons
- Instruction: read the assigned files, generate 3-5 NEW candidates

```
COVERAGE FOCUS: Read these files that haven't been analyzed yet:
[list from Phase B]

ALSO review drop history:
[from hunt-state.md]

MANDATORY CHECKS on each new file:
- External call verification: verify return value semantics, token receivers, function variants
- Position lifecycle: trace all add/remove/update calls, verify boolean flags and position IDs
- Fund flow: map cross-contract token paths, check access control on token transfer functions
- Token edge cases: blacklist tokens in loops, fee-on-transfer, oracle decimal scaling

Generate 3-5 NEW attack scenarios.
For each: attack class, file:line, step-by-step, severity, why it's a bug.
```

**After agent returns:** Update file coverage checklist — mark analyzed files as [✓].

## Phase D: Triage

For each candidate, run checks:
1. Technically exploitable? (trace code path)
2. In scope? (platform rules)
3. Not known design? (check NatSpec, docs, WONTFIX)
4. Meets severity threshold?
5. (Bounty only) Not a duplicate?

Verdict: GO / DROP / UNCERTAIN

## Phase E: PoC Verification

For GO candidates, build Foundry test:
- Control test (normal operation) must PASS
- Exploit test (attack) must PASS
- (Bounty) Must be mainnet fork with real addresses
- (Bounty) Include gas vs profit analysis

If CONFIRMED:
- Add to Confirmed Findings in `hunt-state.md`
- Write/append to `audit-reports/summary.md`
- **Audit mode:** Continue hunting (accumulate more findings)
- **Bounty mode:** Stop — proceed to Phase F

If FAILED:
- Record why, treat as DROP
- Continue loop

## Update hunt-state.md

After each iteration:
```markdown
## Iteration [N] — [date]
Files analyzed this iteration: [list]
Coverage: [X/Y files] ([Z%])
Candidates: [list with GO/DROP status]
New lessons: [what this iteration taught]
```

---

# PHASE F: REPORT

**Audit mode:** Write ALL confirmed findings to `audit-reports/summary.md` using platform format.

**Bounty mode:** Write single finding report with:
- On-chain evidence (addresses, block numbers)
- Mainnet fork PoC
- Economic feasibility analysis
- Gas vs profit with optimal parameters

---

# STOPPING CONDITIONS

1. ✅ **FOUND** (bounty): First PoC passes → report, stop
2. ✅ **COVERAGE COMPLETE** (audit): 100% files read + no new candidates → report all findings, stop
3. ❌ **EXHAUSTED**: N consecutive iterations produce zero new candidates from unread files
4. ⏱️ **MAX**: iteration count exceeds maximum

On stop, print summary:
- Total files: X, Analyzed: Y (Z%)
- Findings: [count] confirmed, [count] dropped
- Total iterations used

---

# ANTI-PATTERNS

- **Do not re-read files already marked [✓]** unless specifically looking for a new attack class
- **Do not retry dropped candidates** without new evidence from newly-read files
- **Do not skip coverage gap check** — it's the main mechanism for improving recall
- **Do not submit borderline-Low as Medium** — hurts reputation
- **Do not count UNCERTAIN as GO** — resolve or drop
