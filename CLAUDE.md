# Bug Hunting Workspace

Smart contract security research. Supports both Immunefi bug bounties and audit competitions via unified `/web3-hunt` and `/web3-loop` skills.

## Commands

### `/web3-hunt [target] [platform?] [rpc-url?]`

Single-run structured hunt. Auto-detects mode (audit vs bounty) from URL/platform.

```bash
# Audit competitions
/web3-hunt "https://cantina.xyz/competitions/..." cantina
/web3-hunt "https://code4rena.com/audits/..." codearena
/web3-hunt "./local-repo" sherlock

# Immunefi bug bounty
/web3-hunt "https://immunefi.com/bug-bounty/balancer" immunefi "https://1rpc.io/eth"
```

Phases: Recon → Systematic File Sweep (100% coverage target) → Cross-Compare → Attack Surface → Triage → PoC → Report.

---

### `/web3-loop [target] [platform?] [max-iterations?] [rpc-url?]`

Autonomous loop. Each iteration covers files missed in previous iterations.

```bash
/web3-loop "https://cantina.xyz/competitions/..." cantina 10
/web3-loop "https://immunefi.com/bug-bounty/balancer" immunefi 10 "https://1rpc.io/eth"
```

Key features:
- Coverage gap tracking — each iteration reads unanalyzed files
- Drop reasons feed back to avoid repeating mistakes
- Audit mode: accumulates multiple findings
- Bounty mode: stops on first confirmed finding

---

### Legacy commands (still available)

`/immunefi-hunt`, `/immunefi-loop`, `/audit-hunt`, `/audit-loop` — same functionality, use `/web3-hunt` and `/web3-loop` instead.

---

### Headless / Background

```bash
python3 hunt_loop.py "balancer" "https://1rpc.io/eth" 10 &
python3 audit_loop.py "https://audits.sherlock.xyz/contests/123" sherlock 12 &
```

---

## Key Rules

### Universal
- **100% file coverage.** Read EVERY in-scope .sol file. Peripheral files hide the best bugs.
- **Cross-compare pattern groups.** 20 connectors implementing the same interface? Compare all 20.
- **Attack scenario first.** Never start from "this code looks weird."
- **Asymmetry ≠ bug.** Find the technical reason before calling it a bug.
- **Trace every value function.** TVL, balance, price, shares — trace the math.
- **Economic feasibility.** Include gas vs profit analysis with optimal parameters.

### Bounty (Immunefi)
- Focus on post-audit code
- Dup check before PoC
- PoC = mainnet fork only + web3.py verification

### Audit Competition
- All code is target
- Duplicates still pay
- High AND Medium count
- Foundry mock PoC is fine

## Directory Layout

```
bounty/
├── CLAUDE.md                ← this file
├── .claude-commands/        ← slash command source files
│   ├── web3-hunt.md         ← unified hunt skill
│   ├── web3-loop.md         ← unified loop skill
│   └── (legacy skills)
├── hunt_loop.py             ← headless Immunefi loop
├── audit_loop.py            ← headless audit loop
├── benchmark/               ← EVMBench evaluation framework
├── poc-forge/test/          ← Foundry PoC files
├── poc/                     ← web3.py verification scripts
└── audit-reports/           ← generated reports
```

## RPC URLs

- Ethereum: `https://1rpc.io/eth`
- No mainnet testing — fork only (Immunefi rule)
