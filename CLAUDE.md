# Bug Hunting Workspace

Smart contract security research. Supports both Immunefi bug bounties and audit competitions.

## Commands

### Immunefi Bug Bounty

#### `/immunefi-hunt [target] [rpc-url]`

Single-run structured hunt on live deployed protocols.
Phases: Recon → Unaudited Diff → Attack Surface → Triage → Mainnet Fork PoC → Report.

```
/immunefi-hunt "https://immunefi.com/bug-bounty/balancer" "https://1rpc.io/eth"
```

**When to use:** Targeting a live protocol on Immunefi. Focuses on post-audit code.

---

#### `/immunefi-loop [target] [rpc-url] [max-iterations]`

Autonomous loop. Runs hunt → drops candidates → feeds reasons back → hunts again.

```
/immunefi-loop "https://immunefi.com/bug-bounty/balancer" "https://1rpc.io/eth" 10
```

**When to use:** Want Claude to iterate autonomously on an Immunefi target (~$2-5/run).

---

### Audit Competition

#### `/audit-hunt [contest-url] [platform] [optional: rpc-url]`

Single-run structured hunt for audit competitions (Cantina, Sherlock, Code4rena, CodeHawks).
Phases: Contest Recon → Codebase Mapping → Attack Surface → Validity Triage → PoC → Platform Report.

```
/audit-hunt "https://cantina.xyz/competitions/..." cantina
/audit-hunt "https://github.com/sherlock-audit/..." sherlock
/audit-hunt "https://code4rena.com/audits/..." codearena
```

**When to use:** Entering an audit competition. Works on fresh (not-yet-deployed) code.

---

#### `/audit-loop [contest-url] [platform] [max-iterations]`

Autonomous loop for audit competitions. Speed-optimized for contest windows.

```
/audit-loop "https://cantina.xyz/competitions/..." cantina 10
```

**When to use:** Want Claude to iterate autonomously on a contest target.

---

### Headless / Background

#### `python3 hunt_loop.py [target] [rpc-url] [max-iterations]`

Fully headless Immunefi loop. Runs `claude -p` subprocess, feeds drop reasons back each iteration.

```bash
python3 hunt_loop.py "balancer" "https://1rpc.io/eth" 10 &
tail -f hunt-logs/*.log
```

State persisted to `hunt-state.json`. Logs in `hunt-logs/`.

---

#### `python3 audit_loop.py [contest-url] [platform] [max-iterations] [optional: rpc-url]`

Fully headless audit competition loop. Autonomous end-to-end: recon → analysis → PoC → report.

```bash
# Background run, tail logs
python3 audit_loop.py "https://audits.sherlock.xyz/contests/123" sherlock 12 &
tail -f audit-logs/*.log

# Foreground run
python3 audit_loop.py "https://code4rena.com/audits/2026-03-foo" codearena 10
```

Key differences from `hunt_loop.py`:
- **Keeps hunting after first finding** — accumulates multiple High/Medium bugs
- **Auto-runs `forge test`** inside each PoC phase — failing tests feed back as drop reasons
- **Generates submission-ready reports** per platform in `audit-reports/`
- **Sherlock auto-submit** — runs `gh issue create` in the contest repo on confirmed findings

State persisted to `audit-state.json`. Logs in `audit-logs/`. Reports in `audit-reports/`.

---

## Key Rules

### Immunefi
1. **Focus on post-audit code.** Anything merged after the last audit = your real target.
2. **Dup check before PoC.** Don't write PoC until Phase 4 triage passes.
3. **PoC = mainnet fork only.** Real addresses, real balances, real state. Control test required.
4. **Two PoC deliverables:** `poc.t.sol` (Foundry fork) + `poc.py` (web3.py on-chain verification).

### Audit Competition
1. **All code is the target.** No audit history filter — prioritize by complexity and value flow.
2. **Duplicates still pay.** Don't over-triage novelty. Focus on validity.
3. **High AND Medium count.** Don't filter out Mediums.
4. **PoC = Foundry mock test is fine.** No mainnet fork required unless code is already deployed.
5. **Report format is platform-specific.** Sherlock = GitHub issue. Code4rena = markdown. Cantina/CodeHawks = platform UI.

### Universal
- **Attack scenario first.** Never start from "this code looks weird."
- **Asymmetry ≠ bug.** Find the technical reason before calling it a bug.

## Directory Layout

```
bounty/
├── CLAUDE.md              ← this file
├── hunt_loop.py           ← headless Immunefi loop orchestrator
├── audit_loop.py          ← headless audit competition loop orchestrator
├── hunt-state.json        ← Immunefi loop state (auto-created)
├── audit-state.json       ← audit loop state (auto-created)
├── hunt-logs/             ← Immunefi iteration logs (auto-created)
├── audit-logs/            ← audit iteration logs (auto-created)
├── audit-reports/         ← submission-ready report files (auto-created)
│   └── summary.md         ← final findings summary
├── poc-forge/
│   └── test/              ← .t.sol PoC files
├── poc/                   ← .py on-chain verification scripts (Immunefi)
└── [protocol]-report/     ← manual report drafts
```

## RPC URLs

- Ethereum: `https://1rpc.io/eth`
- No mainnet testing — fork only (Immunefi rule)
