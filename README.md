# Blitz Bounty Agent

Autonomous smart contract security agent powered by Claude Code.
Finds **valid, PoC-confirmed vulnerabilities** in audit contests and live bug bounty programs.

## Model & Benchmark

| Field | Value |
|---|---|
| **Model** | Claude Opus 4.6 (1M context) |
| **Knowledge cutoff** | May 2025 |
| **Benchmark** | EVMBench (OpenAI + Paradigm) |
| **Post-cutoff recall** | **100%** (4/4 vulns on 2026-01 audits) |
| **Pre-cutoff recall** | 83% (10/12 vulns on 2023–2024 audits) |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                      User / CI / Cron                            │
└─────┬──────────────────┬───────────────────┬─────────────────────┘
      │ interactive       │ headless           │ multi-agent
      ▼                  ▼                    ▼
┌────────────────┐ ┌──────────────────┐ ┌────────────────────────┐
│ Slash Commands  │ │ Loop Orchestrators│ │ Parallel Orchestrator  │
│ (Claude Code)   │ │ (subprocess)      │ │ (ThreadPoolExecutor)   │
│                 │ │                   │ │                        │
│ /web3-hunt      │ │ hunt_loop.py      │ │ audit_orchestrator.py  │
│ /web3-loop      │ │ audit_loop.py     │ │  → 5-7 specialist     │
│                 │ │                   │ │    agents in parallel  │
└──────┬─────────┘ └──────┬───────────┘ └──────┬─────────────────┘
       │                   │                    │
       ▼                   ▼                    ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Shared Phase Pipeline                          │
│                                                                  │
│  1. Recon (scope, rewards, invariants)                           │
│  2. Systematic File Sweep (100% .sol coverage)                   │
│  3. Cross-Compare (pattern groups side-by-side)                  │
│  4. Attack Surface (scenario-first, TVL/value trace)             │
│  5. Triage (exploitability, severity, known-design filter)       │
│  6. PoC (Foundry mock for audits / mainnet fork for bounties)   │
│  7. Report (platform-specific format)                            │
└──────────────────────────────────────────────────────────────────┘
```

### Three Execution Modes

| Mode | Entry Point | How It Works |
|---|---|---|
| **Interactive** | `/web3-hunt`, `/web3-loop` | Claude Code slash commands — user runs in terminal |
| **Headless** | `hunt_loop.py`, `audit_loop.py` | `claude -p` subprocesses — runs in background/CI |
| **Multi-Agent** | `audit_orchestrator.py` | Parallel specialist agents — pure Python recon + LLM analysis + merge |

### Two Hunt Types

| | Audit Competition | Immunefi Bounty |
|---|---|---|
| **Platforms** | Cantina, Sherlock, Code4rena, CodeHawks | Immunefi |
| **Scope** | All in-scope code | Post-audit changes (unaudited diffs) |
| **Duplicates** | Pay — find everything | Deduplicate before PoC |
| **PoC** | Foundry mock | Mainnet fork only (Immunefi rule) |
| **Goal** | High AND Medium count | First valid critical/high |

---

## Skill System (`.claude-commands/`)

Skills are markdown files defining structured prompts for Claude Code slash commands.

### `/web3-hunt [target] [platform?] [rpc-url?]`

Single-run structured hunt. Auto-detects audit vs bounty from URL/platform.

```bash
# Audit competitions
/web3-hunt "https://cantina.xyz/competitions/..." cantina
/web3-hunt "https://code4rena.com/audits/..." codearena

