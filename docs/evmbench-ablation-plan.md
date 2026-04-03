# EVMBench Ablation Plan

## Goal

Answer one question cleanly:

> Does the current smart-contract agent architecture improve recall per dollar, or is it just spending tokens in a different shape?

This is not a leaderboard paper setup. It is a `cost-performance tradeoff` setup.

## Primary Claim To Test

The current system should be framed as:

- `domain-specialized decomposition`
- `scope-pruned single-machine orchestration`
- `cost-aware vulnerability detection`

It should **not** be framed as "best performance per dollar" until the matrix below is run.

## Core Metrics

Report these for every profile:

- `overall_recall`
- `total_cost_usd`
- `cost_per_detected = total_cost_usd / total_detected`
- `detected_per_dollar = total_detected / total_cost_usd`
- `total_award_detected`
- `wall-clock runtime`

Use `post-cutoff` and `pre-cutoff` slices separately. Do not pool them into a single headline number.

## Recommended Evaluation Slices

### Slice A: Post-cutoff capability

Use this for the main claim.

- Audits: `2025-06+`
- Current practical subset: `2026-01-tempo-feeamm`, `2026-01-tempo-mpp-streams`, `2026-01-tempo-stablecoin-dex`
- Purpose: measure generalization without obvious training leakage

### Slice B: Pre-cutoff stress test

Use this only as a secondary diagnostic.

- Audits: `<= 2025-05`
- Purpose: measure behavior on larger and more varied historical audits
- Warning: do not treat these numbers as uncontaminated capability evidence

## Ablation Matrix

### Single-pass baselines

These answer whether orchestration is helping at all.

1. `single_opus`
2. `single_sonnet`
3. `single_opus_cap_050`
4. `single_opus_cap_080`

### Orchestrated baselines

These answer whether the current hybrid split is actually optimal.

1. `hybrid_orchestrated`
   - deep reasoning: Opus
   - grep/pattern work: Sonnet
2. `all_opus_orchestrated`
3. `all_sonnet_orchestrated`

## Decision Rules

### If `single_sonnet` is close to `single_opus`

Then the current value is probably not "frontier intelligence", but:

- better decomposition
- better scope control
- better prompt structure

### If `all_opus_orchestrated` beats `hybrid_orchestrated` on recall but loses badly on cost

Then the right claim is:

- hybrid is the `efficient frontier` point
- not "best absolute recall"

### If `single_opus` matches `hybrid_orchestrated`

Then the orchestrator is not buying enough to justify its complexity.

### If `all_sonnet_orchestrated` is close to `hybrid_orchestrated`

Then the expensive deep-model allocation should be revisited.

## Minimal Paper-Grade Result Table

Every profile should be summarized as:

| Profile | Slice | Recall | Cost | $/Detect | Detect/$ | Runtime |
|---|---|---:|---:|---:|---:|---:|

Then include one frontier plot:

- x-axis: `total_cost_usd`
- y-axis: `overall_recall`
- annotate each profile

## Practical Run Order

To reduce spend:

1. Run all profiles on `post-cutoff` first
2. Drop obviously dominated profiles
3. Only then run the surviving profiles on `pre-cutoff`

Low-cost first-pass protocol:

1. Run `single_sonnet` on the 2026-01 Tempo audits
2. Run `single_opus` on the same audits
3. Only if needed, run `hybrid_orchestrated`
4. Use identifier scoring on the first pass, then spend judge calls only on the profiles worth keeping
5. Visualize from saved JSON instead of re-running

## Implementation Mapping

The repo now has two execution paths aligned with this plan:

- [benchmark/evmbench_skill_runner.py](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/evmbench_skill_runner.py)
  - single-pass detect runner
  - stores raw outputs and cost
  - supports pre/post-cutoff filtering
- [benchmark/evmbench_ablation.py](/home/sane100400/projects/Blitz-Bounty-Agent/benchmark/evmbench_ablation.py)
  - profile sweep runner
  - saves bundle JSON + frontier markdown
- [audit_orchestrator.py](/home/sane100400/projects/Blitz-Bounty-Agent/audit_orchestrator.py)
  - now exposes model and budget knobs needed for orchestration ablations

## Recommended First Command

```bash
python3 benchmark/evmbench_ablation.py \
  --profiles single_opus,single_sonnet,hybrid_orchestrated,all_opus_orchestrated,all_sonnet_orchestrated \
  --post-cutoff
```

That is the smallest experiment that can answer whether the current architecture has a real economic edge.
