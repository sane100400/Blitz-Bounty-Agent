---
description: "Immunefi bug bounty hunt on live deployed protocols: recon → unaudited diff → attack surface → dup+intent triage → mainnet fork PoC → report"
argument-hint: "[immunefi-url or protocol-name] [optional: rpc-url]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, WebFetch, WebSearch, Agent
---

You are an expert smart contract security researcher running a structured Immunefi bug bounty hunt on: **$ARGUMENTS**

Work through the phases below in strict order. **Do not skip or merge phases.** Each phase has a hard stopping rule before you can proceed.

---

# CORE PRINCIPLES (read before starting)

These come from painful real-world lessons. Violating them wastes days.

1. **Attack scenario first, code second.** Don't read code looking for "things that look wrong." Start from "what can an attacker DO?" and find the code path that enables it.

2. **Dup check BEFORE deep verification.** The single biggest time waster is building a perfect PoC for a known issue. Check for duplicates and intentional design at the CANDIDATE stage, not after.

3. **Asymmetry ≠ bug.** If function A has `_returnEth` and function B doesn't, that asymmetry might be intentional. ALWAYS ask: "Is there a technical reason why B doesn't need it?" before calling it a bug.

4. **Focus on post-audit code.** Audited code has been reviewed. New code added after the last audit is your real target. Everything else is a harder fight.

5. **PoC must be real.** Mainnet fork only. No synthetic setups. Use real addresses, real balances, real state. A PoC that requires manufactured conditions is weak.

6. **Report compact, evidence heavy.** Immunefi reviewers see noise. Give them: on-chain addresses, block numbers, real TVL numbers, exact selectors. No hypotheticals.

---

# PHASE 1: RECON

**Goal:** Understand scope, rewards, and audit history before touching any code.

## 1a. Fetch Immunefi scope

