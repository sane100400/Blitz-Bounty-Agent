# Bounty Hunter — AI-Powered Smart Contract Security Automation

Autonomous bug bounty hunting and audit competition tooling powered by Claude Code.  
Finds **valid, PoC-confirmed vulnerabilities** in live protocols (Immunefi) and audit contests (Cantina, Sherlock, Code4rena, CodeHawks).

## Knowledge Cutoff & Model Info

| Field | Value |
|---|---|
| **Model** | Claude Opus 4.6 (1M context) |
| **Knowledge cutoff** | May 2025 |
| **Tooling** | Claude Code CLI with custom slash commands |
| **Benchmark** | EVMBench (OpenAI + Paradigm, 2026-02-18) |
| **Last verified** | 2026-04-02 |

> **Knowledge cutoff note:** The model's Solidity/EVM knowledge covers up to May 2025. EVMBench dataset spans 2023-07 to 2026-01 — some test cases (2025-10, 2026-01) may contain patterns post-cutoff. This is intentional for measuring generalization. Protocols deployed or significantly updated after May 2025 may use patterns the model hasn't seen.

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
│  /web3-hunt       │   │  hunt_loop.py        │
│  /web3-loop       │   │  audit_loop.py       │
└──────┬───────────┘   └──────┬──────────────┘
       │                       │
       ▼                       ▼
┌─────────────────────────────────────────────────────┐
│              Shared Phase Pipeline                    │
│                                                      │
│  Recon → Systematic File Sweep (100% coverage) →    │
│  Cross-Compare → Attack Surface → Triage →          │
│  PoC (Foundry fork / mock) → Report                  │
└─────────────────────────────────────────────────────┘
```

### Two Modes

| Mode | Entry Point | Use Case |
|---|---|---|
| **Interactive** | `/web3-hunt` | Single structured run — auto-detects audit vs bounty |
| **Iterative** | `/web3-loop` | Autonomous loop — coverage gap tracking per iteration |
| **Headless** | `hunt_loop.py`, `audit_loop.py` | Background subprocess loops |

---

## Slash Commands (Skills)

### `/web3-hunt [target] [platform?] [rpc-url?]`

Unified single-run hunt. Auto-detects mode from URL/platform.

**Phases:**
1. **Recon** — Scope, rewards, economic model
2. **Systematic File Sweep** — Enumerate ALL .sol files, 100% coverage target
3. **Cross-Compare** — Pattern groups (connectors, adapters) compared side-by-side
4. **Attack Surface** — Scenario-first, with dedicated accounting/TVL trace
5. **Triage** — Exploitability, scope, known design, severity checks
6. **PoC** — Foundry test (mock for audits, mainnet fork for Immunefi)
7. **Report** — Platform-specific format

```bash
# Audit competitions
/web3-hunt "https://cantina.xyz/competitions/..." cantina
/web3-hunt "https://code4rena.com/audits/..." codearena

# Immunefi bounties
/web3-hunt "https://immunefi.com/bug-bounty/balancer" immunefi "https://1rpc.io/eth"
```

### `/web3-loop [target] [platform?] [max-iterations?] [rpc-url?]`

Autonomous loop with **coverage gap tracking** — each iteration reads files missed previously.

```bash
/web3-loop "https://cantina.xyz/competitions/..." cantina 10
/web3-loop "https://immunefi.com/bug-bounty/balancer" immunefi 10 "https://1rpc.io/eth"
```

- Audit mode: accumulates multiple H/M findings across iterations
- Bounty mode: stops on first confirmed finding
- State persisted to `hunt-state.md`

### Legacy commands

`/immunefi-hunt`, `/immunefi-loop`, `/audit-hunt`, `/audit-loop` still work but `/web3-*` is preferred.

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

Two benchmark tracks: **EVMBench** (official, standardized) and **custom suites**.

### EVMBench (OpenAI + Paradigm)

Industry-standard benchmark for AI smart contract security. 117 vulnerabilities from 40 Code4rena audits.

| Metric | Description |
|---|---|
| **Detect** | Find vulns in a codebase → structured audit report |
| **Patch** | Produce a diff that fixes the bug without breaking tests |
| **Exploit** | Craft transactions that exploit the bug on a local Anvil chain |

**Published scores (2026-02-18):**

| Model | Detect Recall | Detect Award | Exploit |
|---|---|---|---|
| Claude Opus 4.6 | **45.9%** (1st) | **$37,824** (1st) | — |
| GPT-5.3-Codex | — | — | **71.0%** |
| GPT-5.2 | ~lower | $8,106 | 33.3% |

#### Run EVMBench (official harness)

```bash
# One-time setup (clones repo, builds Docker images)
bash benchmark/evmbench_setup.sh

