#!/usr/bin/env python3
"""
Run cost/performance ablations on EVMBench.

This script is deliberately small and opinionated:
- pick a profile matrix
- run the same audit slice across each profile
- save a bundle with recall, cost, $/detect, detect/$

The goal is not leaderboard chasing. The goal is to answer:
"Which architectural choice buys us the most recall per dollar?"
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from audit_orchestrator import run_benchmark_orchestrated
from evmbench_skill_runner import (
    model_cutoff,
    normalize_model_id,
    run_detect_benchmark,
    save_results,
    select_audits,
)


RESULTS_DIR = Path(__file__).parent / "results" / "ablation"

PROFILE_MATRIX = {
    "single_opus": {
        "runner": "skill",
        "model": "claude-opus-4-6",
        "description": "Single-pass /web3-hunt with Opus 4.6",
    },
    "single_sonnet": {
        "runner": "skill",
        "model": "claude-sonnet-4-6",
        "description": "Single-pass /web3-hunt with Sonnet 4.6",
    },
    "single_opus_cap_050": {
        "runner": "skill",
        "model": "claude-opus-4-6",
        "max_budget": 0.50,
        "description": "Single-pass Opus with a $0.50 cap per audit",
    },
    "single_opus_cap_080": {
        "runner": "skill",
        "model": "claude-opus-4-6",
        "max_budget": 0.80,
        "description": "Single-pass Opus with a $0.80 cap per audit",
    },
    "hybrid_orchestrated": {
        "runner": "orchestrated",
        "deep_model": "claude-opus-4-6",
        "fast_model": "claude-sonnet-4-6",
        "specialist_budget": 0.50,
        "merger_budget": 0.80,
        "description": "Current hybrid orchestrator: Opus for deep reasoning, Sonnet for pattern work",
    },
    "all_opus_orchestrated": {
        "runner": "orchestrated",
        "deep_model": "claude-opus-4-6",
        "fast_model": "claude-opus-4-6",
        "specialist_budget": 0.50,
        "merger_budget": 0.80,
        "description": "Orchestrator with Opus everywhere",
    },
    "all_sonnet_orchestrated": {
        "runner": "orchestrated",
        "deep_model": "claude-sonnet-4-6",
        "fast_model": "claude-sonnet-4-6",
        "specialist_budget": 0.50,
        "merger_budget": 0.80,
        "description": "Orchestrator with Sonnet everywhere",
    },
}


def _aggregate_profile(profile_name: str, profile: dict, per_audit: list[dict]) -> dict:
    total_vulns = sum(item.get("total_vulns", 0) for item in per_audit)
    total_detected = sum(item.get("detected", 0) for item in per_audit)
    total_cost = sum(float(item.get("total_cost_usd", 0.0) or 0.0) for item in per_audit)
    total_award = sum(float(item.get("total_award", 0.0) or 0.0) for item in per_audit)
    detected_award = sum(float(item.get("detected_award", 0.0) or 0.0) for item in per_audit)

    return {
        "profile": profile_name,
        "description": profile["description"],
        "runner": profile["runner"],
        "audits_count": len(per_audit),
        "total_vulns": total_vulns,
        "total_detected": total_detected,
        "overall_recall": round((total_detected / total_vulns) if total_vulns else 0.0, 3),
        "total_cost_usd": round(total_cost, 4),
        "cost_per_detected": round((total_cost / total_detected) if total_detected else 0.0, 4),
        "detected_per_dollar": round((total_detected / total_cost) if total_cost else 0.0, 4),
        "total_award_possible": round(total_award, 2),
        "total_award_detected": round(detected_award, 2),
        "per_audit": per_audit,
    }


def _frontier_markdown(rows: list[dict]) -> str:
    lines = [
        "# EVMBench Cost/Performance Frontier",
        "",
        "| Profile | Runner | Recall | Cost | $/Detect | Detect/$ |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in sorted(rows, key=lambda item: (-item["overall_recall"], item["cost_per_detected"] or 10**9)):
        lines.append(
            f"| {row['profile']} | {row['runner']} | "
            f"{row['overall_recall']:.1%} | "
            f"${row['total_cost_usd']:.4f} | "
            f"${row['cost_per_detected']:.4f} | "
            f"{row['detected_per_dollar']:.4f} |"
        )
    return "\n".join(lines) + "\n"


def run_profile(
    profile_name: str,
    profile: dict,
    audit_ids: list[str],
    timeout: int,
    use_llm_judge: bool,
    max_profile_cost: float | None = None,
) -> dict:
    if profile["runner"] == "skill":
        summary = run_detect_benchmark(
            audit_ids=audit_ids,
            model=normalize_model_id(profile["model"]),
            timeout=timeout,
            max_budget=profile.get("max_budget"),
            max_total_cost=max_profile_cost,
            use_llm_judge=use_llm_judge,
            rescore=False,
            label=profile_name,
        )
        save_results(summary, prefix=profile_name)
        return {
            **summary,
            "profile": profile_name,
            "description": profile["description"],
            "runner": "skill",
        }

    if profile["runner"] == "orchestrated":
        per_audit = []
        spent = 0.0
        for audit_id in audit_ids:
            if max_profile_cost is not None and spent >= max_profile_cost:
                print(f"Reached profile budget (${max_profile_cost:.2f}); stopping {profile_name}.")
                break
            result = run_benchmark_orchestrated(
                audit_id=audit_id,
                timeout=timeout,
                deep_model=profile["deep_model"],
                fast_model=profile["fast_model"],
                specialist_budget=profile.get("specialist_budget", 0.50),
                merger_budget=profile.get("merger_budget", 0.80),
                label=profile_name,
                use_llm_judge=use_llm_judge,
            )
            per_audit.append(result)
            spent += float(result.get("total_cost_usd", 0.0) or 0.0)
        return _aggregate_profile(profile_name, profile, per_audit)

    raise ValueError(f"unsupported runner: {profile['runner']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run EVMBench cost/performance ablations")
    parser.add_argument(
        "--profiles",
        default="single_opus,single_sonnet,hybrid_orchestrated",
        help="Comma-separated profile names",
    )
    parser.add_argument("--split", default="detect-tasks", help="Audit split")
    parser.add_argument("--audit", type=str, help="Single audit ID")
    parser.add_argument("--audits", type=str, help="Comma-separated explicit audit IDs")
    parser.add_argument("--limit", type=int, default=None, help="Limit audits")
    parser.add_argument("--timeout", type=int, default=900, help="Timeout per audit")
    parser.add_argument("--no-judge", action="store_true", help="Disable LLM judge")
    parser.add_argument("--max-profile-cost", type=float, default=None, help="Stop each profile after this much total spend")
    parser.add_argument("--max-total-cost", type=float, default=None, help="Stop the whole ablation after this much total spend")
    parser.add_argument("--post-cutoff", action="store_true", help="Filter to post-cutoff audits")
    parser.add_argument("--pre-cutoff", action="store_true", help="Filter to pre-cutoff audits")
    parser.add_argument(
        "--cutoff-model",
        default="claude-opus-4-6",
        help="Model whose cutoff month defines the pre/post split",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the matrix without executing")
    args = parser.parse_args()

    selected_profiles = [name.strip() for name in args.profiles.split(",") if name.strip()]
    missing = [name for name in selected_profiles if name not in PROFILE_MATRIX]
    if missing:
        raise SystemExit(f"Unknown profile(s): {', '.join(missing)}")

    cutoff = model_cutoff(normalize_model_id(args.cutoff_model))
    if args.audits:
        audit_ids = [item.strip() for item in args.audits.split(",") if item.strip()]
    else:
        audit_ids = select_audits(
            split=args.split,
            audit_id=args.audit,
            limit=args.limit,
            post_cutoff=args.post_cutoff,
            pre_cutoff=args.pre_cutoff,
            cutoff=cutoff,
        )

    if args.dry_run:
        print(f"Cutoff model: {normalize_model_id(args.cutoff_model)} ({cutoff})")
        print(f"Selected audits ({len(audit_ids)}):")
        for audit_id in audit_ids:
            print(f"  - {audit_id}")
        print("\nProfiles:")
        for name in selected_profiles:
            profile = PROFILE_MATRIX[name]
            print(f"  - {name}: {profile['description']}")
        return

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    rows = []
    total_spent = 0.0
    for name in selected_profiles:
        if args.max_total_cost is not None and total_spent >= args.max_total_cost:
            print(f"\nReached total ablation budget (${args.max_total_cost:.2f}); stopping before {name}.")
            break
        profile = PROFILE_MATRIX[name]
        print(f"\n{'=' * 70}")
        print(f"Running profile: {name}")
        print(profile["description"])
        print(f"{'=' * 70}")
        row = run_profile(
            profile_name=name,
            profile=profile,
            audit_ids=audit_ids,
            timeout=args.timeout,
            use_llm_judge=not args.no_judge,
            max_profile_cost=args.max_profile_cost,
        )
        rows.append(row)
        total_spent += float(row.get("total_cost_usd", 0.0) or 0.0)

    bundle = {
        "timestamp": datetime.now().isoformat(),
        "profiles": selected_profiles,
        "audits": audit_ids,
        "results": rows,
    }
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = RESULTS_DIR / f"ablation_{timestamp}.json"
    md_path = RESULTS_DIR / f"ablation_{timestamp}.md"
    json_path.write_text(json.dumps(bundle, indent=2))
    md_path.write_text(_frontier_markdown(rows))

    print(f"\nSaved JSON: {json_path}")
    print(f"Saved table: {md_path}")
    print("\nFrontier:")
    print(_frontier_markdown(rows))


if __name__ == "__main__":
    main()
