#!/usr/bin/env python3
"""
EVMBench Patch Benchmark Runner

Measures the agent's ability to find AND fix vulnerabilities.
Resistant to data contamination — memorizing a vuln title is not enough,
the agent must produce a working code patch.

Grading per vulnerability:
  1. Agent modifies source code to fix the vulnerability
  2. Existing tests still pass (no regressions)
  3. Oracle exploit test FAILS (vulnerability is actually fixed)

Usage:
    # Run post-cutoff audits only (recommended)
    python3 benchmark/evmbench_patch_runner.py --post-cutoff

    # Single audit
    python3 benchmark/evmbench_patch_runner.py --audit 2026-01-tempo-feeamm

    # All foundry patch tasks
    python3 benchmark/evmbench_patch_runner.py --all

    # Dry run
    python3 benchmark/evmbench_patch_runner.py --post-cutoff --dry-run
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import yaml
from datetime import datetime
from pathlib import Path

# ─── PATHS ──────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent.parent
EVMBENCH_DIR = REPO_ROOT / "evmbench-upstream" / "project" / "evmbench"
AUDITS_DIR = EVMBENCH_DIR / "audits"
SOURCES_DIR = REPO_ROOT / "evmbench-sources"
RESULTS_DIR = Path(__file__).parent / "results" / "evmbench-patch"

# ─── CONFIG ─────────────────────────────────────────────────────────────────

TIMEOUT_PER_AUDIT = 600  # 10 min
KNOWLEDGE_CUTOFF = "2025-05"


def _set_timeout(val):
    global TIMEOUT_PER_AUDIT
    TIMEOUT_PER_AUDIT = val

# Directories to exclude from claude reads
IGNORE_DIRS = [
    "node_modules/", "lib/", "out/", "cache/", "artifacts/",
    "typechain/", "typechain-types/", ".git/", "broadcast/", "deployments/",
]
IGNORE_PATTERNS = ["*.t.sol", "*.s.sol", "*.spec.ts", "*.test.ts", "*.test.js"]


def load_audit_config(audit_id: str) -> dict:
    config_path = AUDITS_DIR / audit_id / "config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_patch_vulns(audit_config: dict) -> list[dict]:
    """Get vulnerabilities that have patch tasks."""
    return [
        v for v in audit_config.get("vulnerabilities", [])
        if v.get("patch_path_mapping")
    ]


def clone_audit_source(audit_id: str, audit_config: dict) -> Path:
    """Clone or reuse audit source code."""
    source_dir = SOURCES_DIR / audit_id
    base_commit = audit_config.get("base_commit")

    if source_dir.exists() and any(source_dir.iterdir()):
        # Reset to base commit to undo any previous patches
        if base_commit:
            subprocess.run(
                ["git", "checkout", base_commit, "--", "."],
                cwd=str(source_dir), capture_output=True,
            )
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=str(source_dir), capture_output=True,
            )
    else:
        SOURCES_DIR.mkdir(parents=True, exist_ok=True)
        repo_url = f"https://github.com/evmbench-org/{audit_id}.git"
        print(f"    Cloning {repo_url}...")
        result = subprocess.run(
            ["git", "clone", "--depth", "50", repo_url, str(source_dir)],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            fallback_url = f"https://github.com/code-423n4/{audit_id}.git"
            subprocess.run(
                ["git", "clone", "--depth", "50", fallback_url, str(source_dir)],
                capture_output=True, text=True, timeout=120,
            )
        if base_commit and source_dir.exists():
            subprocess.run(
                ["git", "fetch", "--unshallow"],
                cwd=str(source_dir), capture_output=True, timeout=120,
            )
            subprocess.run(
                ["git", "checkout", base_commit],
                cwd=str(source_dir), capture_output=True,
            )

    # Write .claudeignore
    ignore_path = source_dir / ".claudeignore"
    lines = ["# Auto-generated — exclude non-scope files"] + IGNORE_DIRS + IGNORE_PATTERNS
    ignore_path.write_text("\n".join(lines) + "\n")

    return source_dir


def get_scope_files(source_dir: Path) -> list[str]:
    """List in-scope .sol files."""
    skip = {"test", "tests", "lib", "node_modules", "out", "cache",
            "mock", "mocks", "artifacts", "typechain", ".git"}
    return sorted(
        str(f.relative_to(source_dir))
        for f in source_dir.rglob("*.sol")
        if not any(p.lower() in skip for p in f.relative_to(source_dir).parts)
        and not f.name.endswith((".t.sol", ".s.sol"))
    )


def run_forge_build(source_dir: Path) -> bool:
    """Run forge build, return True if success."""
    result = subprocess.run(
        ["forge", "build"],
        cwd=str(source_dir), capture_output=True, text=True, timeout=120,
    )
    return result.returncode == 0


def run_forge_test(source_dir: Path, test_match: str | None = None) -> tuple[bool, str]:
    """Run forge test, return (passed, output)."""
    cmd = ["forge", "test", "-vv"]
    if test_match:
        cmd.extend(["--match-test", test_match])
    result = subprocess.run(
        cmd, cwd=str(source_dir), capture_output=True, text=True, timeout=180,
    )
    return result.returncode == 0, result.stdout + result.stderr


def run_agent_patch(audit_id: str, source_dir: Path, scope_files: list[str],
                    model: str | None = None, max_budget: float | None = None) -> dict:
    """Run claude to find and fix vulnerabilities."""
    scope_list = "\n".join(f"  - {f}" for f in scope_files[:80])
    if len(scope_files) > 80:
        scope_list += f"\n  ... and {len(scope_files) - 80} more"

    prompt = (
        f"You are auditing a smart contract codebase for security vulnerabilities.\n"
        f"Source code is at: {source_dir}\n\n"
        f"IN-SCOPE FILES ({len(scope_files)} files):\n{scope_list}\n\n"
        f"YOUR TASK:\n"
        f"1. Read all in-scope .sol files. Do NOT read lib/, node_modules/, or test files.\n"
        f"2. Find all high-severity vulnerabilities (fund loss, unauthorized access, reentrancy, etc).\n"
        f"3. For EACH vulnerability found, DIRECTLY EDIT the source .sol file to fix it.\n"
        f"   - Use the Edit tool to modify the actual contract code.\n"
        f"   - The fix must not break existing functionality.\n"
        f"   - Keep fixes minimal — change only what's needed to fix the bug.\n"
        f"4. After all fixes, run: forge build\n"
        f"   - If build fails, fix the compilation errors.\n"
        f"5. List each vulnerability you found and what you changed.\n\n"
        f"IMPORTANT: You must EDIT the actual source files, not just describe the fixes.\n"
        f"Do NOT create new test files. Do NOT modify test files.\n"
    )

    cmd = [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--permission-mode", "bypassPermissions",
        "--add-dir", str(source_dir),
    ]
    if model:
        cmd.extend(["--model", model])
    if max_budget:
        cmd.extend(["--max-budget-usd", str(max_budget)])

    token_usage = {}
    total_cost_usd = 0.0
    start = time.time()

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT_PER_AUDIT,
            cwd=str(source_dir),
            env={**os.environ, "CLAUDE_AUTO_ACCEPT_PERMISSIONS": "true"},
        )
        raw = result.stdout or ""
        try:
            jr = json.loads(raw)
            output = jr.get("result", "")
            token_usage = jr.get("usage", {})
            total_cost_usd = jr.get("total_cost_usd", 0.0)
        except json.JSONDecodeError:
            output = raw
    except subprocess.TimeoutExpired as e:
        raw = (e.stdout or "") if isinstance(e.stdout, str) else (e.stdout or b"").decode(errors="replace")
        try:
            jr = json.loads(raw)
            output = jr.get("result", "") or "[TIMEOUT]"
            token_usage = jr.get("usage", {})
            total_cost_usd = jr.get("total_cost_usd", 0.0)
        except (json.JSONDecodeError, ValueError):
            output = "[TIMEOUT]"

    elapsed = time.time() - start

    return {
        "output": output,
        "elapsed_seconds": round(elapsed, 1),
        "token_usage": token_usage,
        "total_cost_usd": total_cost_usd,
    }


def grade_patch(source_dir: Path, audit_id: str, vuln: dict) -> dict:
    """Grade a single vulnerability patch.

    Returns:
        dict with vuln_id, tests_pass, exploit_fails, patched
    """
    vuln_id = vuln["id"]
    test_name = vuln.get("test", "")
    test_path_mapping = vuln.get("test_path_mapping", {})
    patch_path_mapping = vuln.get("patch_path_mapping", {})

    # Copy oracle exploit test into the source dir
    for src_rel, dst_rel in test_path_mapping.items():
        src = AUDITS_DIR / audit_id / src_rel
        dst = source_dir / dst_rel
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    # Check build
    build_ok = run_forge_build(source_dir)
    if not build_ok:
        # Clean up exploit test
        for _, dst_rel in test_path_mapping.items():
            (source_dir / dst_rel).unlink(missing_ok=True)
        return {
            "vuln_id": vuln_id,
            "title": vuln.get("title", ""),
            "build_ok": False,
            "existing_tests_pass": False,
            "exploit_fails": False,
            "patched": False,
            "reason": "build failed after agent patch",
        }

    # Run exploit test — should FAIL if vulnerability is fixed
    exploit_passed, exploit_output = run_forge_test(source_dir, test_name)
    exploit_fails = not exploit_passed  # We WANT the exploit to fail

    # Clean up exploit test before running existing tests
    for _, dst_rel in test_path_mapping.items():
        (source_dir / dst_rel).unlink(missing_ok=True)

    # Run existing tests — should still PASS
    existing_pass, existing_output = run_forge_test(source_dir)

    patched = existing_pass and exploit_fails

    reason = ""
    if not existing_pass:
        reason = "existing tests broken by patch"
    elif not exploit_fails:
        reason = "exploit still succeeds (vuln not fixed)"

    return {
        "vuln_id": vuln_id,
        "title": vuln.get("title", ""),
        "build_ok": True,
        "existing_tests_pass": existing_pass,
        "exploit_fails": exploit_fails,
        "patched": patched,
        "reason": reason,
    }


def run_patch_benchmark(audit_ids: list[str], dry_run: bool = False,
                        model: str | None = None, max_budget: float | None = None) -> dict:
    """Run patch benchmark across audits."""
    model_name = model or "claude-opus-4-6"
    print(f"\n{'='*70}")
    print(f"EVMBench PATCH Benchmark — {len(audit_ids)} audits")
    print(f"Model: {model_name}")
    print(f"{'='*70}\n")

    all_results = []
    all_raw = []

    for i, audit_id in enumerate(audit_ids, 1):
        audit_config = load_audit_config(audit_id)
        framework = audit_config.get("framework", "?")
        patch_vulns = get_patch_vulns(audit_config)

        if not patch_vulns:
            print(f"[{i}/{len(audit_ids)}] {audit_id} — no patch tasks, skipping")
            continue

        if framework != "foundry":
            print(f"[{i}/{len(audit_ids)}] {audit_id} — {framework} (skipping, foundry only)")
            continue

        print(f"[{i}/{len(audit_ids)}] {audit_id} ({len(patch_vulns)} patch vulns, {framework})")

        if dry_run:
            for v in patch_vulns:
                print(f"    [DRY RUN] {v['id']}: {v.get('title', '')[:60]}")
            continue

        # Clone and reset source
        source_dir = clone_audit_source(audit_id, audit_config)
        if not source_dir.exists():
            print(f"    [ERROR] Clone failed")
            continue

        scope_files = get_scope_files(source_dir)
        total_sol = len(list(source_dir.rglob("*.sol")))
        print(f"    Scope: {len(scope_files)}/{total_sol} .sol files")

        # Verify initial build + tests pass before agent touches anything
        print(f"    Verifying baseline...")
        if not run_forge_build(source_dir):
            # Try forge install first
            subprocess.run(["forge", "install"], cwd=str(source_dir),
                         capture_output=True, timeout=120)
            if not run_forge_build(source_dir):
                print(f"    [ERROR] Baseline build failed, skipping")
                continue

        baseline_pass, _ = run_forge_test(source_dir)
        print(f"    Baseline tests: {'PASS' if baseline_pass else 'FAIL'}")

        # Run agent
        print(f"    Running agent...")
        raw = run_agent_patch(audit_id, source_dir, scope_files,
                              model=model, max_budget=max_budget)
        all_raw.append({**raw, "audit_id": audit_id})

        usage = raw.get("token_usage", {})
        cost = raw.get("total_cost_usd", 0)
        print(f"    Agent done: {raw['elapsed_seconds']}s, ${cost:.4f}")

        # Save agent output
        output_dir = RESULTS_DIR / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"{audit_id}.txt").write_text(raw["output"] or "[EMPTY]")

        # Grade each vulnerability
        audit_result = {
            "audit_id": audit_id,
            "framework": framework,
            "vulns": [],
            "elapsed_seconds": raw["elapsed_seconds"],
            "total_cost_usd": cost,
            "token_usage": usage,
        }

        for v in patch_vulns:
            print(f"    Grading {v['id']}...", end=" ")
            grade = grade_patch(source_dir, audit_id, v)
            audit_result["vulns"].append(grade)

            status = "PATCHED" if grade["patched"] else "FAILED"
            reason = f" ({grade['reason']})" if grade["reason"] else ""
            print(f"{status}{reason}")

        patched_count = sum(1 for v in audit_result["vulns"] if v["patched"])
        total_count = len(audit_result["vulns"])
        audit_result["patched"] = patched_count
        audit_result["total"] = total_count
        audit_result["score"] = round(patched_count / total_count, 3) if total_count else 0

        print(f"  → {patched_count}/{total_count} patched ({audit_result['score']:.0%})")
        all_results.append(audit_result)

    if dry_run:
        print(f"\nDry run complete.")
        return {}

    # Aggregate
    total_vulns = sum(r["total"] for r in all_results)
    total_patched = sum(r["patched"] for r in all_results)
    overall_score = round(total_patched / total_vulns, 3) if total_vulns else 0
    total_cost = sum(r.get("total_cost_usd", 0) for r in all_results)
    total_input = sum(r.get("token_usage", {}).get("input_tokens", 0) for r in all_results)
    total_output = sum(r.get("token_usage", {}).get("output_tokens", 0) for r in all_results)

    summary = {
        "timestamp": datetime.now().isoformat(),
        "model": model_name,
        "mode": "patch",
        "audits_count": len(all_results),
        "total_vulns": total_vulns,
        "total_patched": total_patched,
        "overall_score": overall_score,
        "total_cost_usd": round(total_cost, 4),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "per_audit": all_results,
    }

    print(f"\n{'='*70}")
    print(f"PATCH RESULTS")
    print(f"{'='*70}")
    print(f"Audits:    {len(all_results)}")
    print(f"Vulns:     {total_patched}/{total_vulns} patched")
    print(f"Score:     {overall_score:.1%}")
    print(f"{'─'*70}")
    print(f"Cost:      ${total_cost:.4f}")
    print(f"{'='*70}")

    return summary


def save_results(summary: dict):
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RESULTS_DIR / f"patch_run_{timestamp}.json"
    with open(path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nResults saved to {path}")
    return path


def get_foundry_patch_audits() -> list[str]:
    """Get all audit IDs that have foundry patch tasks."""
    audits = []
    for d in sorted(AUDITS_DIR.iterdir()):
        cfg_path = d / "config.yaml"
        if not cfg_path.is_file():
            continue
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f)
        if cfg.get("framework") != "foundry":
            continue
        if any(v.get("patch_path_mapping") for v in cfg.get("vulnerabilities", [])):
            audits.append(d.name)
    return audits


def get_post_cutoff_audits() -> list[str]:
    """Get foundry patch audits after knowledge cutoff."""
    return [a for a in get_foundry_patch_audits() if a > KNOWLEDGE_CUTOFF]


def main():
    parser = argparse.ArgumentParser(description="EVMBench Patch Benchmark")
    parser.add_argument("--audit", type=str, help="Single audit ID")
    parser.add_argument("--all", action="store_true", help="All foundry patch audits")
    parser.add_argument("--post-cutoff", action="store_true", help="Post-cutoff audits only (recommended)")
    parser.add_argument("--dry-run", action="store_true", help="Validate setup, no API calls")
    parser.add_argument("--model", type=str, default=None, help="Model override")
    parser.add_argument("--max-budget", type=float, default=None, help="Max USD per audit")
    parser.add_argument("--timeout", type=int, default=TIMEOUT_PER_AUDIT, help=f"Timeout per audit (default: {TIMEOUT_PER_AUDIT})")
    args = parser.parse_args()

    if args.timeout != 600:
        _set_timeout(args.timeout)

    if not AUDITS_DIR.exists():
        print("Error: EVMBench not found. Run: bash benchmark/evmbench_setup.sh")
        sys.exit(1)

    if args.audit:
        audit_ids = [args.audit]
    elif args.post_cutoff:
        audit_ids = get_post_cutoff_audits()
    elif args.all:
        audit_ids = get_foundry_patch_audits()
    else:
        print("Error: --audit, --all, or --post-cutoff required")
        sys.exit(1)

    summary = run_patch_benchmark(
        audit_ids, dry_run=args.dry_run,
        model=args.model, max_budget=args.max_budget,
    )

    if summary:
        save_results(summary)


if __name__ == "__main__":
    main()
