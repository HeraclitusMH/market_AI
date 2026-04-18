"""Anthropic API client wrapper.

Talks to the Anthropic Messages API directly over HTTP using httpx (already a
project dependency). This avoids pulling in the full SDK for a single endpoint.

Responsibilities:
- Read the API key from an env var (never from YAML).
- Apply per-request timeout + bounded retry policy (exponential backoff + jitter).
- Strip markdown code fences from the response and parse strict JSON.
- Expose usage metadata (prompt/completion tokens, request id) if returned.
"""
from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from common.logging import get_logger

log = get_logger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"

_CODE_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE | re.MULTILINE)


class LlmError(Exception):
    """Base class for LLM client errors."""


class LlmAuthError(LlmError):
    """API key missing or rejected. Not retryable."""


class LlmTransientError(LlmError):
    """Timeouts, 5xx, 429 — retryable."""


class LlmResponseFormatError(LlmError):
    """JSON parse / structure failure. Not retryable."""


@dataclass
class LlmResponse:
    text: str
    data: Any                      # parsed JSON
    model: str
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    request_id: Optional[str]


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _CODE_FENCE_RE.sub("", stripped).strip()
    return stripped


def _extract_first_json_object(text: str) -> str:
    """Walk the string and return the first balanced { ... } block.

    The model is instructed to emit JSON only, but occasionally leading/trailing
    narration sneaks in. Rather than fail the whole batch, pick out the first
    well-balanced object and let the strict Pydantic validator reject it if it
    is malformed.
    """
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start : i + 1]
    return text  # fall through — json.loads will raise


class AnthropicClient:
    def __init__(
        self,
        api_key_env: str = "ANTHROPIC_API_KEY",
        model: str = "claude-3-5-sonnet-latest",
        timeout_seconds: float = 45.0,
        max_retries: int = 3,
        backoff_base_seconds: float = 2.0,
        backoff_max_seconds: float = 30.0,
    ):
        self.api_key_env = api_key_env
        self.model = model
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = int(max_retries)
        self.backoff_base_seconds = float(backoff_base_seconds)
        self.backoff_max_seconds = float(backoff_max_seconds)

    # ---------- helpers ----------

    def _api_key(self) -> str:
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise LlmAuthError(
                f"Anthropic API key missing: set env var {self.api_key_env}. "
                "This is an Anthropic API key (console.anthropic.com), "
                "NOT a Claude Pro chat subscription."
            )
        return key

    def _sleep(self, attempt: int) -> None:
        # Exponential backoff with full jitter.
        base = min(self.backoff_max_seconds, self.backoff_base_seconds * (2 ** attempt))
        delay = random.uniform(0, base)
        time.sleep(delay)

    # ---------- main call ----------

    def complete_json(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float = 0.2,
    ) -> LlmResponse:
        """Send a single Messages request and return parsed JSON.

        Raises:
            LlmAuthError      — 401/403 or missing key.
            LlmTransientError — exhausted retries on transient failures.
            LlmResponseFormatError — body not valid JSON after stripping fences.
        """
        headers = {
            "x-api-key": self._api_key(),
            "anthropic-version": ANTHROPIC_API_VERSION,
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [
                {"role": "user", "content": user},
            ],
        }

        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    resp = client.post(ANTHROPIC_API_URL, headers=headers, json=payload)

                if resp.status_code in (401, 403):
                    raise LlmAuthError(
                        f"Anthropic API rejected the key (HTTP {resp.status_code}): {resp.text[:200]}"
                    )
                if resp.status_code == 429 or 500 <= resp.status_code < 600:
                    raise LlmTransientError(
                        f"Anthropic transient error HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                if resp.status_code >= 400:
                    raise LlmError(
                        f"Anthropic error HTTP {resp.status_code}: {resp.text[:500]}"
                    )

                body = resp.json()
                return _parse_messages_response(body, self.model)

            except (httpx.TimeoutException, httpx.NetworkError, LlmTransientError) as e:
                last_err = e
                if attempt >= self.max_retries:
                    break
                log.warning(
                    "Anthropic request failed (attempt %d/%d): %s",
                    attempt + 1, self.max_retries + 1, e,
                )
                self._sleep(attempt)
                continue
            except (LlmAuthError, LlmResponseFormatError):
                raise
            except Exception as e:
                # Unknown — treat as non-retryable LlmError.
                raise LlmError(f"Anthropic call failed: {e}") from e

        raise LlmTransientError(
            f"Anthropic call exhausted {self.max_retries + 1} attempts: {last_err}"
        )


def _parse_messages_response(body: Dict[str, Any], model: str) -> LlmResponse:
    """Turn the raw Messages API body into an LlmResponse + parsed JSON."""
    content_blocks = body.get("content") or []
    text_parts: List[str] = []
    for block in content_blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(block.get("text") or "")
    raw_text = "".join(text_parts).strip()
    if not raw_text:
        raise LlmResponseFormatError("Anthropic returned no text content.")

    cleaned = _strip_code_fences(raw_text)
    cleaned = _extract_first_json_object(cleaned)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise LlmResponseFormatError(
            f"Model output was not valid JSON: {e}. First 200 chars: {raw_text[:200]!r}"
        ) from e

    usage = body.get("usage") or {}
    return LlmResponse(
        text=raw_text,
        data=data,
        model=body.get("model") or model,
        prompt_tokens=usage.get("input_tokens"),
        completion_tokens=usage.get("output_tokens"),
        request_id=body.get("id"),
    )
