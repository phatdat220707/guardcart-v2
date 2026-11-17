"""Stored mandates, quote-bound approvals and atomic simulated checkout.

This service accepts product/payment IDs, never client-provided prices or
policy decisions. Catalogue installation is an admin/bootstrap operation and
must not be exposed as a browser or LLM tool endpoint.
"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
import secrets
from typing import Callable
from uuid import uuid4

from backend.domain.models import (
    Approval, AuditEvent, CompletedTransaction, ConfirmedMandate, MandateConfirmation,
    Catalogue, Merchant, PaymentOption, PolicyDecision, Product, Quote, QuoteView, SessionInfo, utc_timestamp,
)
from backend.domain.policy import create_quote as build_quote, evaluate_policy
from backend.services.errors import AuthenticationRequired, Conflict, OperationDenied, ResourceNotFound
from backend.storage import Database


OPEN_QUOTES = {"CREATED", "EVALUATED", "AUTO_ALLOWED", "AWAITING_APPROVAL", "APPROVED"}


def _replace(model, **changes):
    return type(model).model_validate({**model.model_dump(), **changes})


class CheckoutService:
    def __init__(self, database: Database, clock: Callable[[], datetime] | None = None,
                 *, session_ttl_seconds: int = 86400):
        if type(session_ttl_seconds) is not int or not 60 <= session_ttl_seconds <= 604800:
            raise ValueError("Session lifetime must be 60 to 604800 seconds.")
        self.database = database
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.session_ttl_seconds = session_ttl_seconds

    def _now(self):
        return utc_timestamp(self.clock())

    @staticmethod
    def _audit(connection, session_id, event_type, entity_id, timestamp, **details):
        event = AuditEvent(id=str(uuid4()), session_id=session_id, event_type=event_type,
                           entity_id=entity_id, timestamp=timestamp, details=details)
        connection.execute("INSERT INTO audit_events(id,session_id,data) VALUES (?,?,?)",
                           (event.id, session_id, event.model_dump_json()))

    def create_session(self) -> str:
        """Return the bearer token once. Only its digest is stored."""
        token = secrets.token_urlsafe(32)
        with self.database.transaction(write=True) as connection:
            session_id, now = str(uuid4()), self._now()
            connection.execute("INSERT INTO sessions(id,token_hash,created_at) VALUES (?,?,?)",
                               (session_id, sha256(token.encode()).hexdigest(), now.isoformat()))
            self._audit(connection, session_id, "session_created", session_id, now)
        return token

    def _session_info(self, connection, token):
        if not isinstance(token, str) or not 20 <= len(token) <= 200:
            raise AuthenticationRequired()
        row = connection.execute("SELECT id,created_at FROM sessions WHERE token_hash=?",
                                 (sha256(token.encode()).hexdigest(),)).fetchone()
        if row is None:
            raise AuthenticationRequired()
        info = SessionInfo(id=row["id"], created_at=row["created_at"],
                           expires_at=utc_timestamp(row["created_at"]) + timedelta(seconds=self.session_ttl_seconds))
        if not info.created_at <= self._now() < info.expires_at:
            raise AuthenticationRequired()
        return info

    def _session(self, connection, token):
        return self._session_info(connection, token).id

    def get_session(self, token):
        with self.database.transaction() as connection:
            return self._session_info(connection, token)

    def end_session(self, token):
        with self.database.transaction(write=True) as connection:
            session_id, now = self._session(connection, token), self._now()
            # Keep owned records/audit, but invalidate the previous bearer token.
            connection.execute("UPDATE sessions SET token_hash=? WHERE id=?",
                               (sha256(secrets.token_bytes(32)).hexdigest(), session_id))
            self._audit(connection, session_id, "session_ended", session_id, now)

    @staticmethod
    def _read_catalogue(connection):
        return Catalogue(
            merchants=tuple(Merchant.model_validate_json(row["data"]) for row in connection.execute("SELECT data FROM merchants ORDER BY id")),
            products=tuple(Product.model_validate_json(row["data"]) for row in connection.execute("SELECT data FROM products ORDER BY id")),
            payments=tuple(PaymentOption.model_validate_json(row["data"]) for row in connection.execute("SELECT data FROM payments ORDER BY id")),
        )

    def get_catalogue(self):
        with self.database.transaction() as connection:
            return self._read_catalogue(connection)

    @staticmethod
    def _owned(connection, table, entity_id, session_id, model):
        # table is a literal chosen by service code, not a user-supplied name.
        row = connection.execute(f"SELECT data FROM {table} WHERE id=? AND session_id=?",
                                 (entity_id, session_id)).fetchone()
        if row is None:
            raise ResourceNotFound()
        return model.model_validate_json(row["data"])

    @staticmethod
    def _catalogue(connection, table, entity_id, model):
        row = connection.execute(f"SELECT data FROM {table} WHERE id=?", (entity_id,)).fetchone()
        if row is None:
            raise ResourceNotFound()
        return model.model_validate_json(row["data"])

    def install_catalogue(self, *, merchants=(), products=(), payments=()):
        """Admin-only insertion/update; changed records require higher versions."""
        with self.database.transaction(write=True) as connection:
            for table, entries, model in (("merchants", merchants, Merchant), ("products", products, Product),
                                          ("payments", payments, PaymentOption)):
                seen = set()
                for entry in entries:
                    entry = model.model_validate(entry.model_dump(warnings=False))
                    if entry.id in seen:
                        raise ValueError("Catalogue batch contains duplicate IDs.")
                    seen.add(entry.id)
                    old = connection.execute(f"SELECT data,version FROM {table} WHERE id=?", (entry.id,)).fetchone()
                    if old is not None:
                        if json.loads(old["data"]) == json.loads(entry.model_dump_json()):
                            continue
                        if entry.version <= old["version"]:
                            raise Conflict("catalogue_version", "Changed catalogue data requires a higher version.")
                    if table == "products":
                        connection.execute("INSERT INTO products(id,merchant_id,version,data) VALUES (?,?,?,?) "
                                           "ON CONFLICT(id) DO UPDATE SET merchant_id=excluded.merchant_id,version=excluded.version,data=excluded.data",
                                           (entry.id, entry.merchant_id, entry.version, entry.model_dump_json()))
                    else:
                        connection.execute(f"INSERT INTO {table}(id,version,data) VALUES (?,?,?) "
                                           "ON CONFLICT(id) DO UPDATE SET version=excluded.version,data=excluded.data",
                                           (entry.id, entry.version, entry.model_dump_json()))

    def confirm_mandate(self, token: str, terms: MandateConfirmation) -> ConfirmedMandate:
        # Revalidate plain fields, including instances copied without validation.
        terms = MandateConfirmation.model_validate(terms.model_dump(warnings=False))
        with self.database.transaction(write=True) as connection:
            session_id, now = self._session(connection, token), self._now()
            if terms.expires_at <= now:
                raise OperationDenied("mandate_expired", "Mandate expiry must be in the future.")
            mandate = ConfirmedMandate(**terms.model_dump(), id=str(uuid4()), session_id=session_id, created_at=now)
            connection.execute("INSERT INTO mandates(id,session_id,version,status,data) VALUES (?,?,?,?,?)",
                               (mandate.id, session_id, mandate.version, mandate.status, mandate.model_dump_json()))
            self._audit(connection, session_id, "mandate_confirmed", mandate.id, now,
                        version=mandate.version, auto_approve_enabled=mandate.auto_approve_enabled)
        return mandate

    def get_mandate(self, token, mandate_id):
        with self.database.transaction() as connection:
            return self._owned(connection, "mandates", mandate_id, self._session(connection, token), ConfirmedMandate)

    def list_mandates(self, token):
        with self.database.transaction() as connection:
            session_id = self._session(connection, token)
            return tuple(ConfirmedMandate.model_validate_json(row["data"]) for row in connection.execute(
                "SELECT data FROM mandates WHERE session_id=? ORDER BY rowid", (session_id,)))

    @staticmethod
    def _save_quote(connection, quote):
        connection.execute("UPDATE quotes SET status=?,data=? WHERE id=?",
                           (quote.status, quote.model_dump_json(), quote.id))

    def reconfirm_mandate(self, token, mandate_id, terms: MandateConfirmation):
        terms = MandateConfirmation.model_validate(terms.model_dump(warnings=False))
        with self.database.transaction(write=True) as connection:
            session_id, now = self._session(connection, token), self._now()
            old = self._owned(connection, "mandates", mandate_id, session_id, ConfirmedMandate)
            if old.status != "ACTIVE":
                raise Conflict("mandate_inactive", "An inactive mandate cannot be reconfirmed.")
            if terms.expires_at <= now:
                raise OperationDenied("mandate_expired", "Mandate expiry must be in the future.")
            mandate = ConfirmedMandate(**terms.model_dump(), id=old.id, session_id=session_id,
                                        version=old.version + 1, created_at=old.created_at)
            connection.execute("UPDATE mandates SET version=?,data=? WHERE id=?",
                               (mandate.version, mandate.model_dump_json(), mandate.id))
            self._invalidate_quotes(connection, mandate.id, session_id, now)
            self._audit(connection, session_id, "mandate_reconfirmed", mandate.id, now, version=mandate.version)
        return mandate

    def _invalidate_quotes(self, connection, mandate_id, session_id, now):
        for row in connection.execute("SELECT data FROM quotes WHERE mandate_id=?", (mandate_id,)).fetchall():
            quote = Quote.model_validate_json(row["data"])
            if quote.status in OPEN_QUOTES:
                self._save_quote(connection, _replace(quote, status="STALE"))
                self._audit(connection, session_id, "quote_invalidated", quote.id, now)

    def revoke_mandate(self, token, mandate_id):
        with self.database.transaction(write=True) as connection:
            session_id, now = self._session(connection, token), self._now()
            mandate = self._owned(connection, "mandates", mandate_id, session_id, ConfirmedMandate)
            if mandate.status != "ACTIVE":
                raise Conflict("mandate_inactive", "Only an active mandate can be revoked.")
            mandate = _replace(mandate, status="REVOKED")
            connection.execute("UPDATE mandates SET status=?,data=? WHERE id=?",
                               (mandate.status, mandate.model_dump_json(), mandate.id))
            self._invalidate_quotes(connection, mandate.id, session_id, now)
            self._audit(connection, session_id, "mandate_revoked", mandate.id, now)
        return mandate

    def _record_policy(self, connection, session_id, decision):
        connection.execute("INSERT INTO policy_decisions(id,quote_id,data) VALUES (?,?,?)",
                           (decision.id, decision.quote_id, decision.model_dump_json()))
        self._audit(connection, session_id, "policy_evaluated", decision.quote_id, decision.evaluated_at,
                    policy_id=decision.id, status=decision.status)

    def propose_quote(self, token, mandate_id, product_id, payment_id, *, ttl_seconds=300):
        with self.database.transaction(write=True) as connection:
            session_id, now = self._session(connection, token), self._now()
            mandate = self._owned(connection, "mandates", mandate_id, session_id, ConfirmedMandate)
            if mandate.status != "ACTIVE":
                raise Conflict("mandate_inactive", "An active mandate is required.")
            if not mandate.created_at <= now < mandate.expires_at:
                raise OperationDenied("mandate_expired", "Mandate is not currently valid.")
            return self._store_proposal(connection, session_id, now, mandate, product_id, payment_id, ttl_seconds=ttl_seconds)

    def _store_proposal(self, connection, session_id, now, mandate, product_id, payment_id, *, ttl_seconds=300):
        """Internal unit-of-work helper; caller holds the write transaction."""
        product = self._catalogue(connection, "products", product_id, Product)
        merchant = self._catalogue(connection, "merchants", product.merchant_id, Merchant)
        payment = self._catalogue(connection, "payments", payment_id, PaymentOption)
        quote = build_quote(mandate, product, merchant, payment, now=now, ttl_seconds=ttl_seconds)
        decision = evaluate_policy(mandate, quote, product, merchant, payment, now=now)
        state = {"APPROVE": "AUTO_ALLOWED", "ASK": "AWAITING_APPROVAL", "BLOCK": "BLOCKED"}[decision.status]
        quote = _replace(quote, status=state)
        connection.execute("INSERT INTO quotes(id,session_id,mandate_id,status,data) VALUES (?,?,?,?,?)",
                           (quote.id, session_id, mandate.id, quote.status, quote.model_dump_json()))
        self._audit(connection, session_id, "quote_created", quote.id, now, mandate_id=mandate.id)
        self._record_policy(connection, session_id, decision)
        return QuoteView(quote=quote, policy=decision)

    def get_quote(self, token, quote_id):
        with self.database.transaction() as connection:
            session_id = self._session(connection, token)
            quote = self._owned(connection, "quotes", quote_id, session_id, Quote)
            row = connection.execute("SELECT data FROM policy_decisions WHERE quote_id=? ORDER BY sequence DESC LIMIT 1",
                                     (quote.id,)).fetchone()
            return QuoteView(quote=quote, policy=PolicyDecision.model_validate_json(row["data"]))

    def _refresh_policy(self, connection, session_id, quote, mandate, now):
        product = self._catalogue(connection, "products", quote.product_id, Product)
        merchant = self._catalogue(connection, "merchants", product.merchant_id, Merchant)
        payment = self._catalogue(connection, "payments", quote.payment_id, PaymentOption)
        decision = evaluate_policy(mandate, quote, product, merchant, payment, now=now)
        self._record_policy(connection, session_id, decision)
        if decision.status == "BLOCK":
            failed = {item.key for item in decision.checks if item.outcome == "FAIL"}
            state = "EXPIRED" if now >= min(quote.expires_at, mandate.expires_at) else (
                "STALE" if failed & {"catalogue_snapshot", "quote_binding"} else "BLOCKED")
            self._save_quote(connection, _replace(quote, status=state))
        return decision

    def decide_quote(self, token, quote_id, *, approve: bool):
        if type(approve) is not bool:
            raise ValueError("Approval must be a boolean.")
        error, approval = None, None
        with self.database.transaction(write=True) as connection:
            session_id, now = self._session(connection, token), self._now()
            quote = self._owned(connection, "quotes", quote_id, session_id, Quote)
            mandate = self._owned(connection, "mandates", quote.mandate_id, session_id, ConfirmedMandate)
            if quote.status != "AWAITING_APPROVAL":
                raise Conflict("quote_not_pending", "Quote is not awaiting human approval.")
            if approve:
                decision = self._refresh_policy(connection, session_id, quote, mandate, now)
                if decision.status == "BLOCK":
                    error = OperationDenied("quote_blocked", "Quote no longer satisfies the mandate; request a new quote.")
            if error is None:
                approval = Approval(id=str(uuid4()), session_id=session_id, quote_id=quote.id,
                                    mandate_id=mandate.id, mandate_version=mandate.version,
                                    decision="APPROVED" if approve else "DECLINED", created_at=now)
                connection.execute("INSERT INTO approvals(id,session_id,quote_id,mandate_id,data) VALUES (?,?,?,?,?)",
                                   (approval.id, session_id, quote.id, mandate.id, approval.model_dump_json()))
                self._save_quote(connection, _replace(quote, status=approval.decision))
                self._audit(connection, session_id, "quote_approved" if approve else "quote_declined", quote.id, now,
                            approval_id=approval.id, mandate_version=mandate.version)
        # Rejected approvals preserve their fresh policy result and audit event.
        if error is not None:
            raise error
        return approval

    def checkout(self, token, quote_id, *, idempotency_key):
        if not isinstance(idempotency_key, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", idempotency_key) is None:
            raise ValueError("Use a 1-128 character alphanumeric idempotency key, with optional dashes/underscores.")
        if not isinstance(quote_id, str):
            raise ValueError("Quote ID must be a string.")
        fingerprint = sha256(json.dumps({"operation": "simulated_checkout_v1", "quote_id": quote_id},
                                        sort_keys=True).encode()).hexdigest()
        error, transaction = None, None
        with self.database.transaction(write=True) as connection:
            session_id, now = self._session(connection, token), self._now()
            previous = connection.execute("SELECT request_hash,data FROM transactions WHERE session_id=? AND idempotency_key=?",
                                          (session_id, idempotency_key)).fetchone()
            if previous is not None:
                if previous["request_hash"] != fingerprint:
                    raise Conflict("idempotency_conflict", "This idempotency key belongs to a different request.")
                return CompletedTransaction.model_validate_json(previous["data"])
            quote = self._owned(connection, "quotes", quote_id, session_id, Quote)
            mandate = self._owned(connection, "mandates", quote.mandate_id, session_id, ConfirmedMandate)
            if mandate.status != "ACTIVE":
                raise Conflict("mandate_inactive", "Mandate is no longer active.")
            if quote.status not in OPEN_QUOTES:
                raise Conflict("quote_closed", "Quote is closed; request a new quote.")
            decision = self._refresh_policy(connection, session_id, quote, mandate, now)
            approval_id = None
            if decision.status == "BLOCK":
                error = OperationDenied("quote_blocked", "Checkout failed current policy checks; request a new quote.")
            elif decision.status == "ASK":
                row = connection.execute("SELECT data FROM approvals WHERE quote_id=? AND session_id=?",
                                         (quote.id, session_id)).fetchone()
                approval = Approval.model_validate_json(row["data"]) if row else None
                if (approval is None or approval.decision != "APPROVED" or approval.mandate_id != mandate.id
                        or approval.mandate_version != mandate.version or quote.status != "APPROVED"):
                    error = OperationDenied("approval_required", "Explicit approval for this exact quote is required.")
                else:
                    approval_id = approval.id
            if error is not None:
                self._audit(connection, session_id, "checkout_denied", quote.id, now, reason=error.code)
            else:
                transaction = CompletedTransaction(
                    id=str(uuid4()), session_id=session_id, quote_id=quote.id, mandate_id=mandate.id,
                    mandate_version=mandate.version, product_id=quote.product_id, payment_id=quote.payment_id,
                    authorization_type="USER_APPROVAL" if approval_id else "AUTO_POLICY", approval_id=approval_id,
                    final_total_minor=quote.final_total_minor, effective_cost_minor=quote.effective_cost_minor,
                    idempotency_key=idempotency_key, completed_at=now,
                )
                consumed = _replace(mandate, status="CONSUMED")
                changed = connection.execute("UPDATE mandates SET status='CONSUMED',data=? WHERE id=? AND status='ACTIVE' AND version=?",
                                             (consumed.model_dump_json(), mandate.id, mandate.version)).rowcount
                if changed != 1:
                    raise Conflict("mandate_inactive", "Mandate was already consumed or changed.")
                connection.execute("INSERT INTO transactions(id,session_id,quote_id,mandate_id,idempotency_key,request_hash,data) "
                                   "VALUES (?,?,?,?,?,?,?)",
                                   (transaction.id, session_id, quote.id, mandate.id, idempotency_key, fingerprint, transaction.model_dump_json()))
                self._save_quote(connection, _replace(quote, status="COMPLETED"))
                self._invalidate_quotes(connection, mandate.id, session_id, now)
                self._audit(connection, session_id, "mandate_consumed", mandate.id, now, transaction_id=transaction.id)
                self._audit(connection, session_id, "checkout_completed", quote.id, now,
                            transaction_id=transaction.id, authorization_type=transaction.authorization_type,
                            final_total_minor=transaction.final_total_minor)
        if error is not None:
            raise error
        return transaction

    def list_transactions(self, token):
        with self.database.transaction() as connection:
            session_id = self._session(connection, token)
            return tuple(CompletedTransaction.model_validate_json(row["data"]) for row in connection.execute(
                "SELECT data FROM transactions WHERE session_id=? ORDER BY rowid", (session_id,)))

    def get_transaction(self, token, transaction_id):
        with self.database.transaction() as connection:
            return self._owned(connection, "transactions", transaction_id,
                               self._session(connection, token), CompletedTransaction)

    def list_policy_decisions(self, token, quote_id):
        with self.database.transaction() as connection:
            session_id = self._session(connection, token)
            quote = self._owned(connection, "quotes", quote_id, session_id, Quote)
            return tuple(PolicyDecision.model_validate_json(row["data"]) for row in connection.execute(
                "SELECT data FROM policy_decisions WHERE quote_id=? ORDER BY sequence", (quote.id,)))

    def list_audit(self, token):
        with self.database.transaction() as connection:
            session_id = self._session(connection, token)
            return tuple(AuditEvent.model_validate_json(row["data"]) for row in connection.execute(
                "SELECT data FROM audit_events WHERE session_id=? ORDER BY sequence", (session_id,)))
