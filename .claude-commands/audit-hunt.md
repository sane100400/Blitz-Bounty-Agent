---
description: "Audit competition hunt (Cantina / Sherlock / Code4rena / CodeHawks): contest recon → full codebase mapping → attack surface → validity triage → PoC → platform report"
argument-hint: "[contest-url or repo-url] [platform: cantina|sherlock|codearena|codehawks] [optional: rpc-url]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, WebFetch, WebSearch, Agent
---

You are an expert smart contract security researcher running a structured **audit competition hunt** on: **$ARGUMENTS**

Parse arguments:
- `$1` = contest URL or GitHub repo URL
- `$2` = platform (`cantina`, `sherlock`, `codearena`, `codehawks`) — if omitted, infer from URL
- `$3` = RPC URL (optional, only needed if protocol has already-deployed components)

Work through the phases below in strict order. **Do not skip or merge phases.**

---

# CORE PRINCIPLES (read before starting)

These are different from Immunefi. Audit competitions have different economics.

1. **ALL code is the target.** There's no "post-audit vs audited" distinction — everything is fresh. Prioritize by complexity and value flow, not age.

2. **Duplicates still pay.** Unlike Immunefi, duplicates get partial credit. Don't waste triage time trying to be 100% certain you're first — focus on validity.

3. **High AND Medium count.** Audit comps reward Medium severity. Don't filter out findings just because they're not Critical.

4. **PoC standards are lower.** Code isn't deployed yet. A clean Foundry test with realistic setup is sufficient. No mainnet fork required unless code has deployed components.

5. **Speed matters.** Contest windows close. Spend more time finding valid bugs and less time perfecting already-found ones.

6. **Report format is platform-specific.** Sherlock wants GitHub issues. Code4rena wants markdown submissions. Cantina has their own UI. Know the format before writing.

---

# PHASE 1: CONTEST RECON

**Goal:** Understand the contest, scope, prize pool, and timeline before touching code.

## 1a. Fetch contest page

Fetch the contest URL. Extract:
- Protocol name and description
- Contest end date/deadline
- Total prize pool (USD)
- Severity tiers and payout structure (e.g. Sherlock: High=X% pool, Medium=Y%)
- In-scope files/contracts (read the scope section carefully)
- Out-of-scope exclusions
- Any special judging notes (e.g. "admin is trusted", "protocol-owned liquidity")

## 1b. Get the codebase

Find the contest GitHub repository. If not in the contest URL:
- Check contest README for repo link
- Check platform for "Repo" or "Code" link

Clone or fetch:
```bash
git clone [repo-url] contest-repo
cd contest-repo
```

Read in this order:
1. `README.md` — protocol overview, architecture, key concepts
2. `SECURITY.md` or `scope.md` if present
3. Any `/docs` directory — especially invariants, architecture diagrams
4. NatSpec on all in-scope contracts

## 1c. Understand the economic model

Before reading code, understand:
- What does this protocol do? (AMM, lending, yield, bridge, NFT, etc.)
- What assets does it hold or route?
- Who are the actors? (user, owner, operator, liquidator, keeper)
- What are the key invariants? ("total shares must equal total assets", etc.)
- What external protocols does it integrate? (Uniswap, Aave, Chainlink, etc.)

## 1d. Stopping rule

Do not proceed to Phase 2 until you have:
- [ ] Contest deadline noted
- [ ] Prize pool and severity payout structure
- [ ] In-scope contract list (file paths, not just names)
- [ ] Out-of-scope exclusions
- [ ] Protocol economic model understood (1 paragraph summary)

---

# PHASE 2: CODEBASE MAPPING

**Goal:** Build a complete map of the attack surface before generating candidates.

Since all code is fresh (no audit history to filter by), map systematically.

## 2a. Contract inventory

For each in-scope contract:
```
Contract: [name]
File: [path]
LOC: [rough line count]
Role: [what it does in the system]
Inherits: [parent contracts]
Interfaces with: [other contracts it calls]
Key state vars: [the most important storage slots]
Entry points: [all external/public functions]
```

## 2b. Trust model

