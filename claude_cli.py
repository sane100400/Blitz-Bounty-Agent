#!/usr/bin/env python3
"""
Shared Claude Code CLI helpers.

These helpers exist for one reason: every repo-local execution path uses
`claude -p`, so login/quota/auth failures should be detected consistently
instead of being mistaken for "0 findings" or "parse error".
"""

from __future__ import annotations

import json
import os
import subprocess


BLOCK_PATTERNS = {
    "usage_exhausted": ("you're out of extra usage", "out of extra usage"),
    "login_required": ("please run /login", "login required"),
    "invalid_api_key": ("invalid api key",),
    "low_credit": ("credit balance is too low",),
}


class ClaudeCliUnavailable(RuntimeError):
    """Raised when Claude CLI exists but cannot serve a usable response."""


def detect_cli_blocker(text: str) -> tuple[str, str] | None:
    lowered = (text or "").lower()
    for label, patterns in BLOCK_PATTERNS.items():
        if any(pattern in lowered for pattern in patterns):
            return label, (text or "").strip()
    return None


def run_claude_prompt(
    prompt: str,
    *,
    cwd: str | None = None,
    timeout: int = 600,
    output_format: str = "text",
    model: str | None = None,
    max_budget: float | None = None,
    add_dir: str | None = None,
    permission_mode: str | None = None,
    auto_accept_permissions: bool = False,
) -> dict:
    """Run `claude -p` and normalize the result across scripts."""
    cmd = ["claude", "-p", prompt]
    if output_format:
        cmd.extend(["--output-format", output_format])
    if model:
        cmd.extend(["--model", model])
    if max_budget is not None:
        cmd.extend(["--max-budget-usd", str(max_budget)])
    if add_dir:
        cmd.extend(["--add-dir", add_dir])
    if permission_mode:
        cmd.extend(["--permission-mode", permission_mode])

    env = dict(os.environ)
    if auto_accept_permissions:
        env["CLAUDE_AUTO_ACCEPT_PERMISSIONS"] = "true"

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
        raw = proc.stdout or proc.stderr or ""
    except FileNotFoundError as exc:
        raise ClaudeCliUnavailable(
            "'claude' CLI not found. Install Claude Code CLI first."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        timeout_raw = exc.stdout if isinstance(exc.stdout, str) else ""
        raw = timeout_raw or "[TIMEOUT]"
        return {
            "raw": raw,
            "text": "[TIMEOUT]",
            "parsed": {},
            "usage": {},
            "total_cost_usd": 0.0,
            "timed_out": True,
        }

    parsed = {}
    text = raw
    usage = {}
    total_cost_usd = 0.0

    if output_format == "json":
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"result": raw}
        text = parsed.get("result", raw)
        usage = parsed.get("usage", {})
        total_cost_usd = parsed.get("total_cost_usd", 0.0)

    blocker = detect_cli_blocker(text) or detect_cli_blocker(raw)
    if blocker:
        _, message = blocker
        raise ClaudeCliUnavailable(message)

    return {
        "raw": raw,
        "text": text,
        "parsed": parsed,
        "usage": usage,
        "total_cost_usd": total_cost_usd,
        "timed_out": False,
    }
