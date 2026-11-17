"""Session-scoped simulated commerce and bounded Agent HTTP workflows."""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
import hmac
import logging
import re
import secrets
from threading import Lock
from time import monotonic
from typing import Annotated, Callable

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyCookie
from fastapi.staticfiles import StaticFiles
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.api_models import ApprovalRequest, EmptyCommand, QuoteRequest, SessionResponse
from backend.ai.config import AiSettings
from backend.ai.errors import AiBusy, AiContextChanged, AiProtocolError, AiQuotaExceeded, AiUnavailable
from backend.ai.models import ChatRequest, ChatResult, ConversationView, RecommendRequest, RecommendationResult, RunView, UsageView
from backend.ai.provider import ChatCompletionsProvider
from backend.ai.service import AgentService
from backend.domain.models import (
    Approval, AuditEvent, Catalogue, CompletedTransaction, ConfirmedMandate,
    EntityId, MandateConfirmation, PolicyDecision, QuoteView, SessionInfo,
)
from backend.services.checkout import CheckoutService
from backend.services.errors import (
    AuthenticationRequired, Conflict, OperationDenied, ResourceNotFound, ServiceError, StorageBusy,
)
from backend.settings import ApiSettings, ROOT
from backend.storage import Database


def _csrf(token: str) -> str:
    return hmac.new(token.encode(), b"guardcart-csrf-v1", sha256).hexdigest()


def _error(status, code, message, **extra):
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, **extra}},
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@dataclass(frozen=True)
class SessionContext:
    token: str = field(repr=False)
    info: SessionInfo


class SessionCreationLimiter:
    """Single-process, per-peer creation limit; not a distributed quota."""

    def __init__(self, limit):
        self.limit, self.records, self.lock = limit, {}, Lock()

    def allow(self, peer):
        now = monotonic()
        with self.lock:
            self.records = {key: values for key, values in self.records.items() if values and values[-1] > now - 60}
            if peer not in self.records and len(self.records) >= 4096:
                return False
            values = self.records.setdefault(peer, deque())
            while values and values[0] <= now - 60:
                values.popleft()
            if len(values) >= self.limit:
                return False
            values.append(now)
            return True


