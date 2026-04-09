#!/usr/bin/env python3
"""
Shared utilities for EVMBench benchmark runners.

Consolidates config loading, audit inventory, source cloning, and scoring
constants used by both detect and patch runners.
"""

from __future__ import annotations

import csv
import subprocess
import sys
import yaml
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BENCHMARK_DIR = Path(__file__).parent
CONFIG_PATH = BENCHMARK_DIR / "config.yaml"
EVMBENCH_DIR = REPO_ROOT / "evmbench-upstream" / "project" / "evmbench"
AUDITS_DIR = EVMBENCH_DIR / "audits"
SOURCES_DIR = REPO_ROOT / "evmbench-sources"

IGNORE_DIRS = [
    "node_modules/", "lib/", "out/", "cache/", "artifacts/",
    "typechain/", "typechain-types/", ".git/", "broadcast/", "deployments/",
]
IGNORE_PATTERNS = ["*.t.sol", "*.s.sol", "*.spec.ts", "*.test.ts", "*.test.js"]

MODEL_ALIASES = {
    "opus": "claude-opus-4-6",
    "claude-opus-4-6": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "claude-sonnet-4-6": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
    "claude-haiku-4-5": "claude-haiku-4-5",
    "claude-haiku-4-5-20251001": "claude-haiku-4-5",
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}


def normalize_model_id(model: str | None) -> str:
    if not model:
        return "claude-opus-4-6"
    return MODEL_ALIASES.get(model, model)


def model_cutoff(model: str | None, config: dict | None = None) -> str | None:
    config = config or load_config()
    model_id = normalize_model_id(model)
    for item in config.get("models", []):
        if item.get("id") == model_id:
            return item.get("knowledge_cutoff")
    return None


def load_audit_config(audit_id: str) -> dict:
    config_path = AUDITS_DIR / audit_id / "config.yaml"
    if not config_path.exists():
        return {}
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def load_finding_details(audit_id: str) -> dict[str, str]:
    finding_dir = AUDITS_DIR / audit_id / "findings"
    details = {}
    if not finding_dir.exists():
        return details
    for path in sorted(finding_dir.glob("*.md")):
        if path.name == "gold_audit.md":
            continue
        details[path.stem] = path.read_text(errors="replace")
    return details


def audit_month(audit_id: str) -> str:
    return audit_id[:7]


def has_detect_vulns(audit_id: str) -> bool:
    return bool(load_audit_config(audit_id).get("vulnerabilities"))


def has_patch_vulns(audit_id: str) -> bool:
    cfg = load_audit_config(audit_id)
    return any(v.get("patch_path_mapping") for v in cfg.get("vulnerabilities", []))


def has_exploit_vulns(audit_id: str) -> bool:
    cfg = load_audit_config(audit_id)
    return any(v.get("test_path_mapping") for v in cfg.get("vulnerabilities", []))


def load_audit_inventory() -> list[dict]:
    csv_path = AUDITS_DIR / "task_info_audits.csv"
    rows = []
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if has_detect_vulns(row["audit"]):
                rows.append(row)
    return rows


def select_audits(
    split: str | None = None,
    audit_id: str | None = None,
    limit: int | None = None,
    post_cutoff: bool = False,
    pre_cutoff: bool = False,
    cutoff: str | None = None,
) -> list[str]:
    if audit_id:
        return [audit_id]

    audits = [row["audit"] for row in load_audit_inventory()]

    if split in (None, "detect-tasks"):
        selected = audits
    elif split == "patch-tasks":
        selected = [a for a in audits if has_patch_vulns(a)]
    elif split == "exploit-tasks":
        selected = [a for a in audits if has_exploit_vulns(a)]
    elif split == "debug":
        selected = ["2026-01-tempo-feeamm"]
    else:
        raise ValueError(f"unknown split: {split}")

    if cutoff:
        if post_cutoff and pre_cutoff:
            raise ValueError("cannot set both pre_cutoff and post_cutoff")
        if post_cutoff:
            selected = [a for a in selected if audit_month(a) > cutoff]
        elif pre_cutoff:
            selected = [a for a in selected if audit_month(a) <= cutoff]

    if limit is not None:
        selected = selected[:limit]
    return selected


def write_claudeignore(source_dir: Path) -> None:
    lines = ["# Auto-generated for EVMBench runs"] + IGNORE_DIRS + IGNORE_PATTERNS
    (source_dir / ".claudeignore").write_text("\n".join(lines) + "\n")


def clone_audit_source(audit_id: str, audit_config: dict, raw: bool = False) -> Path:
    source_dir = SOURCES_DIR / audit_id
    base_commit = audit_config.get("base_commit")

    if source_dir.exists() and any(source_dir.iterdir()):
        if base_commit:
            subprocess.run(
                ["git", "checkout", base_commit, "--", "."],
                cwd=str(source_dir), capture_output=True,
            )
            subprocess.run(
                ["git", "clean", "-fd"],
                cwd=str(source_dir), capture_output=True,
            )
        ignore_path = source_dir / ".claudeignore"
        if raw:
            ignore_path.unlink(missing_ok=True)
        else:
            write_claudeignore(source_dir)
        return source_dir

    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    repo_urls = [
        f"https://github.com/evmbench-org/{audit_id}.git",
        f"https://github.com/code-423n4/{audit_id}.git",
    ]

    cloned = False
    for repo_url in repo_urls:
        result = subprocess.run(
            ["git", "clone", "--depth", "50", repo_url, str(source_dir)],
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode == 0:
            cloned = True
            break

    if not cloned:
        raise RuntimeError(f"failed to clone source for {audit_id}")

    if base_commit:
        subprocess.run(
            ["git", "fetch", "--unshallow"],
            cwd=str(source_dir), capture_output=True, timeout=180,
        )
        subprocess.run(
            ["git", "checkout", base_commit],
            cwd=str(source_dir), capture_output=True, timeout=120,
        )

    if raw:
        (source_dir / ".claudeignore").unlink(missing_ok=True)
    else:
        write_claudeignore(source_dir)
    return source_dir


def get_scope_files(source_dir: Path) -> list[str]:
    skip = {"test", "tests", "lib", "node_modules", "out", "cache",
            "mock", "mocks", "artifacts", "typechain", ".git"}
    return sorted(
        str(f.relative_to(source_dir))
        for f in source_dir.rglob("*.sol")
        if not any(p.lower() in skip for p in f.relative_to(source_dir).parts)
        and not f.name.endswith((".t.sol", ".s.sol"))
    )
