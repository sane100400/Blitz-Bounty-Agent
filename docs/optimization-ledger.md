# Optimization Ledger

## Purpose

This document records the concrete optimization choices currently implemented in Blitz.

The goal is to avoid vague claims like "token efficient" and instead answer:

- what exactly was optimized
- where it is implemented
- what cost it is intended to save
- what tradeoff it introduces

## 1. Pure-Python Recon Before LLM Calls

### What

The orchestrator maps the codebase in Python before spawning any model call.

- enumerate in-scope `.sol` files
- classify pattern groups
- detect external protocol keywords
- build a compact file summary

### Why it saves cost

- avoids spending model context on file discovery
- shortens specialist prompts
- reduces duplicated "what files exist?" reasoning across agents

### Tradeoff

- the classification is heuristic and may miss semantic grouping beyond filename patterns

### Implementation

- [audit_orchestrator.py](/home/sane100400/projects/Blitz-Bounty-Agent/audit_orchestrator.py)

## 2. Scope Pruning With `.claudeignore`

### What

Each benchmark/source clone gets an auto-generated `.claudeignore` that excludes:

- dependencies
- artifacts
- test files
- scripts

### Why it saves cost

- reduces the number of files the Claude CLI can read
- prevents waste on `lib/`, `node_modules/`, `out/`, `cache/`, `*.t.sol`, `*.s.sol`

### Tradeoff

- if a benchmark accidentally encodes relevant logic in excluded paths, recall can drop

### Implementation

- [benchmark/evmbench_skill_runner.py](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/evmbench_skill_runner.py)
- [benchmark/evmbench_patch_runner.py](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/evmbench_patch_runner.py)

## 3. Domain-Specific Specialist Decomposition

### What

Instead of one monolithic audit prompt, Blitz splits analysis into specialist roles:

- TVL/accounting
- position lifecycle
- access control + fund flow
- external protocol semantics
- core logic

### Why it saves cost

- narrows each agent's search space
- avoids repeating the full protocol explanation in every reasoning branch
- makes it possible to allocate lower-cost models to grep/pattern-heavy work

### Tradeoff

- merge overhead exists
- bad decomposition can hide cross-cutting bugs

### Implementation

- [audit_orchestrator.py](/home/sane100400/projects/Blitz-Bounty-Agent/audit_orchestrator.py)

## 4. Model Tiering

### What

The orchestrator can assign:

- a deep model for semantic reasoning and merge
- a fast model for pattern-matching specialists

Current default:

- deep: `claude-opus-4-6`
- fast: `claude-sonnet-4-6`

### Why it saves cost

- reserves expensive reasoning for the stages most likely to benefit from it
- keeps structured grep/pattern work off the most expensive model

### Tradeoff

- if the fast model is too weak, recall can fall before merge

### Implementation

- [audit_orchestrator.py](/home/sane100400/projects/Blitz-Bounty-Agent/audit_orchestrator.py)
- [benchmark/evmbench_ablation.py](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/evmbench_ablation.py)

## 5. Per-Agent And Per-Run Budget Caps

### What

Budget controls now exist at multiple layers:

- per specialist
- merger
- per audit
- per skill run
- per ablation profile
- total ablation run

### Why it saves cost

- stops runaway long-tail runs
- makes comparisons fairer under the same spend ceiling
- turns token spend into an explicit experimental axis

### Tradeoff

- some misses become budget artifacts rather than capability failures
- results must report when a cap truncated the run

### Implementation

- [audit_orchestrator.py](/home/sane100400/projects/Blitz-Bounty-Agent/audit_orchestrator.py)
- [benchmark/evmbench_skill_runner.py](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/evmbench_skill_runner.py)
- [benchmark/evmbench_ablation.py](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/evmbench_ablation.py)

## 6. Lower-Cost Judge Model + Judge Cache

### What

LLM-as-Judge is forced onto a small model by default and caches judgments.

Current default:

- `claude-haiku-4-5`

### Why it saves cost

