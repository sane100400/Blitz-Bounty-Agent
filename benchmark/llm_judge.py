#!/usr/bin/env python3
"""
LLM-as-Judge scorer for EVMBench.

Uses a small/fast model (Haiku) to semantically match skill findings
against ground truth vulnerabilities. Much more accurate than keyword
matching since findings use different IDs and terminology.

Usage:
    from llm_judge import LLMJudge
    judge = LLMJudge()
    is_match, confidence, reason = judge.match(skill_finding, gt_finding)
"""

import json
import os
import re
import subprocess
import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from claude_cli import ClaudeCliUnavailable, run_claude_prompt

# Cache dir to avoid re-judging identical pairs
CACHE_DIR = Path(__file__).parent / "results" / "judge_cache"
JUDGE_MODEL = os.environ.get("BLITZ_JUDGE_MODEL", "claude-opus-4-6")


class LLMJudge:
    """Semantic matching between audit findings using claude -p."""

    def __init__(self, api_key: str | None = None):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._cache = {}
        self._load_cache()

    def _cache_key(self, text_a: str, text_b: str) -> str:
        combined = (text_a.strip()[:2000] + "|||" + text_b.strip()[:2000])
        return hashlib.sha256(combined.encode()).hexdigest()[:16]

    def _load_cache(self):
        cache_file = CACHE_DIR / "judgments.json"
        if cache_file.exists():
            with open(cache_file) as f:
                self._cache = json.load(f)

    def _save_cache(self):
        cache_file = CACHE_DIR / "judgments.json"
        with open(cache_file, "w") as f:
            json.dump(self._cache, f, indent=2)

    def match(
        self,
        skill_finding: str,
        gt_finding: str,
        gt_title: str = "",
    ) -> tuple[bool, float, str]:
        """Check if a skill finding matches a ground truth vulnerability.

        Uses `claude -p` subprocess (no API key needed).
        Returns: (is_match, confidence 0-1, reason)
        """
        key = self._cache_key(skill_finding, gt_finding)
        if key in self._cache:
            cached = self._cache[key]
            return cached["match"], cached["confidence"], cached["reason"]

        prompt = f"""You are a smart contract security expert judging whether two audit findings describe the SAME vulnerability.

Two findings match if they identify the same root cause in the same code location, even if:
- They use different severity labels or IDs (H-01 vs H-03 etc.)
- They describe the impact differently
- One is more detailed than the other

Two findings do NOT match if:
- They are in different contracts/files (unless the bug spans both)
- They describe different root causes even if in the same function
- One is a consequence/symptom of the other but not the same bug

## Ground Truth Finding
Title: {gt_title}

{gt_finding[:2000]}

## Skill Finding (to evaluate)
{skill_finding[:2000]}

Respond ONLY with this JSON, nothing else:
{{"match": true/false, "confidence": 0.0-1.0, "reason": "one sentence"}}"""

        try:
            result_proc = run_claude_prompt(
                prompt,
                timeout=30,
                output_format="text",
                model=JUDGE_MODEL,
            )
            text = result_proc["text"].strip()

            # Parse JSON from response
            json_match = re.search(r'\{[^}]+\}', text)
            if json_match:
                result = json.loads(json_match.group())
            else:
                result = {"match": False, "confidence": 0.0, "reason": "parse error"}

        except ClaudeCliUnavailable as e:
            result = {"match": False, "confidence": 0.0, "reason": f"claude unavailable: {e}"}
        except Exception as e:
            result = {"match": False, "confidence": 0.0, "reason": f"error: {e}"}

        # Cache result
        self._cache[key] = result
        self._save_cache()

        return result["match"], result["confidence"], result["reason"]

    def score_findings(
        self,
        skill_report: str,
        gt_vulns: list[dict],
        gt_finding_details: dict[str, str],
    ) -> list[dict]:
        """Score a full skill report against all ground truth vulnerabilities.

        Uses a single batch call — gives the LLM both the skill report
        and the GT vuln list, asks it to match them all at once.
        """
        # Build GT summary with finding details for better matching
        gt_lines = []
        for vuln in gt_vulns:
            vid = vuln["id"]
            title = vuln.get("title", "")
            detail = gt_finding_details.get(vid, "")
            # Include a brief excerpt of the finding details for context
            detail_excerpt = detail[:300].replace("\n", " ") if detail else ""
            if detail_excerpt:
                gt_lines.append(f"- {vid}: {title}\n  Context: {detail_excerpt}")
            else:
                gt_lines.append(f"- {vid}: {title}")
        gt_summary = "\n".join(gt_lines)

        # Check cache
        cache_key = self._cache_key(skill_report[:3000], gt_summary)
        if cache_key in self._cache:
            return self._cache[cache_key]

        prompt = f"""You are a smart contract security expert. Match findings from a SKILL REPORT against GROUND TRUTH vulnerabilities.

## Ground Truth Vulnerabilities (to find)
{gt_summary}

## Skill Report (what was found)
{skill_report[:20000]}

## Task
For EACH ground truth vulnerability, determine if the skill report contains a finding that identifies the SAME vulnerability.

MATCHING CRITERIA (a match if ANY is true):
1. Same root cause in the same function/contract (strongest signal)
2. Same vulnerable function identified, even if the described impact differs
3. Same bug pattern (e.g., both say "TVL adds debt instead of subtracting") in the same connector
4. Skill finding identifies the specific code line where the GT bug exists, even if framed as a different issue class

NOT a match:
- Completely different contracts with no overlap
- Generic category match without specific code overlap (e.g., both mention "reentrancy" but in different functions)
- The skill finding only mentions the contract name in passing without analyzing the specific bug

IMPORTANT: Be generous with matches. If the skill report demonstrates awareness of the specific buggy code and the core issue, that counts as detection even if severity/framing differs.

Respond with ONLY a JSON array, one entry per ground truth vuln:
[
  {{"gt_id": "H-01", "match": true/false, "confidence": 0.0-1.0, "matched_skill_id": "H-XX or M-XX or empty", "reason": "brief"}},
  ...
]

Include ALL {len(gt_vulns)} ground truth vulns. Output ONLY the JSON array."""

        try:
            result_proc = run_claude_prompt(
                prompt,
                timeout=120,
                output_format="text",
                model=JUDGE_MODEL,
            )
            text = result_proc["text"].strip()

            # Parse JSON array
            array_match = re.search(r'\[[\s\S]*\]', text)
            if array_match:
                judgments = json.loads(array_match.group())
            else:
                return None  # fallback to identifier matching

        except ClaudeCliUnavailable as e:
            print(f"    Judge batch call unavailable: {e}")
            return None
        except Exception as e:
            print(f"    Judge batch call failed: {e}")
            return None

        # Build results from judgments
        judgment_map = {j["gt_id"]: j for j in judgments}
        results = []
        for vuln in gt_vulns:
            vid = vuln["id"]
            j = judgment_map.get(vid, {})
            results.append({
                "vuln_id": vid,
                "title": vuln.get("title", ""),
                "award": vuln.get("award", 0),
                "detected": j.get("match", False),
                "confidence": round(j.get("confidence", 0.0), 3),
                "reason": j.get("reason", "not in judge output"),
                "matched_skill_finding": j.get("matched_skill_id", ""),
            })

        # Cache
        self._cache[cache_key] = results
        self._save_cache()

        return results

    def _split_findings(self, report: str) -> dict[str, str]:
        """Split an audit report into individual findings.

        Handles various formats:
        - ### H-01: Title ...
        - ## [H-01] Title ...
        - | H-01 | High | Title |
        """
        findings = {}

        # Try markdown heading pattern: ### H-XX or ## H-XX or ## [H-XX]
        # Split on finding headers
        pattern = r'(?:^|\n)(#{2,3}\s*(?:\[?)((?:H|M|L)-\d+)(?:\]?)[\s:]+.*?)(?=\n#{2,3}\s*(?:\[?)(?:H|M|L)-\d+|\n## [A-Z]|\Z)'
        matches = re.findall(pattern, report, re.DOTALL)

        if matches:
            for full_match, finding_id in matches:
                findings[finding_id] = full_match.strip()
        else:
            # Fallback: treat entire report as one finding
            findings["FULL"] = report

        return findings


def score_with_judge(
    skill_report: str,
    gt_vulns: list[dict],
    gt_finding_details: dict[str, str],
) -> list[dict]:
    """Convenience function: score using LLM judge via claude -p."""
    judge = LLMJudge()
    return judge.score_findings(skill_report, gt_vulns, gt_finding_details)
