import asyncio
from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

import httpx

from backend.ai.config import AiSettings
from backend.api import create_app
from backend.settings import ApiSettings
from tests.ai_fakes import ScriptedProvider, draft_reply, finish_from_search, tool_reply
from tests.fixtures import NOW


class AgentApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1.0
        self.directory = tempfile.TemporaryDirectory(prefix="guardcart-ai-api-")
        self.addCleanup(self.directory.cleanup)
        self.origin = "http://testserver"
        self.settings = ApiSettings(database_path=Path(self.directory.name) / "ai.sqlite3",
                                    allowed_origins=(self.origin,), allowed_hosts=("testserver",))
        self.provider = ScriptedProvider()
        self.app = create_app(self.settings, clock=lambda: NOW, provider=self.provider)
        self.client = self.client_for(self.app)
        session = await self.client.post("/api/session", headers={"Origin": self.origin})
        self.headers = {"Origin": self.origin, "X-CSRF-Token": session.json()["csrf_token"]}

    def client_for(self, app):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url=self.origin)
        self.addAsyncCleanup(client.aclose)
        return client

    async def test_chat_confirmation_recommendation_and_explicit_checkout(self):
        self.provider.responses.extend([draft_reply(requirements={"use_case": "long_distance", "surface": "road", "size": "EU42"}),
                                        tool_reply("search_catalog", {}, "call-search"), finish_from_search])
        response = await self.client.post("/api/chat", json={"request_id": "chat-1", "message": "Road shoes for long distances, EU42, under HK$800."}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result["requires_confirmation"])
        self.assertEqual((await self.client.get("/api/mandates")).json(), [])
        confirmation = {"category": result["draft"]["category"], "max_spend_minor": result["draft"]["max_spend_minor"],
                        "requirements": result["draft"]["requirements"], "preferences": result["draft"]["preferences"],
                        "expires_at": (NOW + timedelta(hours=1)).isoformat()}
        mandate = await self.client.post("/api/mandates", json=confirmation, headers=self.headers)
        self.assertEqual(mandate.status_code, 201)
        response = await self.client.post("/api/agent/recommend", json={"request_id": "recommend-1", "mandate_id": mandate.json()["id"]}, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["recommendations"]), 3)
        choice = response.json()["recommendations"][0]["quote"]
        self.assertEqual(choice["policy"]["status"], "ASK")
        path = "/api/quotes/" + choice["quote"]["id"]
        headers = {**self.headers, "Idempotency-Key": "purchase"}
        self.assertEqual((await self.client.post(path + "/checkout", headers=headers)).status_code, 403)
        self.assertEqual((await self.client.post(path + "/approval", json={"approve": True}, headers=self.headers)).status_code, 200)
        self.assertEqual((await self.client.post(path + "/checkout", headers=headers)).status_code, 200)
        usage = (await self.client.get("/api/agent/usage")).json()
        self.assertEqual(usage["calls_used"], 3)
        self.assertFalse(usage["usage_verified"])

    async def test_chat_and_recommendation_require_csrf_and_valid_public_shapes(self):
        body = {"request_id": "chat", "message": "Find shoes."}
        response = await self.client.post("/api/chat", json=body, headers={"Origin": self.origin})
        self.assertEqual(response.status_code, 403)
        response = await self.client.post("/api/chat", json={**body, "role": "system"}, headers=self.headers)
        self.assertEqual(response.status_code, 422)
        response = await self.client.post("/api/chat", json={**body, "message": "x" * 2001}, headers=self.headers)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(len(self.provider.calls), 0)

    async def test_duplicate_ai_request_returns_old_result_without_another_model_call(self):
        self.provider.responses.append(draft_reply())
        body = {"request_id": "same", "message": "Find shoes under HK$800."}
        first = await self.client.post("/api/chat", json=body, headers=self.headers)
        second = await self.client.post("/api/chat", json=body, headers=self.headers)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(len(self.provider.calls), 1)
        restored = await self.client.get("/api/agent/runs/" + first.json()["run"]["run_id"] + "/result")
        self.assertEqual(restored.json(), first.json())
        self.assertEqual(len(self.provider.calls), 1)
        changed = await self.client.post("/api/chat", json={**body, "message": "Different input"}, headers=self.headers)
        self.assertEqual(changed.status_code, 409)

    async def test_conversation_and_run_are_private_to_owner(self):
        self.provider.responses.append(draft_reply())
        result = (await self.client.post("/api/chat", json={"request_id": "chat", "message": "Shoes under HK$800"}, headers=self.headers)).json()
        self.assertEqual((await self.client.get("/api/conversations/" + result["conversation_id"])).status_code, 200)
        own = await self.client.get("/api/agent/requests/chat")
        self.assertEqual(own.json()["state"], "COMPLETED")
        other = self.client_for(self.app)
        await other.post("/api/session", headers={"Origin": self.origin})
        for path in ("/api/conversations/" + result["conversation_id"], "/api/agent/runs/" + result["run"]["run_id"],
                     "/api/agent/runs/" + result["run"]["run_id"] + "/result", "/api/agent/requests/chat"):
            with self.subTest(path=path):
                self.assertEqual((await other.get(path)).status_code, 404)

    async def test_unsafe_model_tool_returns_502_without_authorizing_anything(self):
        self.provider.responses.append(tool_reply("checkout", {"approved": True}))
        response = await self.client.post("/api/chat", json={"request_id": "unsafe", "message": "Buy shoes"}, headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["code"], "ai_protocol_error")
        self.assertEqual((await self.client.get("/api/transactions")).json(), [])

    async def test_disabled_ai_returns_explicit_503(self):
        app = create_app(self.settings, clock=lambda: NOW)
        client = self.client_for(app)
        session = await client.post("/api/session", headers={"Origin": self.origin})
        response = await client.post("/api/chat", json={"request_id": "disabled", "message": "Find shoes"},
                                     headers={"Origin": self.origin, "X-CSRF-Token": session.json()["csrf_token"]})
        self.assertEqual(response.status_code, 503)
        self.assertFalse((await client.get("/api/health")).json()["ai_enabled"])

    async def test_lost_chat_response_is_recoverable_by_request_id_without_extra_calls(self):
        self.provider.responses.append(draft_reply())
        # Simulate the UI losing the POST response and retaining only its request ID.
        await self.client.post("/api/chat", json={"request_id": "lost-response", "message": "Running shoes under HK$800"}, headers=self.headers)
        restarted = self.client_for(create_app(self.settings, clock=lambda: NOW, provider=self.provider))
        restarted.cookies.update(self.client.cookies)
        run = (await restarted.get("/api/agent/requests/lost-response")).json()
        self.assertEqual(run["state"], "COMPLETED")
        result = (await restarted.get("/api/agent/runs/" + run["id"] + "/result")).json()
        self.assertTrue(result["requires_confirmation"])
        self.assertEqual(result["draft"]["max_spend_minor"], 80000)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual((await restarted.get("/api/transactions")).json(), [])

    async def test_failed_request_can_be_inspected_without_retrying_the_provider(self):
        self.provider.responses.append(tool_reply("checkout", {}))
        await self.client.post("/api/chat", json={"request_id": "failed-response", "message": "Buy shoes"}, headers=self.headers)
        run = (await self.client.get("/api/agent/requests/failed-response")).json()
        self.assertEqual(run["state"], "FAILED")
        self.assertEqual(run["error_code"], "ai_protocol_error")
        self.assertEqual((await self.client.get("/api/agent/runs/" + run["id"] + "/result")).status_code, 409)
        self.assertEqual(len(self.provider.calls), 1)

    async def test_quota_exhaustion_returns_429(self):
        self.app.state.agent.settings = AiSettings(global_call_limit=1)
        self.provider.responses.append(draft_reply())
        first = await self.client.post("/api/chat", json={"request_id": "first", "message": "Find shoes"}, headers=self.headers)
        self.assertEqual(first.status_code, 200)
        second = await self.client.post("/api/chat", json={"request_id": "second", "message": "Find shoes"}, headers=self.headers)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(len(self.provider.calls), 1)