- semantic judging stays cost-efficient relative to the main audit run
- repeated re-scoring does not re-pay for the same comparison

### Tradeoff

- a weaker judge may mis-score edge cases
- cached wrong labels can persist until invalidated

### Implementation

- [benchmark/llm_judge.py](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/llm_judge.py)

## 7. Re-Score Without Re-Run

### What

Saved benchmark outputs can be scored again without invoking the main agent.

### Why it saves cost

- lets you change scoring logic or judge mode without paying for a fresh audit run
- especially useful when iterating on matching heuristics

### Tradeoff

- only helps evaluation cost, not search cost

### Implementation

- [benchmark/evmbench_skill_runner.py](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/evmbench_skill_runner.py)

## 8. Visualization Without Re-Run

### What

Ablation results are visualized from saved JSON bundles.

### Why it saves cost

- no benchmark rerun is needed to generate plots
- analysis iteration becomes free after the original run

### Tradeoff

- visualization is only as good as the saved bundle schema

### Implementation

- [benchmark/visualize_ablation.py](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/visualize_ablation.py)

## 9. Subscription-Backed Main Path

### What

Repo-local Blitz flows run through `claude -p` rather than requiring `ANTHROPIC_API_KEY`.

### Why it matters

- lowers deployment friction for solo researchers
- removes API integration overhead from the main path
- supports the "deployable" claim better than an API-only setup

### Tradeoff

- the run is now constrained by Claude Code subscription quota
- quota exhaustion must be handled explicitly

### Implementation

- [benchmark/claude_subscription_check.py](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/claude_subscription_check.py)
- [claude_cli.py](/home/sane100400/projects/Blitz-Bounty-Agent/claude_cli.py)

## 10. Fail-Fast Handling For Login / Quota Exhaustion

### What

The shared Claude CLI layer now detects:

- login required
- usage exhausted
- invalid API key
- low credit

and stops the run instead of saving fake zero-cost, zero-recall outputs.

### Why it saves cost and confusion

- avoids polluted benchmark artifacts
- makes interrupted runs diagnosable
- prevents "0 findings" from being confused with a real miss

### Tradeoff

- stricter failure handling means more runs terminate early instead of limping through

### Implementation

- [claude_cli.py](/home/sane100400/projects/Blitz-Bounty-Agent/claude_cli.py)
- [hunt_loop.py](/home/sane100400/projects/Blitz-Bounty-Agent/hunt_loop.py)
- [audit_loop.py](/home/sane100400/projects/Blitz-Bounty-Agent/audit_loop.py)
- [audit_orchestrator.py](/home/sane100400/projects/Blitz-Bounty-Agent/audit_orchestrator.py)
- [benchmark/evmbench_skill_runner.py](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/evmbench_skill_runner.py)
- [benchmark/evmbench_patch_runner.py](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/evmbench_patch_runner.py)
- [benchmark/llm_judge.py](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/llm_judge.py)

## What Is Still Missing

These are not yet fully measured, even if the hooks now exist:

- exact token savings from Python recon vs no recon
- exact savings from model tiering vs all-Opus
- exact cost impact of `.claudeignore` on each audit family
- false-positive cost burden under each profile
- whether Sonnet-first then Opus-escalation beats direct Opus on the Pareto frontier

## Recommended Use In Writing

When describing optimization, prefer:

- "scope pruning"
- "role decomposition"
- "model tiering"
- "budget-capped orchestration"
- "lower-cost judge + cache"
- "subscription-backed deployability"

Avoid vague claims like:

- "we optimized prompts a lot"
- "it uses fewer tokens somehow"
- "it is inexpensive"

## Related Positioning

For contribution framing and experiment claims, pair this ledger with:

- [docs/research-positioning.md](/home/sane100400/projects/Blitz-Bounty-Agent/docs/research-positioning.md)
- [docs/evmbench-ablation-plan.md](/home/sane100400/projects/Blitz-Bounty-Agent/docs/evmbench-ablation-plan.md)
