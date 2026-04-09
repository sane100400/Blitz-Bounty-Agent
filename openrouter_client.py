#!/usr/bin/env python3
"""
Lightweight OpenRouter API client for cheap-model swarm experiments.

Supports MiMo-V2-Flash and other OpenRouter models. Tracks token usage
and cost per call. No dependencies beyond stdlib.
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
import time
from dataclasses import dataclass, field

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Pricing per token (not per 1M)
MODEL_PRICING = {
    "xiaomi/mimo-v2-flash": {"input": 0.00000009, "output": 0.00000029, "cache_read": 0.000000045},
    "xiaomi/mimo-v2-pro": {"input": 0.000001, "output": 0.000003, "cache_read": 0.0000002},
    "xiaomi/mimo-v2-omni": {"input": 0.0000004, "output": 0.000002, "cache_read": 0.00000008},
}

DEFAULT_MODEL = "xiaomi/mimo-v2-flash"


@dataclass
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    model: str = ""
    elapsed_seconds: float = 0.0
    error: str = ""


@dataclass
class TokenTracker:
    calls: list[dict] = field(default_factory=list)
    total_input: int = 0
    total_output: int = 0
    total_cost: float = 0.0

    total_cache_read: int = 0

    def record(self, resp: LLMResponse, label: str = ""):
        self.total_input += resp.input_tokens
        self.total_output += resp.output_tokens
        self.total_cache_read += resp.cache_read_tokens
        self.total_cost += resp.cost_usd
        self.calls.append({
            "label": label,
            "input_tokens": resp.input_tokens,
            "output_tokens": resp.output_tokens,
            "cache_read_tokens": resp.cache_read_tokens,
            "cost_usd": resp.cost_usd,
            "elapsed": resp.elapsed_seconds,
        })

    def summary(self) -> dict:
        return {
            "total_calls": len(self.calls),
            "total_input_tokens": self.total_input,
            "total_output_tokens": self.total_output,
            "total_cache_read_tokens": self.total_cache_read,
            "total_cost_usd": round(self.total_cost, 6),
        }


def call_openrouter(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    system: str = "",
    max_tokens: int = 4096,
    temperature: float = 0.0,
    timeout: int = 120,
    api_key: str | None = None,
) -> LLMResponse:
    """Single synchronous call to OpenRouter API."""
    key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        return LLMResponse(text="", error="OPENROUTER_API_KEY not set")

    messages = []
    if system:
        # Use cache_control to hint that system prompt should be cached
        messages.append({
            "role": "system",
            "content": [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
            ],
        })
    messages.append({"role": "user", "content": prompt})

    body = json.dumps({
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }).encode()

    req = urllib.request.Request(
        OPENROUTER_API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/blitz-bounty-agent",
        },
        method="POST",
    )

    start = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else str(e)
        return LLMResponse(text="", error=f"HTTP {e.code}: {error_body}",
                           elapsed_seconds=round(time.time() - start, 1))
    except Exception as e:
        return LLMResponse(text="", error=str(e),
                           elapsed_seconds=round(time.time() - start, 1))

    elapsed = round(time.time() - start, 1)

    choice = data.get("choices", [{}])[0]
    text = choice.get("message", {}).get("content", "")
    usage = data.get("usage", {})
    input_tokens = usage.get("prompt_tokens", 0)
    output_tokens = usage.get("completion_tokens", 0)
    cache_read_tokens = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0) or 0

    pricing = MODEL_PRICING.get(model, {"input": 0, "output": 0, "cache_read": 0})
    # Non-cached input tokens pay full price, cached ones pay cache_read price
    non_cached = max(0, input_tokens - cache_read_tokens)
    cost = (non_cached * pricing["input"]
            + cache_read_tokens * pricing.get("cache_read", pricing["input"])
            + output_tokens * pricing["output"])

    return LLMResponse(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cost_usd=round(cost, 6),
        model=model,
        elapsed_seconds=elapsed,
    )