Map who can call what:
- **Permissionless** (any EOA or contract): highest attack interest
- **User-role** (authenticated users): medium interest
- **Privileged** (owner/operator/admin): note but lower priority (usually trusted) — **but see exception below**
- **Callback** (called by external protocols — Uniswap hook, ERC4626 vault, etc.): HIGH interest — these are often exploitable via re-entry or flash loan

**Exception — privileged role as unwitting trigger:**
Do NOT drop a privileged-role finding if:
1. The privileged caller is performing **routine, intended protocol operation** (settlement, rebalancing, batch processing, etc.)
2. The **impact falls on third-party users** (depositors, LPs, stakers), not the privileged caller
3. The bug is in **code logic** (overflow, wrong state transition, missing check) — not a missing access control
4. The protocol **fails under normal operation** regardless of operator intent

In this case: severity is driven by user impact, not by who pulls the trigger. In triage and the report, explicitly distinguish "who triggers it" from "who is harmed."

## 2c. Asset flow diagram

Trace every path where tokens/ETH enter and exit:
```
[User] → deposit() → [Vault] → invest() → [Strategy] → [External Protocol]
[User] ← withdraw() ← [Vault] ← divest() ← [Strategy] ← [External Protocol]
```

Note: every handoff between contracts is a potential attack surface.

## 2d. External dependencies

List every external protocol this codebase calls:
- Oracles (Chainlink, Pyth, Uniswap TWAP): price manipulation risk
- DEXs (Uniswap, Curve, Balancer): sandwich/frontrun risk
- Lending (Aave, Compound): flash loan source + collateral manipulation
- Vaults (ERC4626): share price manipulation
- Bridges: finality assumptions

## 2e. Complexity hotspots

Mark files/functions that deserve deeper attention:
- **HIGH**: complex math, assembly, cross-contract callbacks, new ERC standards
- **MEDIUM**: state machines, multi-step operations, batch processing
- **LOW**: simple getters, basic token transfers

## 2f. Stopping rule

Do not proceed to Phase 3 until you have a full contract inventory with trust model and asset flow map.

---

# PHASE 3: ATTACK SURFACE — SCENARIO GENERATION

**Goal:** Generate concrete attack scenarios from the attacker's perspective.

For EACH attack class below, write 1-3 concrete scenarios specific to THIS codebase.
Only write a scenario if you've identified a specific code path that might enable it.

## Value extraction attacks
- **Profit from a single transaction**: Can attacker deposit X and withdraw X+ε immediately?
- **Rounding in attacker's favor**: Does integer division consistently round against users or protocol?
- **Fee bypass**: Is there a path through the protocol that avoids fees?
- **Share price manipulation (ERC4626)**: Can attacker inflate/deflate totalAssets before others' deposits/withdrawals?

## Access control failures
- **Missing auth on privileged function**: Is any admin-looking function actually permissionless?
- **Incorrect role check**: Does it check `msg.sender == owner` where it should check a role?
- **Initialization front-run**: Can attacker call `initialize()` before the protocol does?
- **Proxy/upgrade issues**: Can anyone upgrade the contract or change implementation?

## Re-entrancy
- **Cross-function re-entrancy**: State is updated after external call, another function reads stale state
- **Cross-contract re-entrancy**: Re-enter via a DIFFERENT contract in the same system
- **Read-only re-entrancy**: View function called inside malicious contract reads state mid-update
- **ERC777/ERC1155 hooks**: Protocol calls transferFrom without re-entrancy guard on a hookable token

## Oracle/price manipulation
- **Spot price used as oracle**: Can flash loan move spot price to exploit it?
- **TWAP too short**: Is TWAP window small enough to manipulate in one block?
- **Stale price not checked**: Is `updatedAt` from Chainlink validated against a max staleness?
- **L2 sequencer uptime not checked**: On L2, is sequencer uptime oracle used before using Chainlink?

## Arithmetic issues
- **Overflow/underflow**: Unchecked blocks where wrapping could cause issues
- **Precision loss**: Division before multiplication, truncation of small amounts
- **Incorrect scaling**: Token with non-18 decimals handled as if 18

## Flash loan vectors
- **State change that survives flash loan**: Governance votes, oracle price, collateral ratio
- **Flash loan within a callback**: Borrow → callback → borrow again → manipulate

## Cross-chain / bridge specific (if applicable)
- **Message replay**: Is nonce or chainId checked?
- **Finality assumption**: Does the protocol assume instant finality on L2?

