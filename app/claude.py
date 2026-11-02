"""Claude on Microsoft Foundry (Anthropic Messages API), authenticated with Entra ID."""
from __future__ import annotations

import time
from typing import Any

import requests

ANTHROPIC_VERSION = "2023-06-01"
FOUNDRY_SCOPE = "https://ai.azure.com/.default"


class ClaudeError(RuntimeError):
    pass


class ClaudeClient:
    def __init__(self, base_url: str, credential: Any) -> None:
        self._base_url = base_url.rstrip("/")
        self._credential = credential

    def complete(self, deployment: str, system: str, user: str, max_tokens: int = 1024) -> str:
        headers = {
            "Authorization": f"Bearer {self._credential.get_token(FOUNDRY_SCOPE).token}",
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        body = {
            "model": deployment,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        for attempt in range(3):
            response = requests.post(f"{self._base_url}/v1/messages", headers=headers, json=body, timeout=120)
            if response.status_code == 429 and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            if response.status_code >= 400:
                raise ClaudeError(f"{deployment} returned HTTP {response.status_code}: {response.text[:300]}")
            payload = response.json()
            return "".join(
                block.get("text", "") for block in payload.get("content", []) if block.get("type") == "text"
            )
        raise ClaudeError(f"{deployment} stayed rate limited")
