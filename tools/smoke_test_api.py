"""Exercise local HTTP checkout without calling any AI or payment provider."""

import argparse
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    url = urlparse(base)
    if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost") or url.path
            or url.username or url.password or url.query or url.fragment):
        parser.error("Use a local HTTP base URL without a path or credentials.")
    client = build_opener(HTTPCookieProcessor(CookieJar()))
    csrf = None

    def request(method, path, payload=None, *, key=None):
        headers = {"Origin": base}
        if csrf:
            headers["X-CSRF-Token"] = csrf
        if key:
            headers["Idempotency-Key"] = key
        body = None
        if payload is not None:
            body = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        try:
            with client.open(Request(base + path, data=body, headers=headers, method=method), timeout=10) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())

    try:
        status, session = request("POST", "/api/session")
        if status != 201:
            raise ValueError("session_creation_failed")
        csrf = session["csrf_token"]
        results = []
        for product_id, expected in (("P01", "APPROVE"), ("P02", "ASK"), ("P04_DEMO", "BLOCK")):
            status, mandate = request("POST", "/api/mandates", {
                "category": "running_shoes", "max_spend_minor": 80000,
                "auto_approve_enabled": True, "approval_threshold_minor": 75000,
                "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            })
            if status != 201:
                raise ValueError("mandate_confirmation_failed")
            status, view = request("POST", "/api/quotes", {
                "mandate_id": mandate["id"], "product_id": product_id, "payment_id": "demo-card",
            })
            if status != 201 or view["policy"]["status"] != expected:
                raise ValueError("unexpected_policy_result")
            path = "/api/quotes/" + view["quote"]["id"]
            if expected == "BLOCK":
                status, _ = request("POST", path + "/approval", {"approve": True})
                if status != 409:
                    raise ValueError("blocked_quote_was_approved")
            else:
                if expected == "ASK":
                    status, denied = request("POST", path + "/checkout", key="smoke-" + product_id)
                    if status != 403 or denied["error"]["code"] != "approval_required":
                        raise ValueError("human_approval_was_bypassed")
                    # This CLI explicitly simulates the user's approval; no LLM is involved.
                    status, _ = request("POST", path + "/approval", {"approve": True})
                    if status != 200:
                        raise ValueError("approval_failed")
                status, completed = request("POST", path + "/checkout", key="smoke-" + product_id)
                if status != 200 or completed["status"] != "COMPLETED":
                    raise ValueError("checkout_failed")
                status, replay = request("POST", path + "/checkout", key="smoke-" + product_id)
                if status != 200 or replay != completed:
                    raise ValueError("idempotency_failed")
                status, stored = request("GET", "/api/mandates/" + mandate["id"])
                if status != 200 or stored["status"] != "CONSUMED":
                    raise ValueError("mandate_not_consumed")
            results.append({"product_id": product_id, "policy": expected, "status": "PASS"})
        status, ledger = request("GET", "/api/transactions")
        if status != 200 or len(ledger) != 2:
            raise ValueError("unexpected_transaction_count")
        print(json.dumps({"status": "PASS", "transport": "real localhost HTTP",
                          "scenarios": results, "completed_simulations": len(ledger),
                          "ai_requests": 0, "real_payments": 0}, indent=2))
        return 0
    except (URLError, TimeoutError, OSError):
        print("FAIL: local API is unavailable or timed out. Start the backend first.")
    except (ValueError, KeyError, TypeError):
        print("FAIL: HTTP workflow validation failed. Session credentials and response bodies are omitted.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
