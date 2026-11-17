"""Read only explicit AI settings; never expose the provider key in repr/dumps."""

import os
from pathlib import Path
from typing import Annotated, Self
from urllib.parse import urlparse

from pydantic import Field, SecretStr, StrictBool, StrictInt, StringConstraints, model_validator

from backend.domain.models import Contract


class AiSettings(Contract):
    enabled: StrictBool = False
    base_url: str = "https://zendigikey.shop/v1"
    api_key: SecretStr = Field(default=SecretStr(""), exclude=True, repr=False)
    model: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")] = "gpt-6-luna"
    max_output_tokens: Annotated[StrictInt, Field(ge=128, le=2048)] = 768
    global_call_limit: Annotated[StrictInt, Field(ge=1, le=10000)] = 200
    session_call_limit: Annotated[StrictInt, Field(ge=1, le=1000)] = 40
    calls_per_minute: Annotated[StrictInt, Field(ge=1, le=100)] = 12
    concurrent_runs: Annotated[StrictInt, Field(ge=1, le=4)] = 2

    @model_validator(mode="after")
    def valid_provider(self) -> Self:
        url = urlparse(self.base_url)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment):
            raise ValueError("AI base URL must use HTTPS without credentials, query or fragment.")
        if self.enabled and not self.api_key.get_secret_value().strip():
            raise ValueError("Enabled AI requires a backend API key.")
        return self

    @classmethod
    def from_environment(cls, path: Path | None = None, *, environment=None):
        source = {}
        if path is not None and path.is_file():
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                key, sep, value = line.partition("=")
                if sep and key.strip().startswith("GUARDCART_AI_"):
                    value = value.strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                        value = value[1:-1]
                    source[key.strip()] = value
        env = os.environ if environment is None else environment
        fields = {
            "BASE_URL": "base_url", "API_KEY": "api_key", "MODEL": "model",
            "MAX_OUTPUT_TOKENS": "max_output_tokens", "GLOBAL_CALL_LIMIT": "global_call_limit",
            "SESSION_CALL_LIMIT": "session_call_limit", "CALLS_PER_MINUTE": "calls_per_minute",
            "CONCURRENT_RUNS": "concurrent_runs", "ENABLED": "enabled",
        }
        for suffix in fields:
            key = "GUARDCART_AI_" + suffix
            if key in env:
                source[key] = env[key]
        file_setting = "GUARDCART_AI_API_KEY_FILE"
        key_setting = "GUARDCART_AI_API_KEY"
        if file_setting in env:
            source[file_setting] = env[file_setting]
            if env[file_setting]:
                if env.get(key_setting, "").strip():
                    raise ValueError("Configure either a provider key or a key file, not both.")
                source.pop(key_setting, None)  # An explicit process-level file overrides a local .env key.
        elif key_setting in env:
            source.pop(file_setting, None)
        key_file = source.get(file_setting, "")
        if key_file:
            if source.get(key_setting, "").strip():
                raise ValueError("Configure either a provider key or a key file, not both.")
            try:
                with Path(key_file).open("rb") as stream:
                    raw = stream.read(16385)
                if len(raw) > 16384:
                    raise ValueError("Provider key file is too large.")
                key_value = raw.decode("utf-8-sig").strip()
                if not key_value or "\n" in key_value or "\r" in key_value:
                    raise ValueError("Provider key file must contain a single nonempty key.")
            except (OSError, UnicodeError, ValueError):
                raise ValueError("Provider key file could not be loaded. Check its path, permissions and format.") from None
            source[key_setting] = key_value
        values = {}
        for suffix, name in fields.items():
            value = source.get("GUARDCART_AI_" + suffix)
            if value is None:
                continue
            if name == "enabled":
                if value.lower() not in ("true", "false"):
                    raise ValueError("GUARDCART_AI_ENABLED must be true or false.")
                values[name] = value.lower() == "true"
            elif name in ("max_output_tokens", "global_call_limit", "session_call_limit", "calls_per_minute", "concurrent_runs"):
                values[name] = int(value)
            else:
                values[name] = value
        values.setdefault("enabled", bool(values.get("api_key", "").strip()))
        if "api_key" in values:
            values["api_key"] = SecretStr(values["api_key"])
        return cls.model_validate(values)
