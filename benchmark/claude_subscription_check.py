#!/usr/bin/env python3
"""
Preflight check for subscription-backed Claude Code execution.

Purpose:
- show that Blitz's main path can run without ANTHROPIC_API_KEY
- distinguish "not logged in" vs "usage exhausted" vs "working"
- keep the evidence in one reproducible command
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys


BLOCK_PATTERNS = {
    "usage_exhausted": ("you're out of extra usage", "out of extra usage"),
    "login_required": ("please run /login", "login required"),
    "invalid_api_key": ("invalid api key",),
    "low_credit": ("credit balance is too low",),
}


def _classify(text: str) -> str | None:
    lowered = (text or "").lower()
    for label, patterns in BLOCK_PATTERNS.items():
        if any(pattern in lowered for pattern in patterns):
            return label
    return None


def env_summary() -> dict[str, bool]:
    return {
        "ANTHROPIC_API_KEY": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "CLAUDE_CODE_USE_BEDROCK": bool(os.environ.get("CLAUDE_CODE_USE_BEDROCK")),
        "CLAUDE_CODE_USE_VERTEX": bool(os.environ.get("CLAUDE_CODE_USE_VERTEX")),
        "AWS_PROFILE": bool(os.environ.get("AWS_PROFILE")),
    }


def run_probe(model: str | None, timeout: int) -> dict:
    cmd = [
        "claude",
        "-p",
        "Reply exactly with SUBSCRIPTION_OK",
        "--output-format",
        "json",
    ]
    if model:
        cmd.extend(["--model", model])

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    raw = proc.stdout or proc.stderr or ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"result": raw}

    text = parsed.get("result", raw)
    classification = _classify(text) or _classify(raw)
    return {
        "raw": raw,
        "result_text": text.strip(),
        "classification": classification,
        "usage": parsed.get("usage", {}),
        "total_cost_usd": parsed.get("total_cost_usd", 0.0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check Claude Code subscription-backed execution")
    parser.add_argument("--probe", action="store_true", help="Send a minimal claude -p request")
    parser.add_argument("--model", default=None, help="Optional model override for the probe")
    parser.add_argument("--timeout", type=int, default=20, help="Probe timeout in seconds")
    args = parser.parse_args()

    claude_path = shutil.which("claude")
    if not claude_path:
        print("Claude CLI not found in PATH.")
        sys.exit(1)

    version = subprocess.run(
        ["claude", "--version"],
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()

    env = env_summary()
    print(f"claude_path: {claude_path}")
    print(f"claude_version: {version}")
    print("auth_env:")
    for key, present in env.items():
        print(f"  - {key}: {'set' if present else 'unset'}")

    if not args.probe:
        print("\nInterpretation:")
        print("- Blitz main runners use `claude -p`, so `ANTHROPIC_API_KEY` is not required if Claude Code CLI is already logged in.")
        print("- Upstream EVMBench official harness is a separate path and may still need API-style credentials.")
        return

    probe = run_probe(args.model, args.timeout)
    print("\nprobe_result:")
    print(f"  - classification: {probe['classification'] or 'ok'}")
    print(f"  - total_cost_usd: {probe['total_cost_usd']}")
    print(f"  - output: {probe['result_text']}")

    if probe["classification"] == "usage_exhausted":
        print("\nSubscription session exists, but current usage quota is exhausted.")
        sys.exit(2)
    if probe["classification"] == "login_required":
        print("\nClaude CLI is installed, but the session is not logged in.")
        sys.exit(3)
    if probe["classification"]:
        print("\nClaude CLI responded, but not in a usable state for benchmark runs.")
        sys.exit(4)

    if "SUBSCRIPTION_OK" in probe["result_text"]:
        print("\nSubscription-backed `claude -p` execution is working without API key env.")
        return

    print("\nProbe completed, but did not receive the expected acknowledgement.")
    sys.exit(5)


if __name__ == "__main__":
    main()