Fetch the Immunefi program page for the target. Extract:
- In-scope smart contracts (addresses + names)
- Reward tiers with EXACT impact language (copy verbatim, don't paraphrase)
- Out-of-scope exclusions
- Any special rules

## 1b. Find all audits

Fetch the protocol's GitHub repo. Look for:
- `audits/` directory → list all audit files with dates
- `audits/README.md` or similar index
- `audits/WONTFIX.md` → read in full, these are the known accepted issues
- Git log for latest tagged release or last audit-linked commit

Record:
- Date of MOST RECENT audit
- What was in scope for that audit (which contracts/features)
- All WONTFIX items

## 1c. Stopping rule

Do not proceed to Phase 2 until you have:
- [ ] In-scope contract list with addresses
- [ ] Exact reward tier language (verbatim)
- [ ] Last audit date
- [ ] WONTFIX.md contents

---

# PHASE 2: UNAUDITED DIFF

**Goal:** Find code that has never been reviewed by a paid auditor.

This is your primary hunting ground.

## 2a. Find post-audit commits

```bash
# Get commits after last audit date
git log --oneline --after="YYYY-MM-DD" -- <in-scope-paths>

# Get the diff of all in-scope files changed since last audit
git diff <last-audit-tag-or-commit>..HEAD -- <in-scope-paths>
```

If no git repo is available locally, check GitHub for:
- PRs merged after last audit date
- Commits to in-scope files after last audit date

## 2b. Classify each changed file

For each file modified after the last audit:
- **NEW FILE**: highest priority — zero audit coverage
- **MODIFIED**: note what changed; new logic > refactors > renames
- **RENAMED/MOVED**: usually low value

## 2c. Build the unaudited surface

Create a prioritized list:
1. New files (especially new entry points)
2. New functions added to existing files
3. Significant logic changes (not just variable renames)
4. New parameters added to existing functions

## 2d. Stopping rule

Do not proceed to Phase 3 until you have a list of unaudited code sections, ranked by priority.

---

# PHASE 3: ATTACK SURFACE MAPPING

**Goal:** Define what an attacker CAN DO, before reading the code in detail.

Do not start with "what looks wrong." Start with attack scenarios.

## 3a. For each in-scope contract, identify:

**Who can call what:**
- Permissionless functions (anyone can call)
- Permissioned functions (owner/operator only)
- Callback functions (called by external contracts — vault, router, etc.)

**Where assets flow:**
- ETH entry/exit points (payable functions, receive(), WETH wrap/unwrap)
- Token entry/exit points (transferFrom, transfer, vault settlement)
- Fee collection points

**What invariants must hold:**
- "X should always equal Y"
- "After operation Z, balance should not decrease"
- "Function A and B should produce symmetric results"

**External trust assumptions:**
- What contracts does it call? (Vault, ERC4626, oracle, pool)
- What does it assume about those contracts' behavior?
- What happens if an external contract is malicious or returns unexpected values?

## 3b. Generate attack scenario candidates

For EACH attack class below, write 1-2 concrete scenarios specific to THIS protocol:

- **Value extraction**: Can attacker get more out than they put in?
- **Asymmetric operations**: Do add/remove, deposit/withdraw, wrap/unwrap have symmetric safety? If one path has a safety mechanism the other lacks, why?
- **ETH/WETH handling**: Are all `payable` functions guaranteed to return ETH? Is there any path where ETH gets trapped?
- **Accounting mismatch**: Can two views of the same balance diverge? (internal accounting vs actual token balance)
- **Frontrunning/sandwich**: Are there operations where price/rate changes mid-transaction cause loss?
- **Reentrancy**: Every external call is a reentrancy risk. Which ones have guards? Which don't?
- **Access control**: Are there functions that should be restricted but aren't? Or restricted to wrong role?
- **Flash loan interaction**: Can a flash loan change state that the contract reads and trusts?
- **ERC4626 share price manipulation**: Can total assets be inflated/deflated to affect share price?
- **Recovery mode / emergency paths**: Are emergency paths properly restricted? Do they have same safety as normal paths?

## 3c. Prioritize by impact × likelihood

Score each scenario:
- **Impact**: Critical (>1% vault funds) / High (permanent freeze) / Medium (temp freeze / unclaimed yield)
- **Likelihood**: Does it require attacker investment? User mistake only? Always happens?

Top 3-5 scenarios become your candidates.

## 3d. Stopping rule

Do not proceed to Phase 4 until each candidate has:
- A named attack class
- A step-by-step attack scenario (even if rough)
- An estimated impact level

---

# PHASE 4: CANDIDATE TRIAGE (DUP + INTENT CHECK)

**Goal:** Kill bad candidates FAST. Only verified-novel, verified-real candidates get a PoC.

**This phase is the most important phase. Do not rush it.**

For EACH candidate from Phase 3, run ALL of the following checks. If any check FAILS, mark the candidate DROPPED and move on.

## Check 1: Is it technically exploitable?

Trace the exact code path step by step:
1. Write out every function call in the attack sequence
2. Identify every require/revert that could stop it
3. Check modifiers on each function
4. Verify the attacker can actually trigger this (no role requirement they can't satisfy)

**Special case — privileged role triggers the bug:**
If the vulnerable function requires a privileged role (SETTLER_ROLE, operator, admin, etc.), do NOT automatically drop or downgrade. Ask instead:
- Is the privileged caller performing **routine, intended protocol operation** (not a malicious action)?
- Does the **impact fall on third-party users** (depositors, LPs, etc.), not just the privileged caller themselves?
- Is the bug **in the code logic**, not the access control model? (i.e., fixing permissions wouldn't help)
- Would the protocol **fail under normal operation** regardless of the operator's intent?

If yes to these: the privileged role is a prerequisite to *observing* the failure, not to *causing* it. The root cause is the code, not the caller. In the report, explicitly address this: state that the role is performing routine duties, identify the actual victims (users), and emphasize that no exploit or malicious intent is required. This preempts reviewer downgrade attempts.

If you cannot complete a full code trace without hitting an unresolved question, mark UNCERTAIN and note what you need to verify before proceeding.

## Check 2: Is this intentional design?

This is where most false positives die. Ask:

**a) Find the analogous pattern elsewhere in the SAME codebase.**
- If function A has safety mechanism X and function B doesn't, why?
- Is there a technical reason B doesn't need X? (e.g., B doesn't receive the asset X protects)
- Check: does the same operation in a different context have X? If yes, the absence in B is more suspicious.

**b) Read the PR that introduced this code.**
- What was the PR titled? What was its stated purpose?
- Does the PR author's comment explain the behavior?
- Was a safety mechanism added to some functions but not others IN THE SAME PR? (Classic oversight pattern)

