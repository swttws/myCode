from __future__ import annotations

import json
from typing import Any

import httpx

from mycode.llm import LLMError


def join_url(base_url: str, path: str) -> str:
    # base_url 由用户配置，path 由协议实现控制，统一在这里避免双斜杠。
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def raise_for_bad_status(response: httpx.Response) -> None:
    if response.status_code < 400:
        return
    raise LLMError(f"LLM request failed with status {response.status_code}")


def parse_json_object(data: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except json.JSONDecodeError as exc:
        raise LLMError("Invalid JSON in SSE data.") from exc
    if not isinstance(value, dict):
        raise LLMError("SSE data must be a JSON object.")
    return value
