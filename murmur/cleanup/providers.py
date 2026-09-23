from __future__ import annotations

import json
import logging
from typing import Optional, Protocol

import httpx

from murmur.cleanup.prompts import CleanupContext, build_prompt

log = logging.getLogger(__name__)


class CleanupProvider(Protocol):
    name: str

    def cleanup(self, raw: str, ctx: CleanupContext) -> str: ...


class DisabledProvider:
    name = "disabled"

    def cleanup(self, raw: str, ctx: CleanupContext) -> str:
        return raw


class AnthropicProvider:
    """Cleanup via Claude Haiku 4.5. BYOK."""

    name = "anthropic"
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5", timeout: float = 10.0):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def cleanup(self, raw: str, ctx: CleanupContext) -> str:
        if not self.api_key:
            log.warning("anthropic provider missing api key; returning raw")
            return raw

        prompt = build_prompt(raw, ctx)
        body = {
            "model": self.model,
            "max_tokens": 600,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "content-type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }
        try:
            r = httpx.post(self.URL, json=body, headers=headers, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPError as e:
            log.warning("anthropic cleanup failed: %s", e)
            return raw

        # response format: { "content": [{"type": "text", "text": "..."}] }
        blocks = data.get("content") or []
        text_parts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        cleaned = "".join(text_parts).strip()
        return cleaned or raw


class OllamaProvider:
    """Local cleanup via an Ollama server (default localhost:11434). Privacy fallback."""

    name = "ollama"

    def __init__(
        self,
        model: str = "llama3.2:3b",
        base_url: str = "http://localhost:11434",
        timeout: float = 15.0,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def cleanup(self, raw: str, ctx: CleanupContext) -> str:
        prompt = build_prompt(raw, ctx)
        body = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2},
        }
        try:
            r = httpx.post(f"{self.base_url}/api/generate", json=body, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPError as e:
            log.warning("ollama cleanup failed: %s", e)
            return raw

        return (data.get("response") or "").strip() or raw


def make_provider(
    kind: str,
    *,
    anthropic_api_key: Optional[str] = None,
    anthropic_model: str = "claude-haiku-4-5",
    ollama_model: str = "llama3.2:3b",
    ollama_base_url: str = "http://localhost:11434",
    timeout: Optional[float] = None,
) -> CleanupProvider:
    kind = (kind or "disabled").lower()
    if kind == "anthropic":
        p = AnthropicProvider(api_key=anthropic_api_key or "", model=anthropic_model)
    elif kind == "ollama":
        p = OllamaProvider(model=ollama_model, base_url=ollama_base_url)
    else:
        return DisabledProvider()
    if timeout is not None:
        p.timeout = timeout
    return p