def create_app(settings: ApiSettings | None = None, *, clock: Callable[[], datetime] | None = None,
               seed_catalogue: bool = True, ai_settings: AiSettings | None = None, provider=None) -> FastAPI:
    settings = settings or ApiSettings.from_environment()
    service = CheckoutService(Database(settings.database_path), clock=clock,
                              session_ttl_seconds=settings.session_ttl_seconds)
    if seed_catalogue:
        current = service.get_catalogue()
        if not current.merchants and not current.products and not current.payments:
            catalogue = Catalogue.model_validate_json((ROOT / "data" / "catalogue.json").read_text(encoding="utf-8"))
            service.install_catalogue(merchants=catalogue.merchants, products=catalogue.products, payments=catalogue.payments)
    if provider is None and ai_settings is not None and ai_settings.enabled:
        provider = ChatCompletionsProvider(ai_settings)
    agent = AgentService(service, provider=provider, settings=ai_settings)
    app = FastAPI(title="GuardCart v2 API", version="0.2.0", debug=False,
                  description="Session-scoped, deterministic purchase authorization and simulated checkout. All catalogue and payment data are demo data.")
    app.state.service, app.state.settings, app.state.agent = service, settings, agent
    limiter = SessionCreationLimiter(settings.session_creation_limit)
    cookie = APIKeyCookie(name=settings.cookie_name, auto_error=False)

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        status = 401 if isinstance(exc, AuthenticationRequired) else (
            404 if isinstance(exc, ResourceNotFound) else 409 if isinstance(exc, Conflict) else
            403 if isinstance(exc, OperationDenied) else 503 if isinstance(exc, (StorageBusy, AiUnavailable)) else
            502 if isinstance(exc, AiProtocolError) else 429 if isinstance(exc, AiQuotaExceeded) else
            409 if isinstance(exc, (AiBusy, AiContextChanged)) else 400)
        response = _error(status, exc.code, str(exc))
        if status == 503:
            response.headers["Retry-After"] = "1"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        fields = [{"path": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
                  for error in exc.errors()]
        # Do not echo input values, cookies, provider responses or validator contexts.
        return _error(422, "validation_error", "Request fields are invalid.", fields=fields)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logging.getLogger("guardcart.api").error("Unhandled API exception: %s", type(exc).__name__)
        return _error(500, "internal_error", "An unexpected server error occurred.")

    @app.middleware("http")
    async def transport_controls(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
            if request.headers.get("origin") not in settings.allowed_origins:
                return _error(403, "origin_denied", "An allowed Origin header is required for state-changing requests.")
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
        elif request.url.path in ("/", "/index.html"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Cache-Control"] = "no-cache"
        elif request.url.path.endswith((".js", ".mjs", ".css", ".svg")):
            # Buildless assets retain their paths across updates; revalidate them.
            response.headers["Cache-Control"] = "no-cache"
            response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def authenticated(request: Request, token: Annotated[str | None, Depends(cookie)],
                      csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None):
        info = service.get_session(token)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if (not isinstance(csrf_token, str) or re.fullmatch(r"[0-9a-f]{64}", csrf_token) is None
                    or not secrets.compare_digest(csrf_token, _csrf(token))):
                raise OperationDenied("csrf_denied", "A valid session-bound X-CSRF-Token header is required.")
        return SessionContext(token=token, info=info)

    Auth = Annotated[SessionContext, Depends(authenticated)]

    @app.get("/api/health", tags=["System"])
    def health():
        with service.database.transaction() as connection:
            connection.execute("SELECT 1").fetchone()
        return {"status": "ok", "mode": "simulation", "ai_enabled": agent.enabled}

    @app.get("/api/catalogue", response_model=Catalogue, tags=["Catalogue"])
    def catalogue():
        return service.get_catalogue()

    @app.post("/api/session", response_model=SessionResponse, tags=["Session"])
    def start_session(request: Request, response: Response, payload: EmptyCommand | None = None):
        peer = request.client.host if request.client else "unknown"
        if not limiter.allow(peer):
            result = _error(429, "session_rate_limit", "Too many session requests; try again later.")
            result.headers["Retry-After"] = "60"
            return result
        token = request.cookies.get(settings.cookie_name)
        try:
            info = service.get_session(token)
        except AuthenticationRequired:
            token = service.create_session()
            info = service.get_session(token)
            response.status_code = 201
        response.set_cookie(settings.cookie_name, token, max_age=settings.session_ttl_seconds,
                            httponly=True, secure=settings.secure_cookies, samesite="lax", path="/")
        return SessionResponse(**info.model_dump(), csrf_token=_csrf(token))

    @app.get("/api/session", response_model=SessionResponse, tags=["Session"])
    def current_session(context: Auth):
        return SessionResponse(**context.info.model_dump(), csrf_token=_csrf(context.token))

    @app.delete("/api/session", status_code=204, tags=["Session"])
    def logout(context: Auth, payload: EmptyCommand | None = None):
        service.end_session(context.token)
        response = Response(status_code=204)
        response.delete_cookie(settings.cookie_name, path="/", secure=settings.secure_cookies,
                               httponly=True, samesite="lax")
        return response

    @app.post("/api/mandates", response_model=ConfirmedMandate, status_code=201, tags=["Mandates"])
    def confirm_mandate(terms: MandateConfirmation, context: Auth):
        return service.confirm_mandate(context.token, terms)

    @app.get("/api/mandates", response_model=tuple[ConfirmedMandate, ...], tags=["Mandates"])
    def list_mandates(context: Auth):
        return service.list_mandates(context.token)

    @app.get("/api/mandates/{mandate_id}", response_model=ConfirmedMandate, tags=["Mandates"])
    def get_mandate(mandate_id: EntityId, context: Auth):
        return service.get_mandate(context.token, mandate_id)

    @app.post("/api/mandates/{mandate_id}/reconfirm", response_model=ConfirmedMandate, tags=["Mandates"])
    def reconfirm(mandate_id: EntityId, terms: MandateConfirmation, context: Auth):
        return service.reconfirm_mandate(context.token, mandate_id, terms)

    @app.post("/api/mandates/{mandate_id}/revoke", response_model=ConfirmedMandate, tags=["Mandates"])
    def revoke(mandate_id: EntityId, context: Auth, payload: EmptyCommand | None = None):
        return service.revoke_mandate(context.token, mandate_id)

    @app.post("/api/quotes", response_model=QuoteView, status_code=201, tags=["Quotes"])
    def propose_quote(body: QuoteRequest, context: Auth):
        return service.propose_quote(context.token, body.mandate_id, body.product_id, body.payment_id,
                                     ttl_seconds=body.ttl_seconds)

    @app.get("/api/quotes/{quote_id}", response_model=QuoteView, tags=["Quotes"])
    def get_quote(quote_id: EntityId, context: Auth):
        return service.get_quote(context.token, quote_id)

    @app.get("/api/quotes/{quote_id}/decisions", response_model=tuple[PolicyDecision, ...], tags=["Quotes"])
    def policy_history(quote_id: EntityId, context: Auth):
        return service.list_policy_decisions(context.token, quote_id)

    @app.post("/api/quotes/{quote_id}/approval", response_model=Approval, tags=["Quotes"])
    def approve_quote(quote_id: EntityId, body: ApprovalRequest, context: Auth):
        return service.decide_quote(context.token, quote_id, approve=body.approve)

    @app.post("/api/quotes/{quote_id}/checkout", response_model=CompletedTransaction, tags=["Checkout"])
    def checkout(quote_id: EntityId, context: Auth,
                 idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128,
                                                       pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")],
                 payload: EmptyCommand | None = None):
        return service.checkout(context.token, quote_id, idempotency_key=idempotency_key)

    @app.get("/api/transactions", response_model=tuple[CompletedTransaction, ...], tags=["Checkout"])
    def transactions(context: Auth):
        return service.list_transactions(context.token)

    @app.get("/api/transactions/{transaction_id}", response_model=CompletedTransaction, tags=["Checkout"])
    def transaction(transaction_id: EntityId, context: Auth):
        return service.get_transaction(context.token, transaction_id)

    @app.get("/api/audit", response_model=tuple[AuditEvent, ...], tags=["Audit"])
    def audit(context: Auth):
        return service.list_audit(context.token)

    @app.post("/api/chat", response_model=ChatResult, tags=["Agent"])
    def chat(body: ChatRequest, context: Auth):
        return agent.chat(context.token, body)

    @app.get("/api/conversations/{conversation_id}", response_model=ConversationView, tags=["Agent"])
    def conversation(conversation_id: EntityId, context: Auth):
        return agent.conversation(context.token, conversation_id)

    @app.post("/api/agent/recommend", response_model=RecommendationResult, tags=["Agent"])
    def recommend(body: RecommendRequest, context: Auth):
        return agent.recommend(context.token, body)

    @app.get("/api/agent/runs/{run_id}", response_model=RunView, tags=["Agent"])
    def agent_run(run_id: EntityId, context: Auth):
        return agent.run(context.token, run_id=run_id)

    @app.get("/api/agent/runs/{run_id}/result", response_model=ChatResult | RecommendationResult, tags=["Agent"])
    def agent_result(run_id: EntityId, context: Auth):
        return agent.result(context.token, run_id)

    @app.get("/api/agent/requests/{request_id}", response_model=RunView, tags=["Agent"])
    def agent_request(request_id: EntityId, context: Auth):
        return agent.run(context.token, request_id=request_id)

    @app.get("/api/agent/usage", response_model=UsageView, tags=["Agent"])
    def agent_usage(context: Auth):
        return agent.usage(context.token)

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins), allow_credentials=True,
                       allow_methods=["GET", "POST", "DELETE"],
                       allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key"])
    app.mount("/", StaticFiles(directory=ROOT / "frontend" / "dist", html=True), name="workspace")
    return app
