"""Minimal synchronous Chat Completions adapter. No retries or redirects."""

from dataclasses import dataclass
import json
import re
from typing import Protocol

import httpx

from backend.ai.config import AiSettings
from backend.ai.errors import AiProtocolError, AiUnavailable


@dataclass(frozen=True)
class ProviderReply:
    message: dict
    usage: dict


class ChatProvider(Protocol):
    model_id: str

    def complete(self, messages: list[dict], tools: list[dict], tool_choice) -> ProviderReply: ...


class ChatCompletionsProvider:
    def __init__(self, settings: AiSettings):
        self.settings = settings
        self.model_id = settings.model

    def complete(self, messages, tools, tool_choice):
        payload = {"model": self.model_id, "messages": messages, "tools": tools,
                   "tool_choice": tool_choice, "max_tokens": self.settings.max_output_tokens}
        encoded = json.dumps(payload).encode()
        if len(encoded) > 80000:
            raise AiProtocolError()
        try:
            with httpx.Client(timeout=httpx.Timeout(40, connect=10), follow_redirects=False) as client:
                with client.stream("POST", self.settings.base_url.rstrip("/") + "/chat/completions",
                                   content=encoded, headers={"Authorization": "Bearer " + self.settings.api_key.get_secret_value(),
                                                            "Content-Type": "application/json"}) as response:
                    if response.status_code != 200:
                        raise AiUnavailable(upstream_status=response.status_code)
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > 512000:
                            raise AiProtocolError()
                        chunks.append(chunk)
            data = json.loads(b"".join(chunks))
            choice = data["choices"][0]
            if choice.get("finish_reason") == "length":
                raise AiProtocolError()
            message = choice["message"]
            if not isinstance(message, dict) or message.get("role", "assistant") != "assistant":
                raise AiProtocolError()
            # Preserve only protocol fields. Never save/log private reasoning or raw error bodies.
            content = message.get("content")
            calls = message.get("tool_calls", [])
            if content is not None and (not isinstance(content, str) or len(content) > 6000):
                raise AiProtocolError()
            if not isinstance(calls, list) or len(calls) > 4:
                raise AiProtocolError()
            usage = data.get("usage") or {}
            return ProviderReply(message={"role": "assistant", "content": content, "tool_calls": calls},
                                 usage={key: value if type(value) is int and value >= 0 else None
                                        for key, value in ((key, usage.get(key)) for key in
                                                           ("prompt_tokens", "completion_tokens", "total_tokens"))})
        except (httpx.HTTPError, OSError):
            raise AiUnavailable() from None
        except (KeyError, IndexError, TypeError, ValueError, AttributeError):
            raise AiProtocolError() from None


def validated_calls(reply: ProviderReply):
    calls = reply.message.get("tool_calls")
    if not isinstance(calls, list) or not 1 <= len(calls) <= 4:
        raise AiProtocolError()
    parsed, ids = [], set()
    for call in calls:
        try:
            function, call_id = call["function"], call["id"]
            if (call.get("type") != "function" or not isinstance(call_id, str) or len(call_id) > 128
                    or re.fullmatch(r"[A-Za-z0-9_-]+", call_id) is None or call_id in ids
                    or not isinstance(function["name"], str) or not isinstance(function["arguments"], str)
                    or len(function["arguments"]) > 16000):
                raise AiProtocolError()
            arguments = json.loads(function["arguments"])
            if not isinstance(arguments, dict):
                raise AiProtocolError()
            ids.add(call_id)
            parsed.append((call_id, function["name"], arguments))
        except (KeyError, TypeError, ValueError):
            raise AiProtocolError() from None
    return parsed
