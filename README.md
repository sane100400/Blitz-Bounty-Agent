# Bounty Hunter — AI-Powered Smart Contract Security Automation

Autonomous bug bounty hunting and audit competition tooling powered by Claude Code.  
Finds **valid, PoC-confirmed vulnerabilities** in live protocols (Immunefi) and audit contests (Cantina, Sherlock, Code4rena, CodeHawks).

## Knowledge Cutoff & Model Info

| Field | Value |
|---|---|
| **Model** | Claude Opus 4.6 (1M context) |
| **Knowledge cutoff** | May 2025 |
| **Tooling** | Claude Code CLI with custom slash commands |
| **Last verified** | 2026-04-02 |

> The model's Solidity/EVM knowledge covers up to May 2025. Protocols deployed or significantly updated after this date may use patterns the model hasn't seen. Always supplement with current docs.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                  User / CI / Cron                    │
└──────────┬──────────────────────┬───────────────────┘
           │ interactive          │ headless
           ▼                      ▼
┌──────────────────┐   ┌─────────────────────┐
│  Slash Commands   │   │  Python Orchestrators│
│  (Claude Code)    │   │  (subprocess loops)  │
│                   │   │                      │
│  /immunefi-hunt   │   │  hunt_loop.py        │
│  /immunefi-loop   │   │  audit_loop.py       │
│  /audit-hunt      │   │                      │
│  /audit-loop      │   │                      │
└──────┬───────────┘   └──────┬──────────────┘
       │                       │
       ▼                       ▼
