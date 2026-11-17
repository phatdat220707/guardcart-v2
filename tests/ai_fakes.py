from copy import deepcopy
import json
from threading import Lock

from backend.ai.provider import ProviderReply


def tool_reply(name, arguments, call_id="call-1"):
    return ProviderReply(message={"role": "assistant", "content": None, "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]},
        usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120})


def draft_reply(**changes):
    return tool_reply("propose_mandate", {"draft": {"category": "running_shoes", "max_spend_minor": 80000, **changes}})


def finish_from_search(messages, tools, choice):
    for message in messages:
        if message["role"] == "tool":
            content = json.loads(message["content"])
            if "matches" in content:
                return tool_reply("finish_recommendations", {"product_ids": [item["id"] for item in content["matches"][:3]]}, "call-finish")
    raise AssertionError("No catalogue response was supplied to the model")


class ScriptedProvider:
    model_id = "fake-model-for-offline-tests"

    def __init__(self, *responses):
        self.responses, self.calls, self.lock = list(responses), [], Lock()

    def complete(self, messages, tools, choice):
        with self.lock:
            self.calls.append(deepcopy({"messages": messages, "tools": tools, "choice": choice}))
            if not self.responses:
                raise AssertionError("Unexpected extra provider call")
            response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response(messages, tools, choice) if callable(response) else response