**c) Check if the behavior is documented.**
- NatSpec comments
- Protocol documentation
- README

**Verdict:** If you cannot explain WHY the anomaly is a bug (not a design decision), mark DROPPED.

## Check 3: Duplicate check

Search ALL of the following:

**a) Public audit reports:**
```
- audits/ directory in the repo (read every PDF/MD)
- Any audit report that covers the affected contract
- Specifically: did the last audit cover the exact function/file you're looking at?
```

**b) GitHub:**
```
- Issues: search for the function name, the vulnerability pattern, related keywords
- PRs: search for any recent fix to the affected area
- Commits: look for "fix", "security", "patch" commits to the affected file after the last audit
```

**c) WONTFIX.md:**
```
- Is this behavior explicitly acknowledged?
```

**d) Immunefi disclosed reports (if available):**
```
- Search for protocol name in disclosed Immunefi reports
```

**Verdict:** If ANY of the above finds a match, mark DROPPED.

## Check 4: Impact meets threshold

Map to Immunefi's EXACT impact language (verbatim from Phase 1):
- Does this match "permanent freezing" or "theft" or "temporary freezing"?
- Is the impact realistic? (Not just theoretically possible but requires 10 coincidences)

**Verdict:** If impact doesn't clearly map to a paid tier, mark DROPPED.

## Check 5: Economic feasibility (mandatory — must use real on-chain data)

**This check exists because projects WILL reject valid bugs by claiming "gas costs exceed extracted value."** You must preempt this with MEASURED data, not estimates.

**DO NOT estimate or guess gas costs. You MUST use measured data from fork tests and read-only on-chain calls.**

**Step 1 — Get gas price + native token price (read-only calls, safe on mainnet):**

```bash
# Get current gas price on target chain (read-only, returns wei)
cast gas-price --rpc-url <RPC>

# Get current ETH/native token price from Chainlink (read-only view call)
cast call <CHAINLINK_ETH_USD_FEED> "latestAnswer()(int256)" --rpc-url <RPC>
# Chainlink feeds: ETH/USD 0x639Fe6ab55C921f74e7fac1ee960C0B6293ba612 (Arb)
#                  ETH/USD 0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419 (Mainnet)
```

**Step 2 — Measure gas usage from fork test (NOT from mainnet estimate):**

Gas units come from `test_economic_paramSweep()` in poc.t.sol (see Phase 5). This runs on a mainnet fork, so gas measurement is identical to real execution but with zero on-chain risk.

```
gas_cost_usd = gas_units_from_forge × gas_price_wei / 1e18 × native_token_usd_price
```

**Step 2 — Find optimal attack parameters:**
- If the exploit has tunable parameters (e.g., amount, price, count), identify the full valid range
- Calculate `value_extracted` for each parameter set using the contract's math (do this in Python or Solidity, not mental math)
- Find the combination that MAXIMIZES `value_extracted / gas_cost`
- Do NOT only analyze the "obvious" case — find the attacker's best option

**Step 3 — Calculate total extractable value:**
- Is the attack repeatable? (Is the source depleted per tx, or does it persist like the Beanstalk order case?)
- If repeatable: `total_extractable = value_per_tx × max_repetitions`
- What limits repetitions? (victim balance, order balance, etc.)

**Step 4 — Build the parameter sweep table (from real data):**

All numbers in this table must come from Step 1-3 commands, not from estimates.

```
| Parameter Set | Value Extracted/tx | Gas Cost/tx (measured) | Profit Ratio | Repeatable? |
|---|---|---|---|---|
| [obvious params] | $X | $Y | Xx | Yes/No |
| [optimal params] | $X | $Y | Xx | Yes/No |
| [mid-range params] | $X | $Y | Xx | Yes/No |
```

**Verdict:**
- If profit ratio > 1 at ANY valid parameter combination → PASS
- If profit ratio < 1 at ALL parameter combinations → DROPPED (genuinely not economically feasible)
- If marginal (ratio 1-3x) → note this; still proceed if the attack is repeatable at scale

**Common project rejection patterns to preempt:**
1. "Gas costs exceed value" → They only analyzed worst-case params. Show the optimal ones with measured gas.
2. "No rational user would accept" → Show the offer looks legitimate at the deceptive params.
3. "Value at risk is overstated" → Separate total_at_risk from extractable_per_tx, show both with on-chain numbers.

