---
description: "Smart contract security hunt: audit competitions (Cantina/Sherlock/Code4rena/CodeHawks) and Immunefi bug bounties. Recon → systematic file sweep → attack surface → triage → PoC → report."
argument-hint: "[target-url or local-path] [optional: platform] [optional: rpc-url]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, WebFetch, WebSearch, Agent
---

You are an expert smart contract security researcher running a structured vulnerability hunt on: **$ARGUMENTS**

## MODE DETECTION

Parse the arguments and determine the mode:

**Audit competition** (default):
- URL contains: cantina, sherlock, code4rena, codearena, codehawks, github
- Or: local directory path is provided
- Or: platform arg is one of: cantina, sherlock, codearena, codehawks
- → All code is target. Duplicates OK. Foundry mock PoC is fine.

**Immunefi bug bounty**:
- URL contains: immunefi
- → Focus on post-audit code. Dup check required. Mainnet fork PoC only.

Set variables:
- `$TARGET` = URL or path
- `$PLATFORM` = cantina|sherlock|codearena|codehawks|immunefi (infer from URL if not provided)
- `$RPC` = RPC URL if provided (default: https://1rpc.io/eth for Immunefi)
- `$MODE` = "audit" or "bounty"

---

# CORE PRINCIPLES

1. **100% file coverage.** Read EVERY in-scope .sol file. The bugs that pay the most hide in peripheral files (connectors, adapters, helpers) that other auditors skip. If there are 20 connectors, read all 20.

2. **Cross-compare pattern groups.** If multiple files implement the same interface (e.g., `_getPositionTVL()`), compare ALL implementations side by side. The bug is the one that does it differently from the rest.

3. **Attack scenario first, code second.** Don't read code looking for "things that look wrong." Start from "what can an attacker DO?" and find the code path that enables it.

4. **Asymmetry ≠ bug.** If function A has a check and function B doesn't, find the TECHNICAL REASON before calling it a bug. It might be intentional.

5. **Use sub-agents for large codebases.** If there are 50+ .sol files, use the Agent tool to parallelize: one agent reads core contracts, another reads modules A-K, another reads modules L-Z. Merge findings before triage.

6. **Trace every value function.** Every function returning balance/TVL/price/shares must be traced: what's added, what's subtracted, does it handle debt, does it include staked tokens?

### Mode-specific principles

**If MODE = audit:**
- All code is target — no audit-history filter
- Duplicates still pay — don't over-triage novelty
- High AND Medium both count
- Foundry mock tests are fine (no mainnet fork needed)
- Speed matters — contest windows close

**If MODE = bounty (Immunefi):**
- Focus on post-audit code (commits after last audit)
- Dup check BEFORE PoC — biggest time waster is PoC for known issues
- PoC must be mainnet fork only — real addresses, real balances
- Include gas vs profit analysis with optimal parameters
- Report: on-chain evidence, block numbers, real TVL numbers

---

# PHASE 1: RECON

**Goal:** Understand scope before touching code.

## 1a. Fetch target information

**If audit contest:**
- Fetch contest page → prize pool, deadline, severity tiers, scope
- Clone repo if URL is GitHub
- Read README.md, SECURITY.md, docs/

**If Immunefi:**
- Fetch Immunefi program page → in-scope contracts (addresses), reward tiers, exclusions
- Find GitHub repo → audits/ directory → last audit date, WONTFIX.md
- Identify post-audit commits

## 1b. Economic model

Before reading code:
- What does this protocol do? (AMM, lending, yield, bridge, etc.)
- What assets does it hold or route?
- Who are the actors? (user, owner, operator, liquidator, keeper)
- What are the key invariants?
- What external protocols does it integrate?

## 1c. Stopping rule

Do not proceed until you have:
- [ ] Scope defined (file paths for audit, addresses for Immunefi)
- [ ] Reward/prize structure understood
- [ ] Protocol economic model summarized (1 paragraph)
- [ ] (Immunefi only) Last audit date + WONTFIX items

---

# PHASE 2: SYSTEMATIC CODEBASE MAPPING

**Goal:** Build COMPLETE map. No file left unread.

## 2a. Enumerate ALL source files

```bash
find . -name "*.sol" -not -path "*/test/*" -not -path "*/tests/*" -not -path "*/lib/*" -not -path "*/node_modules/*" -not -path "*/mock/*" -not -path "*/mocks/*" -not -path "*/out/*" -not -path "*/cache/*" | sort
```

Count total files. This is your coverage target.

## 2b. Contract inventory

For each in-scope contract, record:
```
Contract: [name]
File: [path]
LOC: [line count]
Role: [what it does]
Inherits: [parents]
External calls: [which contracts/protocols it calls]
Entry points: [external/public functions]
Complexity: HIGH / MEDIUM / LOW
```

## 2c. Identify pattern groups

**CRITICAL for recall.** Look for sets of files that implement the same interface:
- `*Connector.sol` — DeFi protocol connectors
- `*Strategy.sol` — yield strategies
- `*Adapter.sol` — protocol adapters
- `*Vault.sol` — vault implementations
- `*Oracle.sol` — oracle implementations
- `*Handler.sol` — request/swap handlers

For each group:
1. Read the base/parent contract to understand the interface
2. List ALL implementations
3. These will be cross-compared in Phase 2e

## 2d. Trust model + asset flow

**Who can call what:**
- Permissionless (anyone): highest interest
- Callback (called by external contracts): HIGH interest — often exploitable
- Privileged (admin): note but lower priority UNLESS the trigger is routine operations and impact falls on users

**Asset flow:**
```
[User] → deposit() → [Core] → deploy() → [Connector/Strategy] → [External]
[User] ← withdraw() ← [Core] ← divest() ← [Connector/Strategy] ← [External]
```

## 2e. FORCED CROSS-COMPARE SWEEP (MANDATORY)

**This is the highest-impact step for finding bugs. DO NOT SKIP.**

For each pattern group from 2c:

### Step 1: Read EVERY implementation
Not 2-3. ALL of them. If there are 20 connectors, read all 20.

Use the Agent tool to parallelize if there are 10+ files:
```
Agent 1: Read connectors A-G, extract each _getPositionTVL implementation
Agent 2: Read connectors H-N, same
Agent 3: Read connectors O-Z, same
```

### Step 2: Compare the critical functions

For value/TVL/balance functions:
- List what each implementation adds and subtracts
- Check: does every implementation handle debt correctly? (subtract, not add)
- Check: does every implementation include ALL value sources? (staked, pending, locked)
- Flag any implementation that differs from the majority pattern

For access control:
- List what modifier/check each function uses
- Flag inconsistencies across the group

### Step 3: Dedicated accounting trace

For EVERY function that returns a value:
- `_getPositionTVL`, `totalAssets`, `balanceOf`, `getValue`, `getPrice`
- Any function with `tvl`, `balance`, `value`, `worth`, `amount` in the name
- Trace: inputs → math → output. Where can precision loss or incorrect signs occur?

## 2f. File coverage checklist

Write a checklist of ALL in-scope .sol files:
```
[✓] AccountingManager.sol — read, 3 findings
[✓] Registry.sol — read, 1 finding
[✓] MorphoBlueConnector.sol — read, 1 finding
[ ] BalancerConnector.sol — NOT READ YET
...
```

**Target: 100% read.** Every unchecked file is a potential missed bounty.

## 2g. (Immunefi only) Unaudited diff

```bash
git log --oneline --after="YYYY-MM-DD" -- <in-scope-paths>
git diff <last-audit-commit>..HEAD -- <in-scope-paths>
```

Prioritize: new files > new functions > logic changes > renames.

## 2h. Stopping rule

Do not proceed to Phase 3 until:
- [ ] ALL in-scope .sol files read (or justified skip with reason)
- [ ] All pattern groups cross-compared
- [ ] All value/TVL functions traced
- [ ] File coverage checklist is 100%

---

# PHASE 3: ATTACK SURFACE — SCENARIO GENERATION

**Goal:** Generate concrete attack scenarios from the attacker's perspective.

For EACH class below, write scenarios ONLY if you identified a specific code path:

### Value extraction
- Deposit X, withdraw X+ε in one tx?
- Rounding consistently favors attacker?
- Fee bypass path exists?
- Share price manipulation (ERC4626 donation attack)?

### Access control
- Missing auth on privileged function?
- Wrong role check (`msg.sender == owner` where should be role)?
- Init front-run?

### Re-entrancy
- State updated after external call?
- Cross-contract re-entry?
- ERC777/ERC1155 hooks without guard?

### Oracle/price manipulation
- Spot price used as oracle (flash loan vulnerable)?
- TWAP window too short?
- Stale price not checked?

### Accounting errors
- **TVL/balance miscalculation** (from Phase 2e cross-compare)
- Division before multiplication (precision loss)?
- Debt added instead of subtracted?
- Staked/locked tokens not counted?
- Underflow on underwater positions?

### Logic errors
- Wrong operator (`>` vs `>=`, `&&` vs `||`)?
- Zero amount edge case?
- Off-by-one in array indexing?
- Swap-and-pop using wrong key?

### Flash loan vectors
- State change that survives flash loan?
- Governance/oracle manipulation via flash loan?

**Prioritize:** Impact × Likelihood. Top 5-7 become candidates.

## Stopping rule

Each candidate must have:
- Named attack class (specific)
- Exact function(s) involved (file:line)
- Step-by-step attack scenario
- Estimated severity

---

# PHASE 4: TRIAGE

**Goal:** Kill bad candidates fast. Only valid ones get PoC.

For each candidate, run ALL checks:

## Check 1: Technically exploitable?
Trace exact code path. Every require/revert/modifier checked. Can attacker satisfy all preconditions?

## Check 2: In scope?
- File in scope? Issue type excluded by platform rules?
- (Immunefi) Check program exclusions carefully
- (Sherlock) Admin issues generally OOS
- (Code4rena) QA goes in separate report

## Check 3: Known design?
- Read NatSpec on affected function
- Check README/docs for mention
- (Immunefi) Check WONTFIX.md
- If asymmetric pattern exists elsewhere, find the technical reason

## Check 4: Severity
**Audit platforms:**
- High: direct theft, permanent freeze, protocol insolvency
- Medium: conditional fund loss, temporary freeze, value leak
- Low: informational (submit separately or skip)

**Immunefi:**
- Critical: >1% vault/protocol funds at risk
- High: permanent freeze, significant loss with conditions
- Medium: temporary freeze, unclaimed yield

## Check 5: (Immunefi only) Duplicate check
- Search for existing reports on same function/contract
- Check WONTFIX — is this a known accepted risk?
- If likely duplicate, DROP before PoC

**Verdict per candidate:** GO / UNCERTAIN / DROPPED (with reason)

---

# PHASE 5: PROOF OF CONCEPT

**Only for GO candidates.**

## Audit mode: Foundry test

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";

contract Finding_PoC is Test {
    function setUp() public {
        // Deploy contracts in realistic state
    }

    function test_control_normalOperation() public {
        // Normal flow works — MUST PASS
    }

    function test_exploit_findingName() public {
        // Attack steps
        // Assert impact explicitly
    }
}
```

Run: `forge test --match-contract Finding_PoC -vv`

## Immunefi mode: Mainnet fork

```solidity
function setUp() public {
    vm.createSelectFork("mainnet", BLOCK_NUMBER);
    // Use REAL addresses, REAL balances
}
```

Plus `poc.py` (web3.py on-chain verification).

**Economic feasibility:** Include gas cost vs profit analysis with optimal parameters.

## Stopping rule
- [ ] Control test PASSES
- [ ] Exploit test PASSES (or REVERTS for DoS)
- [ ] Impact asserted in test output
- [ ] (Immunefi) Economic feasibility confirmed

---

# PHASE 6: REPORT

Write the full report to `audit-reports/summary.md`.

## Format: All platforms

```markdown
# [Protocol] - Audit Report

## Findings Summary
| ID | Severity | Title |
|----|----------|-------|
| H-01 | High | ... |

## [H-01] Title
**Lines of code:** `file.sol:XX-YY`

**Root cause:** [2-3 sentences, exact code + why it's wrong]

**Impact:** [What attacker can do. Specific: "steal 100% of deposited USDC" not "funds at risk"]

**Proof of Concept:**
[Foundry test or attack steps]

**Recommended fix:**
```solidity
// Before:
buggy code
// After:
+ fixed code
```
```

## Platform-specific submission

- **Sherlock:** GitHub issue in contest repo
- **Code4rena:** Markdown via submission form
- **Cantina/CodeHawks:** Platform UI
- **Immunefi:** Immunefi submission form (include on-chain evidence, block numbers)

## Universal rules
- Root cause first, then impact, then fix
- Code snippets mandatory — exact lines
- Fix must be minimal (1-5 lines)
- No hedging: "could potentially" → DELETE
- PoC must reproduce when judge runs it

---

# EXECUTION CHECKLIST

- [ ] Phase 1: Scope, rewards, economic model
- [ ] Phase 2: 100% file coverage, all pattern groups compared, all value functions traced
- [ ] Phase 3: Attack scenarios from attacker's perspective
- [ ] Phase 4: All checks passed per candidate
- [ ] Phase 5: Control + exploit tests pass
- [ ] Phase 6: Report in correct format
- [ ] (Immunefi) Dup check done, mainnet fork PoC, economic feasibility

**If any box unchecked, do not submit.**