┌─────────────────────────────────────────────────────┐
│              Shared Phase Pipeline                    │
│                                                      │
│  Recon → Scope/Diff → Attack Surface → Triage →     │
│  PoC (Foundry fork / mock) → Report                  │
└─────────────────────────────────────────────────────┘
```

### Two Modes

| Mode | Entry Point | Use Case |
|---|---|---|
| **Interactive** | `/immunefi-hunt`, `/audit-hunt` | Single structured run inside Claude Code session |
| **Headless loop** | `hunt_loop.py`, `audit_loop.py` | Autonomous iteration — feeds drop reasons back each cycle |

---

## Slash Commands (Skills)

### `/immunefi-hunt [target] [rpc-url]`

Single-run structured hunt on a **live Immunefi** target.

**Phases:**
1. **Recon** — Fetch scope, rewards, audit history, WONTFIX
2. **Unaudited Diff** — Find post-audit code changes (primary attack surface)
3. **Attack Surface** — Scenario-first mapping (10 attack classes)
4. **Triage** — 4-check gate: code trace → intent check → dup check → impact threshold
5. **PoC** — Mainnet fork only (Foundry `.t.sol` + web3.py verification)
6. **Report** — On-chain evidence, block numbers, real TVL, gas vs profit analysis

```bash
/immunefi-hunt "https://immunefi.com/bug-bounty/balancer" "https://1rpc.io/eth"
```

### `/immunefi-loop [target] [rpc-url] [max-iterations]`

Autonomous loop wrapper. Runs `/immunefi-hunt` repeatedly, learning from each dropped candidate.

- State persisted to `hunt-state.md`
- Recon runs once, reused across iterations
- Drop reasons feed back to avoid repeating mistakes

```bash
/immunefi-loop "https://immunefi.com/bug-bounty/balancer" "https://1rpc.io/eth" 10
```

### `/audit-hunt [contest-url] [platform] [rpc-url?]`

Single-run hunt for **audit competitions**. All code is target (no audit-history filtering).

**Platforms:** `cantina`, `sherlock`, `codearena`, `codehawks`

**Key differences from Immunefi:**
- Duplicates still pay → less aggressive dup triage
- Mediums count → don't filter by severity
- Foundry mock tests are acceptable (no mainnet fork required)
- Platform-specific report format

```bash
/audit-hunt "https://cantina.xyz/competitions/..." cantina
/audit-hunt "https://audits.sherlock.xyz/contests/123" sherlock
```

### `/audit-loop [contest-url] [platform] [max-iterations]`

Autonomous loop for contests. Speed-optimized for contest windows.

- Keeps hunting after first finding (accumulates multiple H/M)
- Auto-runs `forge test` in PoC phase
- Generates submission-ready reports in `audit-reports/`
- Sherlock: auto-submits via `gh issue create`

```bash
/audit-loop "https://cantina.xyz/competitions/..." cantina 10
```

---

## Headless Scripts

For background/CI execution without an interactive Claude Code session.

### `hunt_loop.py`

```bash
# Background run
python3 hunt_loop.py "https://immunefi.com/bug-bounty/balancer" "https://1rpc.io/eth" 10 &
tail -f hunt-logs/*.log
```

- Runs `claude -p` as subprocess per iteration
- State: `hunt-state.json` + `hunt-logs/`
- Cost: ~$2-5 per full run

### `audit_loop.py`

```bash
python3 audit_loop.py "https://audits.sherlock.xyz/contests/123" sherlock 12 &
tail -f audit-logs/*.log
```

- State: `audit-state.json` + `audit-logs/`
- Reports: `audit-reports/`
- Auto-runs `forge test` for each PoC

---

## Key Design Principles

1. **Attack scenario first, code second.** Never start from "this code looks weird." Start from "what can an attacker DO?"
2. **Triage before PoC.** Don't waste time on PoC for known issues or intentional design.
3. **Asymmetry ≠ bug.** Always find the technical reason before reporting.
4. **PoC = proof, not theory.** Mainnet fork (Immunefi) or Foundry mock (audits). Real state, real balances.
5. **Economic feasibility matters.** Every report includes gas vs profit analysis with optimal parameters.
6. **Drop reasons are knowledge.** Each failed candidate teaches the next iteration what to avoid.

---

## Benchmarking

See [`benchmark/`](./benchmark/) for the evaluation framework.

The benchmark system measures:
- **Precision** — What % of reported findings are valid?
- **Recall** — What % of known bugs does it find?
- **Severity accuracy** — Does it correctly classify H/M/L?
- **Cost efficiency** — $ spent per valid finding

Run benchmarks:
```bash
python3 benchmark/run.py --suite known-vulns
python3 benchmark/run.py --suite audit-contests --model claude-opus-4-6
```

---

## Directory Layout

```
bounty/
├── README.md                ← this file
├── CLAUDE.md                ← Claude Code project instructions
├── .gitignore
├── hunt_loop.py             ← headless Immunefi loop orchestrator
├── audit_loop.py            ← headless audit competition loop orchestrator
├── benchmark/               ← evaluation framework
│   ├── run.py               ← benchmark runner
│   ├── config.yaml          ← test suites & scoring config
│   ├── suites/              ← test case definitions
│   │   └── known-vulns.yaml ← known vulnerability test cases
│   └── results/             ← benchmark outputs (gitignored)
├── poc-forge/test/          ← Foundry PoC files
├── poc/                     ← web3.py verification scripts
└── [protocol]-report/       ← per-protocol report drafts
```

---

## Setup

### Prerequisites

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
- [Foundry](https://book.getfoundry.sh/) (`forge`, `cast`, `anvil`)
- Python 3.10+
- `gh` CLI (for Sherlock auto-submit)

### Install

```bash
git clone https://github.com/<your-username>/bounty.git
cd bounty

# Slash commands are in ~/.claude/commands/ — copy if needed
cp -r .claude-commands/* ~/.claude/commands/ 2>/dev/null || true
```

---

## Cost Estimates

| Command | Typical Cost | Notes |
|---|---|---|
| `/immunefi-hunt` | $0.50–2.00 | Single structured run |
| `/immunefi-loop` (10 iter) | $2–5 | Recon cached after first iter |
| `/audit-hunt` | $0.50–2.00 | Single structured run |
| `/audit-loop` (10 iter) | $2–5 | Contest-optimized |

---

## License

MIT