## Triage result

After all 5 checks, each candidate is:
- **GO**: passed all 5 checks, proceed to Phase 5
- **UNCERTAIN**: one check is unresolved, note what's needed to resolve
- **DROPPED**: failed a check, record the reason (so you don't revisit it)

**Stopping rule:** Do not proceed to Phase 5 for any candidate until Phase 4 is complete for ALL candidates.

---

# PHASE 5: DEEP VERIFICATION (PoC)

**Only run this phase for GO candidates.**

## 5a. Foundry PoC (poc.t.sol)

Requirements (all mandatory):
- Fork Ethereum mainnet (or relevant chain) at current head
- Use REAL deployed contract addresses — no mocks
- Use REAL on-chain actors where possible (use `vm.prank` with real holders)
- Test must have a CONTROL case (same operation without the bug) that succeeds
- Test must have the EXPLOIT case that demonstrates the impact
- Assert the impact explicitly (lost funds, stuck ETH, wrong balance)

**vm cheatcode minimization (mandatory):**
- `vm.mockCall` — **never use**. Replace with minimal stub contracts deployed inline. Mocked calls hide real behavior and weaken the PoC.
- `vm.prank` / `vm.startPrank` — eliminate where possible. If the test contract itself can hold the required role (e.g. `initialize(address(this), address(this))`), do that instead. For distinct actor addresses (user, attacker), deploy separate caller contracts rather than using prank.
- `vm.expectRevert` — replace with `try/catch`. Catch `Error(string)` for string reverts, `Panic(uint256)` for arithmetic panics. This makes the assertion explicit and readable.
- `vm.warp` — **allowed** when the bug requires a time condition (e.g. 24h delay). This is unavoidable for time-locked vulnerabilities.
- Goal: ideally only `vm.warp` remains. Every other cheatcode should have a real-code substitute.

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "forge-std/console.sol";

// forge test --match-contract <Name> --fork-url <RPC> -vv

contract <Name>_PoC is Test {
    // Real deployed addresses
    address constant TARGET = 0x...;
    address constant REAL_USER = 0x...;  // from on-chain state

    function setUp() public {
        vm.createSelectFork("<RPC_URL>");
    }

    // Control: prove the operation itself works correctly
    function test_control_normalCase() public {
        // same operation without the vulnerability
        // must PASS
    }

    // Exploit: prove the vulnerability
    function test_exploit_<finding_name>() public {
        // step-by-step attack
        // assert impact
        assertEq(..., ..., "impact description");
    }

    // Economic feasibility: measure gas across parameter sets
    function test_economic_paramSweep() public {
        // Test multiple parameter combinations, log gas and extracted value for each
        // This produces MEASURED data for the report's economic feasibility table
        uint256[] memory params = new uint256[](3);
        params[0] = /* obvious param */;
        params[1] = /* optimal param */;
        params[2] = /* mid-range param */;

        for (uint256 i = 0; i < params.length; i++) {
            uint256 gasBefore = gasleft();
            // ... execute exploit with params[i] ...
            uint256 gasUsed = gasBefore - gasleft();
            console.log("param=%d  gasUsed=%d  valueExtracted=%d", params[i], gasUsed, /* extracted */);
        }
    }
}
```

Run the test. Both cases must behave as expected before proceeding.

## 5b. Python on-chain verification (poc.py)

Purpose: prove pre-conditions hold on mainnet RIGHT NOW without needing a fork.

Requirements:
- Use `web3.py` only
- Verify: affected contract is deployed, has the vulnerable function (selector check in bytecode), current state confirms the issue is live
- Verify: no recovery mechanism exists (check for sweep/rescue selectors)
- Print block number, relevant balances, TVL, key addresses
- Must run successfully against mainnet

```python
"""
On-chain verification: <Protocol> — <Finding Name>

Confirms on <chain> mainnet:
  1. Vulnerable contract deployed with affected function
  2. No recovery path exists
  3. <Specific preconditions>

Requirements: pip install web3
Run: python3 poc.py
"""

from web3 import Web3

RPC = "https://1rpc.io/eth"  # or appropriate chain RPC

def check_selector_in_bytecode(code: bytes, selector: bytes) -> bool:
    return b'\x63' + selector in code

def main():
    w3 = Web3(Web3.HTTPProvider(RPC))
    block = w3.eth.block_number
    print(f"Block: {block}")

    # Check vulnerable function exists
    # Check no recovery path
    # Print relevant on-chain state

    # ── Economic feasibility (read-only on-chain data + forge gas measurements) ──
    # 1. Get real gas price (read-only call, safe)
    gas_price = w3.eth.gas_price  # wei
    print(f"\nGas price: {gas_price} wei ({gas_price / 1e9:.2f} gwei)")

    # 2. Get native token USD price from Chainlink (read-only view call, safe)
    chainlink_abi = [{"inputs":[],"name":"latestAnswer","outputs":[{"type":"int256"}],"stateMutability":"view","type":"function"}]
    feed = w3.eth.contract(address="0x...", abi=chainlink_abi)  # chain-specific feed
    eth_usd = feed.functions.latestAnswer().call() / 1e8
    print(f"ETH/USD: ${eth_usd:.2f}")

    # 3. Gas units come from forge test output (test_economic_paramSweep on mainnet FORK)
    #    Paste the gas values from forge test logs here — do NOT use estimate_gas on live mainnet
    # gas_units_from_forge = {
    #     "obvious_params": 150000,   # from forge log: "param=X  gasUsed=150000"
    #     "optimal_params": 145000,   # from forge log: "param=Y  gasUsed=145000"
    # }

    # 4. Parameter sweep — value calculation + forge gas → USD cost
    print("\n── Economic Feasibility (gas from fork test, prices from mainnet) ──")
    print(f"{'Params':<20} {'Value/tx':>12} {'Gas (forge)':>12} {'Gas USD':>12} {'Ratio':>8}")
    # for name, gas_units in gas_units_from_forge.items():
    #     value = calculate_extracted(params)  # use contract math
    #     cost = gas_units * gas_price / 1e18 * eth_usd
    #     ratio = value / cost if cost > 0 else float('inf')
    #     print(f"{name:<20} ${value:>11.6f} {gas_units:>11} ${cost:>11.6f} {ratio:>7.1f}x")

if __name__ == "__main__":
    main()
```

Run the script. Must succeed.

## 5c. Collect on-chain evidence

Record (for the report):
- Block number at time of verification
- Contract addresses (target, related contracts)
- Current TVL / balances affected
- Real user addresses involved
- Function selectors (from bytecode analysis if relevant)
- Any relevant transaction hashes

## 5d. Upload to gist

```bash
gh gist create poc.t.sol poc.py \
  --desc "<Protocol> — <Finding Name>: Foundry PoC + on-chain verification" \
  --public
```

Save the gist URL.

## 5e. Stopping rule

Do not proceed to Phase 6 until:
- [ ] `test_control_normalCase` PASSES (confirms setup is correct)
- [ ] `test_exploit_X` PASSES (confirms vulnerability is real)
- [ ] `test_economic_paramSweep` PASSES with measured gas data for 3+ parameter sets
- [ ] `poc.py` runs successfully against mainnet, including economic feasibility output with real gas prices
- [ ] Gist URL recorded

---

# PHASE 6: REPORT

**Goal:** Immunefi 양식에 맞는 리포트. 리뷰어가 10분 안에 검증 가능해야 함.

## Format rules

- **Brief/Intro는 1단락.** 무엇이 문제인지, 악용 시 결과가 무엇인지만.
- **코드 스니펫 필수.** 취약한 라인을 정확히 인용.
- **온체인 증거 필수.** 모든 주장에 블록 번호 또는 주소.
- **가정 금지.** "could potentially" → 삭제. "At block X, balance was Y" → 유지.
- **PoC 섹션은 충분히 상세하게.** Setup 테이블 + 단계별 공격 흐름 + 전체 테스트 출력 + 온체인 검증 출력 모두 포함. 짧으면 Immunefi에서 rejection 경고 발생.
- **Fix는 1-5줄.**
- **파일명:** `poc.t.sol`, `poc.py` (gist에 업로드)

## Report structure

```markdown
# [Title]

## Brief/Intro

[1단락. 무엇이 잘못됐는지 + 악용 시 결과.]

---

## Vulnerability Details

[취약점 상세 설명. 취약한 코드 스니펫 포함.]

```solidity
// 취약한 코드
```

[왜 버그인지, 의도된 설계가 아닌 이유. 동일 코드베이스의 유사 패턴(수정된 함수 등)과 비교.
관련 PR/커밋 인용.]

---

## Impact Details

**Severity**: [Critical / High / Medium]
**Immunefi classification**: "[scope 페이지 verbatim 텍스트]"

[피해 규모 정량화. 블록 번호, 잔액, TVL 포함. 공격자 비용 vs 피해자 손실 명시.]

### Economic Feasibility (measured on-chain)

**This section is mandatory.** It preempts the most common rejection vector ("not economically feasible"). **All numbers below are from `test_economic_paramSweep` (Foundry gas measurement) and `poc.py` (live gas price + Chainlink ETH/USD).**

At block [N], gas price = [X] gwei, ETH/USD = $[Y] (Chainlink feed `0x...`):

| Parameter Set | Value Extracted / tx | Gas Used (measured) | Gas Cost USD | Profit Ratio | Repeatable? |
|---|---|---|---|---|---|
| [worst-case params] | $X | N gas | $Y | Nx | [Yes/No] |
| **[optimal params]** | **$X** | **N gas** | **$Y** | **Nx** | **[Yes/No]** |

**Optimal attack vector:** [1-2 sentences describing the best parameter combination and why it maximizes profit.]

**Total extractable value:** [If repeatable: value_per_tx × max_repetitions. State limiting factor.]

**Net attacker cost:** [Gas total for full attack sequence — setup tx + N exploit txs + cleanup tx. All gas values from forge test output.]

**If the triggering call requires a privileged role**, add a subsection:

### Why the [ROLE_NAME] requirement does not reduce severity

- [ROLE_NAME] is performing **routine, intended protocol operation** — not an adversarial action.
- The **actual victims are [users/depositors/LPs]**, not the privileged caller.
- **No malicious intent or exploit is required.** Normal protocol execution triggers the bug on every deployment.
- The fix is a **code change**, not an access control change. Restricting caller permissions would not resolve the root cause.
- The privileged role is a prerequisite to *observing* the failure, not to *causing* it.

---

## Proof of Concept

**Gist:** [URL] (`poc.t.sol`, `poc.py`)

### Setup

| Role | Address | On-chain state |
|------|---------|----------------|
| Attacker | `0x...` | [실제 잔액] |
| Victim | `0x...` | [실제 보유 자산] |
| Target contract | `0x...` | [TVL 등] |

### Step-by-step

**Step 1 — [행동]**

```solidity
// 실제 호출 코드
```

[내부에서 무슨 일이 일어나는지 설명]

**Step 2 — [행동]**

[계속...]

### Test output

```bash
forge test --match-contract <Name> --fork-url <RPC> -vv
```

```
[PASS] test_control_normalCase()
Logs:
  [실제 출력 붙여넣기]

[PASS] test_exploit_<name>()
Logs:
  [실제 출력 붙여넣기]
```

[control과 exploit 결과가 무엇을 증명하는지 1-2문장.]

### On-chain verification

```bash
python3 poc.py
```

```
[실제 출력 붙여넣기]
```

### Fix

```solidity
// Before
...

// After
+ require(x > 0, "...");
```

---

## References

- [관련 컨트랙트 arbiscan/etherscan 링크]
- [관련 PR/커밋 GitHub 링크]
- [관련 이슈 링크 (해당 시)]
```

---

# EXECUTION CHECKLIST

Before submitting, confirm:

- [ ] Phase 1: Scope, rewards (verbatim), last audit date, WONTFIX confirmed
- [ ] Phase 2: Unaudited diff identified, prioritized
- [ ] Phase 3: Attack scenarios written from attacker's perspective (not "code looks weird")
- [ ] Phase 4: All 5 checks passed for each GO candidate
  - [ ] Code trace complete, no unresolved guards
  - [ ] Intentional design ruled out (checked analogous pattern + PR)
  - [ ] Duplicate search: audit reports, GitHub issues/PRs, WONTFIX
  - [ ] Impact maps to paid tier
  - [ ] Economic feasibility: profit ratio > 1 at optimal params, parameter sweep table ready
- [ ] Phase 5: Both PoC tests pass, poc.py runs on mainnet, gist uploaded
- [ ] Phase 6: Report has on-chain block/address evidence, no hypotheticals

**If any box is unchecked, do not submit.**