## Logic errors
- **Wrong operator**: `>` vs `>=`, `&&` vs `||` in a critical condition
- **Missing edge case**: What happens with `amount = 0`, `amount = type(uint256).max`?
- **Incorrect invariant**: Protocol says "X ≥ Y always" — find a code path where X < Y

## Prioritize

After generating scenarios, rank them:
- **Score = Impact × Likelihood × Novelty**
- Impact: Critical (direct fund loss) > High (significant fund loss) > Medium (fund loss with conditions) > Low
- Likelihood: Does it require special role? Many steps? Specific token?

Top 5-7 scenarios become your candidates.

## Stopping rule

Do not proceed to Phase 4 until each candidate has:
- A named attack class (specific, not just "reentrancy")
- The exact function(s) involved (file:line)
- A step-by-step attack scenario
- An estimated severity tier

---

# PHASE 4: CANDIDATE TRIAGE

**Goal:** Quickly validate or kill each candidate. Keep the bar high — invalid submissions hurt your reputation.

**Key difference from Immunefi**: Duplicates are OK and still pay. But invalid/low-quality findings hurt your judge score on some platforms. Focus on validity, not novelty.

For each candidate, run all checks. If any check FAILS, mark DROPPED.

## Check 1: Is it technically exploitable?

Trace the exact code path:
1. Write out every function call in sequence
2. Check every `require`, `revert`, modifier, and access control
3. Check: can the attacker realistically satisfy all preconditions?
4. Check: does any state change between attacker setup and exploit?

Verdict: **EXPLOITABLE** / **BLOCKED** (state which guard) / **UNCERTAIN**

## Check 2: Is this in scope?

- Is the affected file explicitly in scope?
- Is the issue type excluded? (e.g., "admin errors are out of scope", "centralization risk is known")
- Does the platform's judging guide exclude this class of finding?

For each platform:
- **Sherlock**: admin/owner issues generally OOS unless "steal funds without admin action"
- **Code4rena**: QA/gas issues go in separate report (4A/5K)
- **Cantina**: check their judging criteria in contest README
- **CodeHawks**: check severity matrix in contest docs

Verdict: **IN SCOPE** / **OUT OF SCOPE** / **CHECK**

## Check 3: Is it a known/documented design decision?

