#!/usr/bin/env python3
"""
EVMBench vulnerability tagger.

Reads all audit configs + finding markdowns and produces a tagged JSON
with mechanical tags (cutoff, scale, difficulty) and placeholders for
LLM-tagged fields (locality, category).

Usage:
    python3 benchmark/evmbench_tagger.py                # extract + save
    python3 benchmark/evmbench_tagger.py --stats         # show tag distributions
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
EVMBENCH_DIR = REPO_ROOT / "evmbench-upstream" / "project" / "evmbench"
AUDITS_DIR = EVMBENCH_DIR / "audits"
OUTPUT_PATH = Path(__file__).parent / "evmbench_tags.json"

KNOWLEDGE_CUTOFF = "2025-05"

# Scale thresholds (based on n_contracts)
SCALE_SMALL = 20
SCALE_LARGE = 80

# Difficulty thresholds (based on dup count)
DIFF_HARD = 3
DIFF_MEDIUM = 15


def load_audit_csv() -> dict[str, dict]:
    """Load task_info_audits.csv into dict keyed by audit ID."""
    csv_path = AUDITS_DIR / "task_info_audits.csv"
    result = {}
    with open(csv_path, newline="") as f:
        for row in csv.DictReader(f):
            result[row["audit"]] = row
    return result


def count_dups(finding_path: Path) -> int:
    """Count duplicate submissions from finding markdown.

    Supports two formats:
    1. Issue link style: (findings/issues/NNN) — count unique issue numbers
    2. Text style: '*Submitted by X, also found by Y, Z*' — count names
    Returns total submitter count (including original).
    """
    if not finding_path.exists():
        return 1
    text = finding_path.read_text(errors="replace")
    header = text[:5000]

    # Method 1: GitHub issue links (e.g. Curves format)
    issue_nums = set(re.findall(r"findings/issues/(\d+)", header))
    if len(issue_nums) > 2:
        return len(issue_nums)

    # Method 2: 'Submitted by X, also found by Y, Z' text pattern
    m = re.search(r"\*Submitted by (.+?)(\n|\*)", header, re.DOTALL)
    if m:
        submitter_text = m.group(1)
        # Split by comma, 'and', parenthetical groupings
        parts = re.split(r",\s*|\band\b", submitter_text)
        names = [
            p.strip() for p in parts
            if p.strip()
            and p.strip() != "also found by"
            and not p.strip().startswith("(")
        ]
        return max(len(names), 1)

    return 1


def classify_scale(n_contracts: int) -> str:
    if n_contracts <= SCALE_SMALL:
        return "small"
    elif n_contracts <= SCALE_LARGE:
        return "medium"
    else:
        return "large"


def classify_difficulty(dup_count: int) -> str:
    if dup_count <= DIFF_HARD:
        return "hard"
    elif dup_count <= DIFF_MEDIUM:
        return "medium"
    else:
        return "easy"


def extract_severity(vuln_id: str) -> str:
    if vuln_id.startswith("H"):
        return "high"
    elif vuln_id.startswith("M"):
        return "medium"
    return "unknown"


def get_finding_text(audit_id: str, vuln_id: str) -> str:
    """Get first ~2000 chars of finding markdown for LLM tagging."""
    finding_path = AUDITS_DIR / audit_id / "findings" / f"{vuln_id}.md"
    if not finding_path.exists():
        return ""
    text = finding_path.read_text(errors="replace")
    # Skip the submitter list header, get to the actual description
    # Look for first paragraph after the submitter line
    return text[:3000]


def build_tags() -> list[dict]:
    audit_csv = load_audit_csv()
    tags = []

    for audit_dir in sorted(AUDITS_DIR.iterdir()):
        cfg_path = audit_dir / "config.yaml"
        if not cfg_path.is_file():
            continue

        audit_id = audit_dir.name
        with open(cfg_path) as f:
            cfg = yaml.safe_load(f) or {}

        vulns = cfg.get("vulnerabilities", [])
        if not vulns:
            continue

        csv_row = audit_csv.get(audit_id, {})
        n_contracts = int(csv_row.get("n_contracts", 0))
        codebase_sloc = int(csv_row.get("codebase_sloc", 0))
        audit_month = audit_id[:7]  # e.g. "2024-01"

        for vuln in vulns:
            vuln_id = vuln["id"]
            finding_path = AUDITS_DIR / audit_id / "findings" / f"{vuln_id}.md"
            dup_count = count_dups(finding_path)

            entry = {
                "id": f"{audit_id}-{vuln_id}",
                "audit": audit_id,
                "vuln_id": vuln_id,
                "title": vuln.get("title", ""),
                "severity": extract_severity(vuln_id),
                "award": float(vuln.get("award", 0.0) or 0.0),
                "has_patch": bool(vuln.get("patch_path_mapping")),
                "has_exploit": bool(vuln.get("test_path_mapping")),
                # Mechanical tags
                "audit_month": audit_month,
                "cutoff_status": "post" if audit_month > KNOWLEDGE_CUTOFF else "pre",
                "n_contracts": n_contracts,
                "codebase_sloc": codebase_sloc,
                "scale": classify_scale(n_contracts),
                "dup_count": dup_count,
                "difficulty": classify_difficulty(dup_count),
                # LLM-tagged (placeholder)
                "locality": "",
                "category": "",
            }
            tags.append(entry)

    return tags


def print_stats(tags: list[dict]) -> None:
    print(f"\nTotal vulnerabilities: {len(tags)}")
    print(f"Audits: {len(set(t['audit'] for t in tags))}")

    # Cutoff
    pre = sum(1 for t in tags if t["cutoff_status"] == "pre")
    post = sum(1 for t in tags if t["cutoff_status"] == "post")
    print(f"\nCutoff: pre={pre}, post={post}")

    # Severity
    for sev in ["high", "medium"]:
        count = sum(1 for t in tags if t["severity"] == sev)
        print(f"Severity {sev}: {count}")

    # Scale
    for s in ["small", "medium", "large"]:
        count = sum(1 for t in tags if t["scale"] == s)
        print(f"Scale {s}: {count}")

    # Difficulty
    for d in ["hard", "medium", "easy"]:
        count = sum(1 for t in tags if t["difficulty"] == d)
        avg_dup = 0
        dups = [t["dup_count"] for t in tags if t["difficulty"] == d]
        if dups:
            avg_dup = sum(dups) / len(dups)
        print(f"Difficulty {d}: {count} (avg dups: {avg_dup:.1f})")

    # Locality/category fill rate
    loc_filled = sum(1 for t in tags if t["locality"])
    cat_filled = sum(1 for t in tags if t["category"])
    print(f"\nLocality tagged: {loc_filled}/{len(tags)}")
    print(f"Category tagged: {cat_filled}/{len(tags)}")


def apply_llm_tags(tags: list[dict]) -> int:
    """Apply LLM-classified locality and category tags."""
    from evmbench_llm_tags import TAGS as LLM_TAGS

    applied = 0
    for t in tags:
        key = t["id"]
        if key in LLM_TAGS:
            locality, category = LLM_TAGS[key]
            t["locality"] = locality
            t["category"] = category
            applied += 1
    return applied


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM tag application")
    args = parser.parse_args()

    if args.stats and OUTPUT_PATH.exists():
        with open(OUTPUT_PATH) as f:
            tags = json.load(f)
        print_stats(tags)
        return

    tags = build_tags()

    if not args.no_llm:
        applied = apply_llm_tags(tags)
        print(f"Applied LLM tags to {applied}/{len(tags)} vulnerabilities")

    # Filter out template entries
    tags = [t for t in tags if not t["id"].startswith("template")]

    with open(OUTPUT_PATH, "w") as f:
        json.dump(tags, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(tags)} tagged vulnerabilities to {OUTPUT_PATH}")
    print_stats(tags)


if __name__ == "__main__":
    main()
