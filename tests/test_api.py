import asyncio
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest
import os
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from backend.api import create_app
from backend.settings import ApiSettings
from tests.fixtures import NOW, updated


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1.0
        self.directory = tempfile.TemporaryDirectory(prefix="guardcart-api-")
        self.addCleanup(self.directory.cleanup)
        self.now = NOW
        self.origin = "http://testserver"
        self.settings = ApiSettings(database_path=Path(self.directory.name) / "api.sqlite3",
                                    allowed_origins=(self.origin,), allowed_hosts=("testserver",))
        self.app = create_app(self.settings, clock=lambda: self.now)
        self.service = self.app.state.service
        self.client = self.client_for(self.app)
        response = await self.client.post("/api/session", headers={"Origin": self.origin})
        self.assertEqual(response.status_code, 201)
        self.csrf = response.json()["csrf_token"]

    def client_for(self, app, *, base_url="http://testserver"):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url=base_url)
        self.addAsyncCleanup(client.aclose)
        return client

    def headers(self, **extra):
        return {"Origin": self.origin, "X-CSRF-Token": self.csrf, **extra}

    def terms(self, **changes):
        return {"category": "running_shoes", "max_spend_minor": 80000,
                "expires_at": (NOW + timedelta(hours=1)).isoformat(), **changes}

    async def mandate(self, **changes):
        response = await self.client.post("/api/mandates", json=self.terms(**changes), headers=self.headers())
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def quote(self, mandate, product_id="P01"):
        response = await self.client.post("/api/quotes", json={"mandate_id": mandate["id"],
                                          "product_id": product_id, "payment_id": "demo-card"}, headers=self.headers())
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def checkout(self, quote, key="purchase"):
        return await self.client.post(f"/api/quotes/{quote['quote']['id']}/checkout",
                                      headers=self.headers(**{"Idempotency-Key": key}))

    async def approve(self, quote, action=True):
        return await self.client.post(f"/api/quotes/{quote['quote']['id']}/approval",
                                      json={"approve": action}, headers=self.headers())

    async def test_public_health_and_catalogue_need_no_session(self):
        visitor = self.client_for(self.app)
        response = await visitor.get("/api/health")
        self.assertEqual(response.json(), {"status": "ok", "mode": "simulation", "ai_enabled": False})
        catalogue = (await visitor.get("/api/catalogue")).json()
        self.assertEqual(len(catalogue["products"]), 20)
        self.assertEqual(len(catalogue["payments"]), 4)
        self.assertTrue(any(not entry["availability"] for entry in catalogue["products"]))
        self.assertTrue(any(not entry["trusted"] for entry in catalogue["merchants"]))

    async def test_session_cookie_is_http_only_and_not_in_response_json(self):
        response = await self.client.post("/api/session", headers={"Origin": self.origin})
        self.assertEqual(response.status_code, 200)
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertIn("path=/", cookie)
        self.assertNotIn("domain=", cookie)
        self.assertNotIn(self.client.cookies.get(self.settings.cookie_name), response.text)
        self.assertEqual(response.json()["csrf_token"], self.csrf)

    async def test_missing_or_disallowed_origin_is_rejected_before_session_creation(self):
        visitor = self.client_for(self.app)
        for headers in ({}, {"Origin": "null"}, {"Origin": "https://evil.example"}):
            with self.subTest(headers=headers):
                response = await visitor.post("/api/session", headers=headers)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["error"]["code"], "origin_denied")
        self.assertEqual(len(visitor.cookies), 0)

    async def test_session_bootstrap_does_not_accept_client_identity(self):
        response = await self.client.post("/api/session", json={"session_id": "attacker-choice"},
                                         headers={"Origin": self.origin})
        self.assertEqual(response.status_code, 422)

    async def test_anonymous_requests_cannot_read_private_data(self):
        visitor = self.client_for(self.app)
        for path in ("/api/session", "/api/mandates", "/api/transactions", "/api/audit"):
            with self.subTest(path=path):
                self.assertEqual((await visitor.get(path)).status_code, 401)

    async def test_write_requires_a_valid_session_bound_csrf_token(self):
        for value in (None, "", "0" * 64, "non-ascii-\u00e9"):
            headers = {"Origin": self.origin}
            if value is not None:
                # HTTP header values must be ASCII; malformed ASCII is enough here.
                headers["X-CSRF-Token"] = value.encode("ascii", "replace").decode()
            with self.subTest(value=value):
                response = await self.client.post("/api/mandates", json=self.terms(), headers=headers)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response.json()["error"]["code"], "csrf_denied")

    async def test_another_sessions_csrf_token_is_not_valid(self):
        visitor = self.client_for(self.app)
        other = await visitor.post("/api/session", headers={"Origin": self.origin})
        response = await self.client.post("/api/mandates", json=self.terms(),
                                         headers=self.headers(**{"X-CSRF-Token": other.json()["csrf_token"]}))
        self.assertEqual(response.status_code, 403)

    async def test_same_session_retrieves_csrf_without_exposing_bearer(self):
        response = await self.client.get("/api/session")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["csrf_token"], self.csrf)
        self.assertNotIn(self.client.cookies.get(self.settings.cookie_name), response.text)

    async def test_default_ask_approve_checkout_and_history(self):
        mandate = await self.mandate()
        quote = await self.quote(mandate)
        self.assertEqual(quote["policy"]["status"], "ASK")
        denied = await self.checkout(quote)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["error"]["code"], "approval_required")
        self.assertEqual((await self.approve(quote)).status_code, 200)
        completed = await self.checkout(quote)
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["authorization_type"], "USER_APPROVAL")
        self.assertEqual(completed.json()["final_total_minor"], 72900)
        state = await self.client.get("/api/mandates/" + mandate["id"])
        self.assertEqual(state.json()["status"], "CONSUMED")
        ledger = await self.client.get("/api/transactions")
        self.assertEqual(ledger.json(), [completed.json()])
        stored = await self.client.get("/api/transactions/" + completed.json()["id"])
        self.assertEqual(stored.json(), completed.json())
        history = await self.client.get(f"/api/quotes/{quote['quote']['id']}/decisions")
        self.assertEqual(history.json()[0], quote["policy"])
        self.assertTrue(all(item["status"] == "ASK" for item in history.json()))
        audit = await self.client.get("/api/audit")
        self.assertIn("checkout_completed", [entry["event_type"] for entry in audit.json()])

    async def test_explicit_auto_approval_three_scenarios(self):
        for product_id, expected in (("P01", "APPROVE"), ("P02", "ASK"), ("P04_DEMO", "BLOCK")):
            with self.subTest(product=product_id):
                mandate = await self.mandate(auto_approve_enabled=True, approval_threshold_minor=75000)
                quote = await self.quote(mandate, product_id)
                self.assertEqual(quote["policy"]["status"], expected)
                if expected == "APPROVE":
                    self.assertEqual((await self.checkout(quote, key="auto-" + product_id)).json()["authorization_type"], "AUTO_POLICY")
                elif expected == "BLOCK":
                    self.assertEqual((await self.approve(quote)).status_code, 409)

    async def test_same_request_replays_and_changed_request_conflicts(self):
        mandate = await self.mandate(auto_approve_enabled=True, approval_threshold_minor=75000)
        first, second = await self.quote(mandate), await self.quote(mandate)
        result = await self.checkout(first, key="idempotent")
        repeated = await self.checkout(first, key="idempotent")
        self.assertEqual(result.json(), repeated.json())
        conflict = await self.checkout(second, key="idempotent")
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json()["error"]["code"], "idempotency_conflict")
        self.assertEqual((await self.checkout(second, key="another-key")).status_code, 409)

    async def test_missing_or_invalid_idempotency_key_is_rejected(self):
        quote = await self.quote(await self.mandate(auto_approve_enabled=True, approval_threshold_minor=75000))
        path = f"/api/quotes/{quote['quote']['id']}/checkout"
        self.assertEqual((await self.client.post(path, headers=self.headers())).status_code, 422)
        for key in ("has spaces", "a/b", "x" * 129):
            with self.subTest(key=key):
                self.assertEqual((await self.checkout(quote, key=key)).status_code, 422)

    async def test_decline_does_not_consume_mandate(self):
        mandate = await self.mandate()
        quote = await self.quote(mandate)
        self.assertEqual((await self.approve(quote, False)).status_code, 200)
        self.assertEqual((await self.checkout(quote)).status_code, 409)
        state = (await self.client.get("/api/mandates/" + mandate["id"])).json()
        self.assertEqual(state["status"], "ACTIVE")

    async def test_foreign_session_ids_do_not_grant_access(self):
        mandate = await self.mandate()
        quote = await self.quote(mandate)
        visitor = self.client_for(self.app)
        bootstrap = await visitor.post("/api/session", headers={"Origin": self.origin})
        headers = {"Origin": self.origin, "X-CSRF-Token": bootstrap.json()["csrf_token"]}
        for path in ("/api/mandates/" + mandate["id"], "/api/quotes/" + quote["quote"]["id"],
                     f"/api/quotes/{quote['quote']['id']}/decisions"):
            with self.subTest(path=path):
                self.assertEqual((await visitor.get(path)).status_code, 404)
        denied = await visitor.post(f"/api/quotes/{quote['quote']['id']}/approval", json={"approve": True}, headers=headers)
        self.assertEqual(denied.status_code, 404)
        denied = await visitor.post(f"/api/quotes/{quote['quote']['id']}/checkout",
                                    headers={**headers, "Idempotency-Key": "foreign"})
        self.assertEqual(denied.status_code, 404)
        self.assertEqual((await visitor.get("/api/mandates")).json(), [])
        self.assertEqual((await visitor.get("/api/transactions")).json(), [])

    async def test_client_cannot_submit_prices_trust_or_decisions(self):
        mandate = await self.mandate()
        for field in ("price_minor", "trusted", "decision", "session_id", "auto_approve_enabled"):
            with self.subTest(field=field):
                body = {"mandate_id": mandate["id"], "product_id": "P01", "payment_id": "demo-card", field: 1}
                response = await self.client.post("/api/quotes", json=body, headers=self.headers())
                self.assertEqual(response.status_code, 422)
        self.assertEqual((await self.client.post("/api/mandates", json=self.terms(status="ACTIVE"), headers=self.headers())).status_code, 422)

    async def test_empty_checkout_command_cannot_hide_authority_fields(self):
        quote = await self.quote(await self.mandate(auto_approve_enabled=True, approval_threshold_minor=75000))
        response = await self.client.post(f"/api/quotes/{quote['quote']['id']}/checkout", json={"approved": True, "price_minor": 1},
                                          headers=self.headers(**{"Idempotency-Key": "forged"}))
        self.assertEqual(response.status_code, 422)
        self.assertEqual((await self.client.get("/api/transactions")).json(), [])

    async def test_wrong_types_and_nonfinite_numbers_are_rejected(self):
        for changes in ({"max_spend_minor": "80000"}, {"auto_approve_enabled": "false"},
                        {"max_points": 1.5}, {"expires_at": "2026-11-18T00:00:00"}):
            with self.subTest(changes=changes):
                response = await self.client.post("/api/mandates", json=self.terms(**changes), headers=self.headers())
                self.assertEqual(response.status_code, 422)
        response = await self.client.post("/api/mandates", content='{"category":"running_shoes","max_spend_minor":NaN,"expires_at":"2026-11-18T00:00:00Z"}',
                                         headers=self.headers(**{"Content-Type": "application/json"}))
        self.assertEqual(response.status_code, 422)

    async def test_validation_error_does_not_echo_secret_input(self):
        sentinel = "sensitive-input-do-not-echo"
        response = await self.client.post("/api/mandates", json=self.terms(max_spend_minor=sentinel), headers=self.headers())
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(sentinel, response.text)
        self.assertIn("fields", response.json()["error"])

    async def test_changed_price_after_approval_requires_new_quote(self):
        mandate = await self.mandate()
        quote = await self.quote(mandate)
        await self.approve(quote)
        item = next(item for item in self.service.get_catalogue().products if item.id == "P01")
        self.service.install_catalogue(products=(updated(item, version=2, price_minor=69899),))
        denied = await self.checkout(quote)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["error"]["code"], "quote_blocked")
        snapshot = await self.client.get("/api/quotes/" + quote["quote"]["id"])
        self.assertEqual(snapshot.json()["quote"]["status"], "STALE")

    async def test_reconfirm_and_revoke_are_owner_scoped_commands(self):
        mandate = await self.mandate()
        quote = await self.quote(mandate)
        changed = await self.client.post(f"/api/mandates/{mandate['id']}/reconfirm",
                                         json=self.terms(max_spend_minor=90000), headers=self.headers())
        self.assertEqual(changed.status_code, 200)
        self.assertEqual(changed.json()["version"], 2)
        stale = await self.client.get("/api/quotes/" + quote["quote"]["id"])
        self.assertEqual(stale.json()["quote"]["status"], "STALE")
        revoked = await self.client.post(f"/api/mandates/{mandate['id']}/revoke", headers=self.headers())
        self.assertEqual(revoked.json()["status"], "REVOKED")

    async def test_session_expiry_is_enforced_on_server(self):
        self.now += timedelta(seconds=self.settings.session_ttl_seconds)
        response = await self.client.get("/api/session")
        self.assertEqual(response.status_code, 401)
        renewed = await self.client.post("/api/session", headers={"Origin": self.origin})
        self.assertEqual(renewed.status_code, 201)
        self.assertNotEqual(renewed.json()["csrf_token"], self.csrf)

    async def test_logout_invalidates_old_token_and_clears_cookie(self):
        token = self.client.cookies.get(self.settings.cookie_name)
        response = await self.client.delete("/api/session", headers=self.headers())
        self.assertEqual(response.status_code, 204)
        self.assertNotIn(self.settings.cookie_name, self.client.cookies)
        self.client.cookies.set(self.settings.cookie_name, token)
        self.assertEqual((await self.client.get("/api/session")).status_code, 401)

    async def test_session_creation_is_rate_limited(self):
        for _ in range(self.settings.session_creation_limit - 1):
            self.assertEqual((await self.client.post("/api/session", headers={"Origin": self.origin})).status_code, 200)
        response = await self.client.post("/api/session", headers={"Origin": self.origin})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "60")

    async def test_cors_allows_only_explicit_origins(self):
        headers = {"Origin": self.origin, "Access-Control-Request-Method": "POST",
                   "Access-Control-Request-Headers": "X-CSRF-Token,Idempotency-Key,Content-Type"}
        response = await self.client.options("/api/quotes", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], self.origin)
        self.assertEqual(response.headers["access-control-allow-credentials"], "true")
        rejected = await self.client.options("/api/quotes", headers={**headers, "Origin": "https://evil.example"})
        self.assertEqual(rejected.status_code, 400)
        self.assertNotIn("access-control-allow-origin", rejected.headers)

    async def test_untrusted_host_is_rejected(self):
        response = await self.client.get("/api/health", headers={"Host": "evil.example"})
        self.assertEqual(response.status_code, 400)

    async def test_api_responses_have_no_store_and_nosniff(self):
        for path in ("/api/session", "/api/catalogue", "/api/audit"):
            response = await self.client.get(path)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    async def test_unexpected_errors_do_not_echo_internal_values(self):
        with patch.object(self.service, "list_transactions", side_effect=RuntimeError("internal-sensitive-detail")):
            with self.assertLogs("guardcart.api", level="ERROR") as logs:
                response = await self.client.get("/api/transactions")
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["error"]["code"], "internal_error")
        self.assertNotIn("internal-sensitive-detail", response.text)
        self.assertNotIn("internal-sensitive-detail", "".join(logs.output))

    async def test_openapi_documents_only_the_implemented_endpoints(self):
        document = (await self.client.get("/openapi.json")).json()
        self.assertIn("/api/quotes/{quote_id}/checkout", document["paths"])
        self.assertIn("/api/session", document["paths"])
        self.assertIn("/api/agent/recommend", document["paths"])
        self.assertNotIn("/api/catalogue/install", document["paths"])

    async def test_production_cookie_uses_host_prefix_and_secure_flag(self):
        config = ApiSettings(environment="production", secure_cookies=True,
                             database_path=Path(self.directory.name) / "secure.sqlite3",
                             allowed_origins=("https://testserver",), allowed_hosts=("testserver",))
        secure_app = create_app(config, clock=lambda: NOW)
        client = self.client_for(secure_app, base_url="https://testserver")
        response = await client.post("/api/session", headers={"Origin": "https://testserver"})
        self.assertEqual(response.status_code, 201)
        cookie = response.headers["set-cookie"]
        self.assertIn("__Host-guardcart_session=", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertNotIn("Domain=", cookie)
        self.assertEqual((await client.get("/api/session")).status_code, 200)


class ApiSettingsTests(unittest.TestCase):
    def test_render_production_defaults_use_exact_platform_hostname(self):
        environment = {"GUARDCART_ENVIRONMENT": "production", "RENDER": "true", "RENDER_EXTERNAL_HOSTNAME": "guardcart-test.onrender.com"}
        with patch.dict(os.environ, environment, clear=True):
            settings = ApiSettings.from_environment()
        self.assertEqual(settings.allowed_origins, ("https://guardcart-test.onrender.com",))
        self.assertIn("guardcart-test.onrender.com", settings.allowed_hosts)
        self.assertTrue(settings.secure_cookies)
        with patch.dict(os.environ, {**environment, "GUARDCART_ALLOWED_ORIGINS": "https://shop.example.test", "GUARDCART_ALLOWED_HOSTS": "shop.example.test"}, clear=True):
            custom = ApiSettings.from_environment()
        self.assertEqual(custom.allowed_origins, ("https://shop.example.test",))
        self.assertEqual(custom.allowed_hosts, ("shop.example.test",))

    def test_render_metadata_rejects_urls_and_untrusted_host_patterns(self):
        for host in ("https://guardcart.onrender.com", "*.onrender.com", "evil.test", "guardcart.onrender.com/path", "guardcart.onrender.com.evil.test"):
            with self.subTest(host=host), patch.dict(os.environ, {"GUARDCART_ENVIRONMENT": "production", "RENDER": "true", "RENDER_EXTERNAL_HOSTNAME": host}, clear=True):
                with self.assertRaises(ValueError):
                    ApiSettings.from_environment()

    def test_invalid_origins_and_wildcards_are_rejected(self):
        for origin in ("*", "null", "http://localhost/path", "https://user:pass@example.test", "http://localhost?x=1"):
            with self.subTest(origin=origin), self.assertRaises(ValidationError):
                ApiSettings(allowed_origins=(origin,))
        for host in ("*", "*.example.test", "example.test/path"):
            with self.subTest(host=host), self.assertRaises(ValidationError):
                ApiSettings(allowed_hosts=(host,))

    def test_production_cannot_use_insecure_cookies_or_http_origins(self):
        for changes in ({"secure_cookies": False, "allowed_origins": ("https://web.example.test",)},
                        {"secure_cookies": True, "allowed_origins": ("http://localhost",)}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                ApiSettings(environment="production", **changes)

    def test_insecure_development_is_loopback_only(self):
        with self.assertRaises(ValidationError):
            ApiSettings(allowed_origins=("http://web.example.test",))
        with self.assertRaises(ValidationError):
            ApiSettings(allowed_hosts=("api.example.test",))


if __name__ == "__main__":
    unittest.main()
