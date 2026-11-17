from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from backend.ai.config import AiSettings
from backend.server import main, parse_port
from tools.package_release import REQUIRED, collect_sources, manifest_for, package


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="guardcart-release-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_secret_file_overrides_dotenv_key_without_exposing_either(self):
        key_file = self.root / "key.txt"
        key_file.write_text("fake-file-key\n")
        env_file = self.root / ".env"
        env_file.write_text("GUARDCART_AI_API_KEY=fake-old-key\n")
        settings = AiSettings.from_environment(env_file, environment={"GUARDCART_AI_API_KEY_FILE": str(key_file)})
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.api_key.get_secret_value(), "fake-file-key")
        self.assertNotIn("fake-file-key", repr(settings))
        self.assertNotIn("fake-file-key", settings.model_dump_json())
        direct = AiSettings.from_environment(env_file, environment={"GUARDCART_AI_API_KEY": "fake-explicit-key"})
        self.assertEqual(direct.api_key.get_secret_value(), "fake-explicit-key")

    def test_missing_multiline_empty_and_oversized_secret_files_fail_safely(self):
        key_file = self.root / "key.txt"
        for contents in (None, "", "fake-first\nfake-second", "x" * 16385):
            if contents is not None:
                key_file.write_text(contents)
            with self.subTest(size=len(contents) if contents is not None else None), self.assertRaises(ValueError) as caught:
                AiSettings.from_environment(environment={"GUARDCART_AI_API_KEY_FILE": str(key_file)})
            self.assertNotIn("fake-first", str(caught.exception))
            self.assertNotIn(str(key_file), str(caught.exception))

    def test_two_explicit_provider_key_sources_are_rejected(self):
        with self.assertRaises(ValueError):
            AiSettings.from_environment(environment={"GUARDCART_AI_API_KEY_FILE": "missing", "GUARDCART_AI_API_KEY": "fake-key"})

    def test_entrypoint_checks_production_configuration_without_opening_storage_or_network(self):
        env = {"PORT": "9000", "GUARDCART_ENVIRONMENT": "production", "GUARDCART_ALLOWED_ORIGINS": "https://guardcart.example.test",
               "GUARDCART_ALLOWED_HOSTS": "guardcart.example.test", "GUARDCART_AI_ENABLED": "false"}
        output = io.StringIO()
        with patch.dict(os.environ, env, clear=True), patch("backend.server.ROOT", self.root), redirect_stdout(output):
            self.assertEqual(main(["--check"]), 0)
        self.assertEqual(json.loads(output.getvalue())["provider_calls"], 0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_entrypoint_rejects_invalid_port_and_insecure_production(self):
        for value in ("0", "65536", "8000.0", "8000\n", "-1", "１２３", "secret-invalid-port"):
            with self.subTest(port=value), self.assertRaises(ValueError):
                parse_port(value)
        for env in ({"PORT": "secret-invalid-port"}, {"GUARDCART_ENVIRONMENT": "production", "GUARDCART_SECURE_COOKIES": "false"}):
            output = io.StringIO()
            with patch.dict(os.environ, env, clear=True), patch("backend.server.ROOT", self.root), redirect_stdout(output):
                self.assertEqual(main(["--check"]), 2)
            self.assertNotIn("secret-invalid-port", output.getvalue())

    def populate(self):
        for name in REQUIRED:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("synthetic source\n")

    def test_archive_excludes_runtime_data_secrets_and_caches_and_has_verified_manifest(self):
        self.populate()
        for name in (".env", "var/live.sqlite3", "reports/private.json", "deploy/secrets/ai-key.txt", "backend/__pycache__/cached.py", "backend/unwanted.zip"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("private-runtime-value")
        (self.root / ".env.example").write_text("GUARDCART_AI_API_KEY=\n")
        result = package(self.root)
        with zipfile.ZipFile(result["archive"]) as archive:
            names = archive.namelist()
            self.assertIn("guardcart-v2/.env.example", names)
            self.assertNotIn("guardcart-v2/.env", names)
            self.assertTrue(all("private-runtime-value" not in archive.read(name).decode() for name in names))
            manifest = json.loads(archive.read("guardcart-v2/release-manifest.json"))
            self.assertEqual(manifest["source_sha256"], result["source_sha256"])
        self.assertEqual(package(self.root)["archive_sha256"], result["archive_sha256"])

    def test_packaging_blocks_known_credential_patterns_without_printing_values(self):
        self.populate()
        leaked = "sk-" + "A" * 40
        (self.root / "backend/leak.py").write_text('KEY = "' + leaked + '"')
        with self.assertRaises(ValueError) as caught:
            collect_sources(self.root)
        self.assertNotIn(leaked, str(caught.exception))
        self.assertIn("backend/leak.py", str(caught.exception))
        (self.root / "backend/leak.py").unlink()
        for value in ("accidental-live-value", "'quoted-accidental-value'", '"quoted-accidental-value"'):
            (self.root / ".env.example").write_text("GUARDCART_AI_API_KEY=" + value + "\n")
            with self.subTest(value=value), self.assertRaises(ValueError):
                collect_sources(self.root)
        (self.root / ".env.example").write_text("GUARDCART_AI_API_KEY=\nGUARDCART_AI_MODEL=fake-model\n")
        self.assertIn(".env.example", collect_sources(self.root))

    def test_changed_source_changes_manifest_and_check_mode_creates_no_archive(self):
        self.populate()
        before = package(self.root, check_only=True)
        self.assertFalse((self.root / "releases").exists())
        (self.root / "backend/main.py").write_text("updated source")
        self.assertNotEqual(manifest_for(collect_sources(self.root))["source_sha256"], before["source_sha256"])
