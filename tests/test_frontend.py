import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from backend.api import create_app
from backend.settings import ApiSettings


class FrontendServingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().slow_callback_duration = 1.0
        self.directory = tempfile.TemporaryDirectory(prefix="guardcart-frontend-")
        self.addCleanup(self.directory.cleanup)
        settings = ApiSettings(database_path=Path(self.directory.name) / "ui.sqlite3",
                               allowed_origins=("http://testserver",), allowed_hosts=("testserver",))
        self.app = create_app(settings)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver")
        self.addAsyncCleanup(self.client.aclose)

    async def test_workspace_and_local_assets_are_served(self):
        response = await self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('lang="en"', response.text)
        self.assertIn("GuardCart", response.text)
        for path in ("/app.js", "/app.css", "/format.mjs", "/recovery.mjs", "/view-state.mjs", "/favicon.svg"):
            with self.subTest(path=path):
                self.assertEqual((await self.client.get(path)).status_code, 200)
        self.assertIn("javascript", (await self.client.get("/format.mjs")).headers["content-type"])

    async def test_source_secrets_and_database_files_are_not_static_assets(self):
        for path in ("/.env", "/.env.example", "/backend/main.py", "/var/guardcart.sqlite3", "/%2e%2e/.env"):
            with self.subTest(path=path):
                self.assertEqual((await self.client.get(path)).status_code, 404)

    async def test_asset_mime_types_do_not_depend_on_windows_registry(self):
        expected = {"/": "text/html", "/app.js?v=0.2.1": "text/javascript",
                    "/format.mjs": "text/javascript", "/app.css": "text/css", "/favicon.svg": "image/svg+xml"}
        with patch("starlette.responses.guess_type", return_value=("text/plain", None)):
            for path, content_type in expected.items():
                with self.subTest(path=path):
                    response = await self.client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.headers["content-type"].split(";")[0], content_type)
                    self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    async def test_javascript_range_response_keeps_correct_type_and_length(self):
        with patch("starlette.responses.guess_type", return_value=("text/plain", None)):
            response = await self.client.get("/format.mjs", headers={"Range": "bytes=0-9"})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.headers["content-type"].split(";")[0], "text/javascript")
        self.assertEqual(len(response.content), 10)
        self.assertEqual(response.headers["content-length"], "10")

    async def test_stable_asset_urls_revalidate_after_an_update(self):
        for path in ("/app.js?v=0.2.1", "/view-state.mjs?v=0.2.1", "/app.css?v=0.2.1"):
            with self.subTest(path=path):
                response = await self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-cache")
                self.assertEqual(response.headers["x-content-type-options"], "nosniff")
                changed = await self.client.get(path, headers={"If-None-Match": response.headers["etag"]})
                self.assertEqual(changed.status_code, 304)
                self.assertEqual(changed.headers["cache-control"], "no-cache")

    async def test_workspace_csp_forbids_inline_and_third_party_scripts(self):
        policy = (await self.client.get("/")).headers["content-security-policy"]
        self.assertIn("script-src 'self'", policy)
        self.assertIn("connect-src 'self'", policy)
        self.assertNotIn("unsafe-inline", policy)
        self.assertNotIn("unsafe-eval", policy)

    async def test_frontend_does_not_replace_api_and_docs_routes(self):
        self.assertEqual((await self.client.get("/api/health")).status_code, 200)
        self.assertEqual((await self.client.get("/docs")).status_code, 200)
        self.assertEqual((await self.client.get("/api/not-an-endpoint")).status_code, 404)


if __name__ == "__main__":
    unittest.main()