# Immunefi bounties
/web3-hunt "https://immunefi.com/bug-bounty/balancer" immunefi "https://1rpc.io/eth"
```

**Internal phases** (defined in `.claude-commands/web3-hunt.md`, 537 lines):

| Phase | What it does |
|---|---|
| Recon | Fetch scope, rewards, invariants, README |
| File Sweep | Enumerate ALL .sol files, read every one |
| Cross-Compare | Same-interface implementations compared in table |
| Value Trace | Every TVL/balance/price function traced input→math→output |
| External Protocol Verification | Token destination, return value units, function variant selection |
| Position Lifecycle | Track every add/remove position call with boolean flag audit |
| Fund Flow | Cross-contract token paths, flash loan surfaces, fee-on-transfer edges |
| Triage | 5-gate filter: exploitable → in-scope → not-known-design → severity → economic |
| PoC | Foundry test with concrete attack scenario |
| Report | Structured markdown to `audit-reports/summary.md` |

### `/web3-loop [target] [platform?] [max-iterations?] [rpc-url?]`

Autonomous iterative loop. Each iteration feeds **coverage gaps** and **drop reasons** back as lessons.

```bash
/web3-loop "https://cantina.xyz/competitions/..." cantina 10
/web3-loop "https://immunefi.com/bug-bounty/balancer" immunefi 10 "https://1rpc.io/eth"
```

- Tracks which files were read vs missed per iteration
- Accumulates drop history (`reason: known design`, `reason: not exploitable`, etc.)
- Audit mode: keeps hunting across iterations, accumulates H/M findings
- Bounty mode: stops on first confirmed finding
- State persisted to `hunt-state.md`

### Legacy commands

`/immunefi-hunt`, `/immunefi-loop`, `/audit-hunt`, `/audit-loop` still work but `/web3-*` is preferred.

---

## Headless Orchestrators

For background/CI execution — no interactive Claude Code session needed. Each invokes `claude -p` as a subprocess and parses structured signals.

### `hunt_loop.py` (Immunefi)

```bash
python3 hunt_loop.py "https://immunefi.com/bug-bounty/balancer" "https://1rpc.io/eth" 10
```

- **Signals:** `HUNT_SIGNAL:FOUND:`, `HUNT_SIGNAL:DROP:`, `HUNT_SIGNAL:CONTINUE`, `HUNT_SIGNAL:EXHAUSTED`
- **State:** `hunt-state.json` — iterations, drop history, PoC failures, lessons learned
- **Logs:** `hunt-logs/` — timestamped JSON per iteration
- **Economic checks:** Uses `cast` for gas price + Chainlink ETH/USD feed

### `audit_loop.py` (Contests)

```bash
python3 audit_loop.py "https://audits.sherlock.xyz/contests/123" sherlock 12
```

- **Signals:** `AUDIT_SIGNAL:FOUND:`, `AUDIT_SIGNAL:DROP:`, `AUDIT_SIGNAL:RECON_DONE`, `AUDIT_SIGNAL:REPORT:`
- **State:** `audit-state.json` — findings, drops, PoC results
- **Reports:** `audit-reports/` — platform-specific format
- **Auto-submit:** Sherlock findings via `gh issue create`

### `audit_orchestrator.py` (Multi-Agent)

```bash
python3 audit_orchestrator.py ./protocol-source --platform codearena --timeout 900
```

Parallel specialist architecture for deep analysis:

```
Phase 1: Pure Python Recon (no LLM tokens)
  → File map, pattern detection, external protocol identification

Phase 2: 5-7 Parallel Specialist Agents (ThreadPoolExecutor, max 5 workers)
  ├── TVL/Accounting Agent (split across connectors)
  ├── Position Lifecycle Agent
  ├── Access Control + Fund Flow Agent
  ├── External Protocol Semantics Agent
  └── Core Logic Agent

Phase 3: Merger Agent
  → Deduplicates, triages, produces final report
```

- Budget caps: $0.50/specialist, $0.80/merger
- Token tracking with per-agent cost breakdown

---

## Benchmarking

### EVMBench (OpenAI + Paradigm)

Industry-standard benchmark: 120 vulnerabilities across 40 Code4rena audits.

#### Our Results

**Post-cutoff audits (2026-01, never seen in training):**

| Audit | Vulns | Detected | Recall | Cost |
|---|---|---|---|---|
| tempo-feeamm | 1 | 1 | **100%** | $0.79 |
| tempo-mpp-streams | 1 | 1 | **100%** | $0.61 |
| tempo-stablecoin-dex | 2 | 2 | **100%** | $0.33 |
| **Total** | **4** | **4** | **100%** | **$1.74** |

All findings confirmed by LLM-as-Judge (Haiku) with 95-100% confidence.

**Pre-cutoff audits (2023–2024, may overlap with training data):**

| Audit | Vulns | Detected | Recall | Cost |
|---|---|---|---|---|
| pooltogether | 2 | 2 | 100% | $0.73 |
| nextgen | 2 | 2 | 100% | $0.85 |
| ethereumcreditguild | 2 | 0 | 0% | $0.90* |
| canto | 2 | 2 | 100% | $0.47 |
| curves | 4 | 4 | 100% | $0.52 |
| **Total** | **12** | **10** | **83%** | **$3.48** |

*\* Budget cap ($0.80) hit before report generation — not a detection failure.*

LLM judge rescore: **9/10 (90%)** with 60-100% confidence.

**Published baselines (EVMBench paper, 2026-02-18):**

| Model | Detect Recall | Detect Award |
|---|---|---|
| Claude Opus 4.6 | 45.9% | $37,824 |
| GPT-5.2 | ~lower | $8,106 |

#### Running Benchmarks

```bash
# Single audit
python3 benchmark/evmbench_skill_runner.py --audit 2026-01-tempo-feeamm

# Batch (first 5 from detect split)
python3 benchmark/evmbench_skill_runner.py --split detect-tasks --limit 5

# With cost controls
python3 benchmark/evmbench_skill_runner.py --audit 2024-05-munchables \
    --timeout 600 --max-budget 0.80 --no-judge

# Rescore existing outputs with LLM judge
python3 benchmark/evmbench_skill_runner.py --split detect-tasks --limit 5 --rescore

