import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from pydantic import ValidationError

from backend.ai.config import AiSettings
from backend.ai.errors import AiProtocolError, AiUnavailable
from backend.ai.provider import ChatCompletionsProvider, ProviderReply, validated_calls
from tests.ai_fakes import draft_reply


class ProviderTests(unittest.TestCase):
    def settings(self):
        return AiSettings(enabled=True, base_url="https://gateway.example/v1", api_key="fake-key-for-tests")

    def execute(self, handler):
        client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
        with patch("backend.ai.provider.httpx.Client", return_value=client):
            return ChatCompletionsProvider(self.settings()).complete([{"role": "user", "content": "Find shoes"}], [], "auto")

    def test_request_uses_configured_url_and_key_only_in_header(self):
        reply = draft_reply()

        def handler(request):
            self.assertEqual(str(request.url), "https://gateway.example/v1/chat/completions")
            self.assertEqual(request.headers["Authorization"], "Bearer fake-key-for-tests")
            body = json.loads(request.content)
            self.assertEqual(body["max_tokens"], 768)
            self.assertNotIn("fake-key-for-tests", request.content.decode())
            return httpx.Response(200, json={"choices": [{"message": reply.message}], "usage": reply.usage})

        self.assertEqual(validated_calls(self.execute(handler))[0][1], "propose_mandate")

    def test_redirects_and_http_errors_are_not_followed_or_echoed(self):
        for status in (302, 400, 401, 429, 500):
            seen = []

            def handler(request):
                seen.append(str(request.url))
                return httpx.Response(status, headers={"Location": "https://other.example/"},
                                      content=b"secret-error-body")

            with self.subTest(status=status), self.assertRaises(AiUnavailable) as caught:
                self.execute(handler)
            self.assertEqual(caught.exception.upstream_status, status)
            self.assertNotIn("secret-error-body", str(caught.exception))
            self.assertEqual(len(seen), 1)

    def test_transport_timeout_does_not_echo_details_or_retry(self):
        seen = []

        def handler(request):
            seen.append(True)
            raise httpx.ReadTimeout("private transport detail", request=request)

        with self.assertRaises(AiUnavailable) as caught:
            self.execute(handler)
        self.assertEqual(len(seen), 1)
        self.assertNotIn("private transport detail", str(caught.exception))

    def test_oversized_or_invalid_response_is_rejected(self):
        for content in (b"x" * 512001, b"not-json", b'{"choices": []}'):
            with self.subTest(size=len(content)), self.assertRaises(AiProtocolError):
                self.execute(lambda request: httpx.Response(200, content=content))

    def test_usage_is_sanitized_and_private_reasoning_is_not_retained(self):
        reply = draft_reply()
        message = {**reply.message, "reasoning_content": "private internal reasoning"}
        result = self.execute(lambda request: httpx.Response(200, json={
            "choices": [{"message": message}],
            "usage": {"prompt_tokens": -1, "completion_tokens": "20", "total_tokens": True},
        }))
        self.assertEqual(result.usage, {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None})
        self.assertNotIn("reasoning_content", result.message)

    def test_tool_ids_arguments_and_count_are_validated(self):
        original = draft_reply().message["tool_calls"][0]
        for calls in ([], [original, original], [{**original, "id": "invalid id"}],
                      [{**original, "function": {"name": "propose_mandate", "arguments": "[]"}}]):
            with self.subTest(calls=calls), self.assertRaises(AiProtocolError):
                validated_calls(ProviderReply(message={"tool_calls": calls}, usage={}))

    def test_secret_config_is_not_in_repr_or_dump(self):
        settings = self.settings()
        self.assertNotIn("fake-key-for-tests", repr(settings))
        self.assertNotIn("fake-key-for-tests", settings.model_dump_json())

    def test_local_ai_configuration_does_not_mutate_environment(self):
        with tempfile.TemporaryDirectory(prefix="guardcart-config-") as directory:
            path = Path(directory) / ".env"
            # This fixture is intentionally fake; it never uses the real project .env.
            with patch.object(Path, "read_text", return_value="GUARDCART_AI_API_KEY=fake-local-key\nGUARDCART_AI_MODEL=model-a\n"), \
                 patch.object(Path, "is_file", return_value=True):
                settings = AiSettings.from_environment(path, environment={"GUARDCART_AI_MODEL": "model-b"})
            self.assertTrue(settings.enabled)
            self.assertEqual(settings.model, "model-b")
            self.assertNotIn("fake-local-key", repr(settings))

    def test_bad_provider_urls_and_missing_keys_are_rejected(self):
        for url in ("http://example.test/v1", "https://user:pass@example.test/v1", "https://example.test/v1?key=x"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                AiSettings(base_url=url)
        with self.assertRaises(ValidationError):
            AiSettings(enabled=True)