# Run Claude on full detect split
cd evmbench-upstream/project/evmbench
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY \
uv run python -m evmbench.nano.entrypoint \
    evmbench.audit_split=detect-tasks \
    evmbench.mode=detect \
    evmbench.hint_level=none \
    evmbench.log_to_run_dir=True \
    evmbench.solver=evmbench.nano.solver.EVMbenchSolver \
    evmbench.solver.agent_id=claude-opus-4.6 \
    runner.concurrency=3
```

#### Run EVMBench via our skills (custom wrapper)

Feeds EVMBench cases through our `/web3-hunt` skill and scores against ground truth.

```bash
# All detect cases
python3 benchmark/evmbench_skill_runner.py --split detect-tasks

# Single audit
python3 benchmark/evmbench_skill_runner.py --audit 2024-04-noya

# Compare last two runs
python3 benchmark/evmbench_skill_runner.py --compare

# Dry run (validate, no API calls)
python3 benchmark/evmbench_skill_runner.py --dry-run --split detect-tasks
```

### Custom Suites

For additional test cases beyond EVMBench:

```bash
python3 benchmark/run.py --suite known-vulns
python3 benchmark/run.py --compare
```

See `benchmark/config.yaml` for scoring weights and `benchmark/suites/` for test case definitions.

---

## Directory Layout

```
bounty/
├── README.md                       ← this file
├── CLAUDE.md                       ← Claude Code project instructions
├── .gitignore
├── hunt_loop.py                    ← headless Immunefi loop orchestrator
├── audit_loop.py                   ← headless audit competition loop orchestrator
├── .claude-commands/               ← slash command source files
│   ├── web3-hunt.md                ← unified hunt (auto-detects audit vs bounty)
│   ├── web3-loop.md                ← unified loop with coverage tracking
│   └── (legacy: immunefi-*, audit-*)
├── benchmark/                      ← evaluation framework
│   ├── evmbench_setup.sh           ← EVMBench official harness setup
│   ├── evmbench_skill_runner.py    ← skill wrapper for EVMBench cases
│   ├── run.py                      ← custom suite benchmark runner
│   ├── config.yaml                 ← scoring config
│   ├── suites/                     ← custom test case definitions
│   └── results/                    ← benchmark outputs (gitignored)
├── evmbench-upstream/              ← cloned openai/frontier-evals (gitignored)
├── poc-forge/test/                 ← Foundry PoC files
├── poc/                            ← web3.py verification scripts
└── [protocol]-report/              ← per-protocol report drafts
```

---

## Setup

### Prerequisites

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
- [Foundry](https://book.getfoundry.sh/) (`forge`, `cast`, `anvil`)
- Python 3.10+ with [uv](https://docs.astral.sh/uv/) (for EVMBench)
- Docker (for EVMBench official harness)
- `gh` CLI (for Sherlock auto-submit)
- PyYAML (`pip install pyyaml`)

### Install

```bash
git clone https://github.com/sane100400/bounty.git
cd bounty

# Install slash commands
mkdir -p ~/.claude/commands
cp .claude-commands/*.md ~/.claude/commands/

# (Optional) Set up EVMBench
bash benchmark/evmbench_setup.sh
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