# Compare last two runs
python3 benchmark/evmbench_skill_runner.py --compare

# Dry run (validate setup, no API calls)
python3 benchmark/evmbench_skill_runner.py --split detect-tasks --dry-run
```

**CLI options:**

| Flag | Description |
|---|---|
| `--split` | EVMBench split (detect-tasks, patch-tasks, exploit-tasks) |
| `--audit` | Single audit ID |
| `--model` | Model override (sonnet, opus, haiku) |
| `--max-budget` | Max USD per audit |
| `--timeout` | Seconds per audit (default: 900) |
| `--no-judge` | Skip LLM judge, use identifier matching only |
| `--rescore` | Re-score existing outputs without re-running |
| `--runs N` | Best-of-N ensemble (union of findings across N runs) |

#### Scoring Pipeline

```
Skill Output (audit-reports/summary.md)
  │
  ├── LLM Judge (primary): Haiku-based semantic matching
  │   → Extracts findings from output
  │   → Compares each against ground truth vulnerability descriptions
  │   → Returns confidence score + matched finding ID
  │   → Cached in benchmark/results/judge_cache/
  │
  └── Identifier Matching (fallback): .sol filenames + camelCase function names
      → Overlap between output identifiers and ground truth identifiers
      → Threshold: 3+ matching identifiers OR 60%+ title word overlap

Final score = recall (40%) + precision (30%) + severity (15%) + cost (15%)
```

#### Token Optimization

The benchmark auto-generates `.claudeignore` in each audit source directory to exclude dependencies:

```
# Excluded from claude reads:
node_modules/  lib/  out/  cache/  artifacts/  typechain/
*.t.sol  *.s.sol  (test/script files)
```

Typical savings: 80-90% of .sol files excluded (e.g., 49/850 in-scope for munchables).

### Custom Suites

```bash
python3 benchmark/run.py --suite known-vulns
python3 benchmark/run.py --compare
```

Test cases in `benchmark/suites/`, config in `benchmark/config.yaml`.

---

## Directory Layout

```
Blitz-Bounty-Agent/
├── .claude-commands/                 Skill definitions (slash commands)
│   ├── web3-hunt.md                  Unified hunt (537 lines)
│   ├── web3-loop.md                  Autonomous loop (224 lines)
│   └── (legacy: immunefi-*, audit-*)
│
├── hunt_loop.py                      Headless Immunefi orchestrator
├── audit_loop.py                     Headless audit contest orchestrator
├── audit_orchestrator.py             Multi-agent parallel orchestrator
│
├── benchmark/                        Evaluation framework
│   ├── evmbench_skill_runner.py      EVMBench skill wrapper + token tracking
│   ├── llm_judge.py                  LLM-as-Judge (Haiku semantic matcher)
│   ├── run.py                        Custom suite runner
│   ├── config.yaml                   Scoring weights + model config
│   ├── evmbench_setup.sh             One-time EVMBench setup
│   ├── suites/                       Custom test case definitions
│   └── results/                      Benchmark outputs (gitignored)
│
├── poc-forge/                        Foundry PoC framework
│   ├── foundry.toml
│   └── test/                         PoC test files (.t.sol)
│
├── evmbench-sources/                 Cloned audit source code (gitignored)
├── evmbench-upstream/                EVMBench dataset (gitignored)
│
├── CLAUDE.md                         Claude Code project instructions
├── README.md                         This file
└── scope.md                          Example bounty scope document
```

**Runtime artifacts (gitignored):**
- `hunt-state.json` / `audit-state.json` — iteration state
- `hunt-logs/` / `audit-logs/` — per-iteration subprocess logs
- `audit-reports/` — generated findings reports

---

## Setup

### Prerequisites

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)
- [Foundry](https://book.getfoundry.sh/) (`forge`, `cast`, `anvil`)
- Python 3.10+ with PyYAML
- Optional: Docker + [uv](https://docs.astral.sh/uv/) (EVMBench official harness)
- Optional: `gh` CLI (Sherlock auto-submit)

### Install

```bash
git clone https://github.com/sane100400/Blitz-Bounty-Agent.git
cd Blitz-Bounty-Agent

# (Optional) EVMBench setup
bash benchmark/evmbench_setup.sh
```

Slash commands in `.claude-commands/` are auto-detected by Claude Code when running from this directory.

---

## Cost Estimates

| Command | Typical Cost | Notes |
|---|---|---|
| `/web3-hunt` (single audit) | $0.50–1.00 | Depends on codebase size |
| `/web3-loop` (10 iterations) | $3–8 | Recon cached after first iter |
| `audit_orchestrator.py` | $3–5 | 5-7 parallel agents |
| EVMBench (per audit) | $0.50–0.90 | With .claudeignore + budget cap |
| EVMBench (full 40 audits) | ~$25–35 | ~2.5 hours runtime |

---

## License

MIT