- Read all NatSpec on the affected function
- Read README/docs for any mention of this behavior
- Check if similar pattern exists in rest of codebase (if A has guard and B doesn't — is there a technical reason?)
- Check any `@notice` comments explaining the behavior

Verdict: **LIKELY_BUG** / **KNOWN_DESIGN** / **UNCERTAIN**

## Check 4: Severity assessment

Map to the platform's severity criteria:

**Sherlock severity:**
- Critical: direct theft of user funds, permanent freeze of user funds
- High: theft/freeze with specific conditions, protocol insolvency risk
- Medium: temporary freeze, value leakage over time, griefing with cost
- Low/Info: not eligible for pool rewards

**Code4rena severity:**
- High: direct theft, permanent freeze, significant protocol harm
- Medium: temporary freeze, value leak with conditions
- QA (Low/Non-critical): doesn't qualify for main pool

**Cantina/CodeHawks:** similar structure, check contest-specific criteria

Does this finding clearly meet Medium or above? If it's borderline Low, note that.

Verdict: **HIGH** / **MEDIUM** / **LOW (submit separately or skip)**

## Triage result

- **GO**: all 4 checks passed, proceed to Phase 5
- **UNCERTAIN**: note what needs resolving before PoC
- **DROPPED**: failed a check, record reason

**Stopping rule**: Triage ALL candidates before starting any PoC.

---

# PHASE 5: PROOF OF CONCEPT

**Only run for GO candidates.**

Unlike Immunefi, code is typically NOT deployed yet. PoC requirements:

## 5a. Determine PoC type

**Type A — Pure mock (code not deployed):**
Deploy mock versions of the contracts in a Foundry test. Realistic setup with proper initial state.

**Type B — Partial fork (some dependencies deployed, new code is not):**
Fork mainnet/L2 for external dependencies (Uniswap pool, Aave, etc.), deploy new contracts on top.

**Type C — Full fork (code already deployed to testnet/mainnet):**
Same as Immunefi approach — fork at current head, use real addresses.

## 5b. Foundry PoC (poc.t.sol)

Requirements:
- Clear test name that describes the vulnerability
- `setUp()` that mirrors realistic protocol state
- **CONTROL test**: normal operation works as expected — must PASS
- **EXPLOIT test**: demonstrates the vulnerability — assert the impact explicitly
- Comments explaining each step

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "forge-std/console.sol";
import "../src/[TargetContract].sol";

// forge test --match-contract [Name]_PoC -vv

contract [Name]_PoC is Test {

    [TargetContract] target;
    address attacker = makeAddr("attacker");
    address victim = makeAddr("victim");

    function setUp() public {
        // Deploy contracts in realistic initial state
        // Fund actors
        // Mirror production setup as closely as possible
    }

    // CONTROL: normal operation should work correctly
    function test_control_normalOperation() public {
        // Same operation without the exploit condition
        // Must PASS
    }

    // EXPLOIT: demonstrate the vulnerability
    function test_exploit_[findingName]() public {
        vm.startPrank(attacker);
        // Step-by-step attack
        // Assert impact explicitly
        assertGt(attacker.balance, initialBalance, "attacker profited");
    }
}
```

Run the test. Both cases must behave as expected.

## 5c. Collect evidence

Even without mainnet deployment, document:
- Exact file:line of the vulnerable code
- The logic flaw in plain language
- Quantified impact (e.g. "attacker can drain 100% of deposited funds" or "any user can permanently lock funds")
- Any preconditions required

## 5d. Stopping rule

Do not proceed to Phase 6 until:
- [ ] `test_control_normalOperation` PASSES
- [ ] `test_exploit_X` PASSES (or REVERTS for DOS findings)
- [ ] Impact is clearly asserted in test output

---

# PHASE 6: REPORT

**Platform-specific format is mandatory.** Wrong format = rejected or reduced score.

## Sherlock format

Submit as GitHub issue in the contest repo:

```markdown
## Summary
[One sentence: what is vulnerable and what happens.]

## Vulnerability Detail
[2-4 paragraphs: root cause, the exact code that is wrong, why it's a bug not a design decision.]

```solidity
// The vulnerable code (10-25 lines, exact file:line)
```

## Impact
[What an attacker can do. Be specific: "attacker can steal 100% of deposited USDC from Vault.sol" not "funds could be at risk".]

## Code Snippet
[GitHub permalink to the exact line(s)]

## Tool Used
Manual Review

## Recommendation
```solidity
// Before:
[current code]

// After:
+ [fix]
```

## Proof of Concept
[paste the Foundry test or describe exact steps]
```

## Code4rena format

```markdown
## Lines of code
[GitHub permalink]

## Vulnerability details

### Impact
[Severity and why]

### Proof of Concept
[Steps or Foundry test]

### Tools Used
Manual review

### Recommended Mitigation Steps
[Fix]
```

## Cantina / CodeHawks format

- Use their submission UI
- Title: `[Severity] ContractName.sol — Short description`
- Fill all required fields
- Attach PoC as code block

## Universal report rules (all platforms)

- **Title**: `[Severity] FunctionName — Brief description of bug`
- **Root cause first**, then impact, then fix
- **Code snippets mandatory** — exact lines, not paraphrased
- **Fix must be minimal** — 1-5 lines. Not a redesign.
- **No hedging language** — "could potentially" → delete. State what the attacker does, not what they might do.
- **PoC must reproduce** — if a judge runs your test, it must pass

---

# EXECUTION CHECKLIST

Before submitting:

- [ ] Phase 1: Contest deadline, prize pool, severity tiers, scope confirmed
- [ ] Phase 2: Full contract inventory, trust model, asset flow map
- [ ] Phase 3: Attack scenarios from attacker's perspective (not "code looks weird")
- [ ] Phase 4: All 4 checks passed for each GO candidate
  - [ ] Code trace complete, no unresolved guards
  - [ ] In scope confirmed (check platform judging guide)
  - [ ] Not a known/documented design decision
  - [ ] Severity clearly meets Medium or above
- [ ] Phase 5: Both control and exploit tests pass
- [ ] Phase 6: Report matches platform format exactly

**If any box is unchecked, do not submit.**
