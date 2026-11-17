"""Small Chat Completions compatibility test; no third-party packages required.

Without --live this only checks local configuration. With --live it makes
at most two inference requests, with no retries and capped output tokens.
It never prints the key, request headers, or raw provider error bodies.
"""

import argparse
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request


ROOT = Path(__file__).resolve().parents[1]
CATALOG = [{"id": "P01", "name": "Demo Running Shoe", "category": "running_shoes",
            "price_hkd": 699, "shipping_hkd": 30, "available": True}]
TOOLS = [{"type": "function", "function": {
    "name": "search_catalog",
    "description": "Read matching products from the demo catalogue.",
    "parameters": {
        "type": "object",
        "properties": {"category": {"type": "string", "enum": ["running_shoes"]}},
        "required": ["category"],
        "additionalProperties": False,
    },
}}]


def load_env(path):
    """Read simple NAME=value settings; existing process settings take priority."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not name.startswith("GUARDCART_AI_"):
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(name, value)


def get_message(response):
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Missing choices in the Chat Completions response.")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("Missing assistant message.")
    return message


def validate_tool_call(message):
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise ValueError("Expected exactly one tool call.")
    call = calls[0]
    function = call.get("function", {})
    if call.get("type") != "function" or function.get("name") != "search_catalog":
        raise ValueError("Unexpected tool requested; nothing was executed.")
    if not isinstance(call.get("id"), str) or not call["id"]:
        raise ValueError("Tool call has no valid ID.")
    arguments = json.loads(function.get("arguments", ""))
    if arguments != {"category": "running_shoes"}:
        raise ValueError("Tool arguments failed local validation.")
    return call


def post_chat(base_url, key, payload):
    request = Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
        method="POST",
    )
    # A redirect is rejected so credentials are never forwarded to another host.
    from urllib.request import HTTPRedirectHandler, build_opener

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    with build_opener(NoRedirect).open(request, timeout=30) as response:
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("Response exceeds the smoke-test size limit.")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Expected a JSON object response.")
        return result


def run(base_url, key, model):
    messages = [
        {"role": "system", "content": "Use the catalogue tool. Do not invent product IDs or prices."},
        {"role": "user", "content": "Find running shoes under HK$800 including shipping. Search the catalogue first."},
    ]
    started = time.perf_counter()
    print("Request 1/2: checking the model's tool call...", flush=True)
    first = post_chat(base_url, key, {
        "model": model, "messages": messages, "tools": TOOLS,
        "tool_choice": {"type": "function", "function": {"name": "search_catalog"}},
        "max_tokens": 256,
    })
    message = get_message(first)
    call = validate_tool_call(message)
    print("Tool call and arguments validated locally.", flush=True)
    messages.extend([
        {"role": "assistant", "content": message.get("content"), "tool_calls": [call]},
        {"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"products": CATALOG})},
        {"role": "user", "content": "Return the recommended product ID and total including shipping. Keep the answer short."},
    ])
    print("Request 2/2: returning catalogue data to the model...", flush=True)
    second = post_chat(base_url, key, {
        "model": model, "messages": messages, "tools": TOOLS,
        "tool_choice": "none", "max_tokens": 256,
    })
    final = get_message(second)
    content = final.get("content")
    if final.get("tool_calls") or not isinstance(content, str):
        raise ValueError("Expected a final text response without further tools.")
    if "P01" not in content or "729" not in content:
        raise ValueError("The final response did not contain the expected product ID and checkout total.")
    return {
        "status": "PASS", "requested_model": model,
        "tests": {"tool_call_valid": True, "tool_result_round_trip": True,
                  "expected_product_and_total_present": True},
        "elapsed_seconds": round(time.perf_counter() - started, 2),
        "usage": [
            {field: response.get("usage", {}).get(field)
             for field in ("prompt_tokens", "completion_tokens", "total_tokens")}
            for response in (first, second)
        ],
        "note": "Compatibility smoke test only. Billing and model provenance are not verified.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--live", action="store_true", help="Allow up to two billed inference requests.")
    args = parser.parse_args()
    load_env(args.env_file)
    key = os.environ.get("GUARDCART_AI_API_KEY", "").strip()
    model = os.environ.get("GUARDCART_AI_MODEL", "").strip()
    base_url = os.environ.get("GUARDCART_AI_BASE_URL", "https://zendigikey.shop/v1").strip()
    if not key or not model:
        print("Configuration incomplete. Set GUARDCART_AI_API_KEY and GUARDCART_AI_MODEL locally.")
        return 2
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        print("Invalid base URL: use HTTPS without credentials, query parameters, or fragments.")
        return 2
    if not args.live:
        print("Configuration present. No API requests made. Add --live to run the compatibility test.")
        return 0
    try:
        print(json.dumps(run(base_url, key, model), indent=2))
        return 0
    except HTTPError as exc:
        print("FAIL: provider returned HTTP " + str(exc.code) + ". Error body omitted.")
    except (URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        print("FAIL: network/timeout error (" + type(reason).__name__ + "). No automatic retries were made.")
    except (ValueError, KeyError, TypeError, AttributeError):
        print("FAIL: provider response failed protocol or expected-result validation. Raw response omitted.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

