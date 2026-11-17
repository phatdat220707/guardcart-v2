"""Run with: python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000."""

from backend.api import create_app
from backend.ai.config import AiSettings
from backend.settings import ROOT


try:
    ai_settings = AiSettings.from_environment(ROOT / ".env")
except ValueError:
    raise RuntimeError("Invalid AI configuration. Check the local AI settings without sharing the key.") from None
app = create_app(ai_settings=ai_settings)
