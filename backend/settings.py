"""Non-secret transport configuration; this module never reads the AI key."""

import os
from pathlib import Path
from typing import Annotated, Literal, Self
from urllib.parse import urlparse

from pydantic import Field, StrictBool, StrictInt, model_validator

from backend.domain.models import Contract


ROOT = Path(__file__).resolve().parents[1]


class ApiSettings(Contract):
    environment: Literal["development", "production"] = "development"
    database_path: Path = ROOT / "var" / "guardcart.sqlite3"
    allowed_origins: tuple[str, ...] = (
        "http://127.0.0.1:8000", "http://localhost:8000",
        "http://127.0.0.1:5173", "http://localhost:5173",
    )
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    secure_cookies: StrictBool = False
    session_ttl_seconds: Annotated[StrictInt, Field(ge=60, le=604800)] = 86400
    session_creation_limit: Annotated[StrictInt, Field(ge=1, le=1000)] = 20

    @model_validator(mode="after")
    def validate_transport(self) -> Self:
        if not self.allowed_origins or not self.allowed_hosts:
            raise ValueError("Explicit allowed origins and hosts are required.")
        for origin in self.allowed_origins:
            parsed = urlparse(origin)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.path
                    or parsed.query or parsed.fragment or parsed.username or parsed.password):
                raise ValueError("Origins must be exact HTTP(S) scheme/host/port values without paths or credentials.")
        if any("*" in host or "/" in host or not host for host in self.allowed_hosts):
            raise ValueError("Host wildcards and paths are not permitted.")
        if not self.secure_cookies:
            if self.environment == "production" or any(host not in ("localhost", "127.0.0.1", "testserver") for host in self.allowed_hosts) or any(
                urlparse(origin).hostname not in ("localhost", "127.0.0.1", "testserver")
                for origin in self.allowed_origins
            ):
                raise ValueError("Insecure cookies are restricted to local development origins.")
        if self.environment == "production" and any(not origin.startswith("https://") for origin in self.allowed_origins):
            raise ValueError("Production origins must use HTTPS.")
        return self

    @property
    def cookie_name(self):
        return "__Host-guardcart_session" if self.secure_cookies else "guardcart_session"

    @classmethod
    def from_environment(cls):
        environment = os.environ.get("GUARDCART_ENVIRONMENT", "development")
        values = {"environment": environment, "secure_cookies": environment == "production"}
        for name, field in (("GUARDCART_DATABASE_PATH", "database_path"),):
            if name in os.environ:
                values[field] = os.environ[name]
        for name, field in (("GUARDCART_ALLOWED_ORIGINS", "allowed_origins"), ("GUARDCART_ALLOWED_HOSTS", "allowed_hosts")):
            if name in os.environ:
                values[field] = tuple(part.strip() for part in os.environ[name].split(",") if part.strip())
        for name, field in (("GUARDCART_SESSION_TTL_SECONDS", "session_ttl_seconds"),
                            ("GUARDCART_SESSION_CREATION_LIMIT", "session_creation_limit")):
            if name in os.environ:
                values[field] = int(os.environ[name])
        if "GUARDCART_SECURE_COOKIES" in os.environ:
            raw = os.environ["GUARDCART_SECURE_COOKIES"].lower()
            if raw not in ("true", "false"):
                raise ValueError("GUARDCART_SECURE_COOKIES must be true or false.")
            values["secure_cookies"] = raw == "true"
        return cls.model_validate(values)
