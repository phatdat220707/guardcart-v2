"""Opt-in local Agent smoke: one draft call and at most three recommendation calls.

Uses a fixed synthetic shopping request. Never prints the bearer cookie, CSRF
token or provider key. Does not approve or execute a purchase.
"""

import argparse
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--live", action="store_true", help="Allow up to four metered model requests through the local API.")
    args = parser.parse_args()
    if not args.live:
        print("No model requests made. Add --live to exercise the real provider through the local API.")
        return 0
    base = args.base_url.rstrip("/")
    parsed = urlparse(base)
    if (parsed.scheme != "http" or parsed.hostname not in ("localhost", "127.0.0.1") or parsed.path
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        parser.error("Use a local HTTP base URL without credentials or a path.")
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    csrf, stage = None, "session"

    def request(method, path, body=None):
        headers = {"Origin": base}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        try:
            with client.open(Request(base + path, data=data, headers=headers, method=method), timeout=150) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    try:
        status, session = request("POST", "/api/session")
        if status != 201:
            raise ValueError("session failed")
        csrf = session["csrf_token"]
        stage = "draft"
        print("1/3: asking the model for an unconfirmed mandate draft...", flush=True)
        status, chat = request("POST", "/api/chat", {
            "request_id": str(uuid4()),
            "message": "Find road running shoes for long-distance training, EU42. Maximum HK$800 including shipping and fees. High cushioning and lightweight are preferences.",
        })
        if status != 200:
            print(json.dumps({"status": "FAIL", "stage": stage, "http_status": status,
                              "error_code": chat.get("error", {}).get("code", "unknown")}))
            return 1
        draft = chat["draft"]
        if (chat["needs_clarification"] or draft["category"] != "running_shoes"
                or draft["max_spend_minor"] != 80000):
            raise ValueError("draft expectation failed")
        stage = "confirmation"
        print("2/3: explicitly confirming demo terms with automatic approval OFF...", flush=True)
        status, mandate = request("POST", "/api/mandates", {
            "category": draft["category"], "max_spend_minor": draft["max_spend_minor"],
            "requirements": draft["requirements"], "preferences": draft["preferences"],
            "auto_approve_enabled": False, "approval_threshold_minor": None,
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        })
        if status != 201:
            raise ValueError("confirmation failed")
        stage = "recommendation"
        print("3/3: asking the Agent to search and return grounded catalogue matches...", flush=True)
        request_id = str(uuid4())
        status, result = request("POST", "/api/agent/recommend", {"request_id": request_id, "mandate_id": mandate["id"]})
        if status != 200:
            print(json.dumps({"status": "FAIL", "stage": stage, "http_status": status,
                              "error_code": result.get("error", {}).get("code", "unknown")}))
            return 1
        rows = result["recommendations"]
        if not rows or any(row["quote"]["policy"]["status"] != "ASK" for row in rows):
            raise ValueError("recommendation expectation failed")
        if any(row["quote"]["quote"]["final_total_minor"] > 80000 for row in rows):
            raise ValueError("budget expectation failed")
        if any(row["quote"]["quote"]["mandate_version"] != mandate["version"] for row in rows):
            raise ValueError("quote binding expectation failed")
        _, transactions = request("GET", "/api/transactions")
        if transactions:
            raise ValueError("unexpected purchase")
        status, replay = request("POST", "/api/agent/recommend", {"request_id": request_id, "mandate_id": mandate["id"]})
        if status != 200 or replay != result:
            raise ValueError("recommendation replay failed")
        print(json.dumps({
            "status": "PASS", "requested_model": result["run"]["requested_model"],
            "draft_provider_calls": chat["run"]["provider_calls"],
            "recommendation_provider_calls": result["run"]["provider_calls"],
            "draft_elapsed_ms": chat["run"]["elapsed_ms"],
            "recommendation_elapsed_ms": result["run"]["elapsed_ms"],
            "product_ids": [row["product_id"] for row in rows],
            "policies": [row["quote"]["policy"]["status"] for row in rows],
            "tools": [item["name"] for item in result["tool_trace"]],
            "idempotent_replay": True, "purchases_executed": 0,
            "billing_verified": False,
        }, indent=2))
        return 0
    except (URLError, TimeoutError, OSError):
        print(json.dumps({"status": "FAIL", "stage": stage, "error": "Local request unavailable or timed out; no automatic retry."}))
    except (ValueError, KeyError, TypeError):
        print(json.dumps({"status": "FAIL", "stage": stage, "error": "Workflow expectation failed; raw responses omitted."}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
