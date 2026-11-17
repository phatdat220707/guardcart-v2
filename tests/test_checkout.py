from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import multiprocessing
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from backend.domain.models import MandateConfirmation
from backend.services.checkout import CheckoutService
from backend.services.errors import AuthenticationRequired, Conflict, OperationDenied, ResourceNotFound, ServiceError
from backend.storage import Database
from tests.fixtures import NOW, merchant, payment, product, updated


def terms(**changes):
    return MandateConfirmation.model_validate({
        "category": "running_shoes", "max_spend_minor": 80000,
        "expires_at": NOW + timedelta(hours=1), **changes,
    })


def process_checkout(path, token, quote_id, key, barrier, queue):
    """Spawn-safe worker: each process owns its own store and connection."""
    try:
        service = CheckoutService(Database(path), clock=lambda: NOW)
        barrier.wait(timeout=20)
        transaction = service.checkout(token, quote_id, idempotency_key=key)
        queue.put(("completed", transaction.id))
    except ServiceError as exc:
        queue.put(("denied", exc.code))
    except Exception as exc:
        queue.put(("error", type(exc).__name__))


class CheckoutTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="guardcart-test-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "test.sqlite3"
        self.database = Database(self.path)
        self.now = NOW
        self.service = CheckoutService(self.database, clock=lambda: self.now)
        self.service.install_catalogue(
            merchants=(merchant(),),
            products=(product(), product(id="P02", price_minor=76000, shipping_minor=1000),
                      product(id="P04", price_minor=80000, shipping_minor=2000)),
            payments=(payment(),),
        )
        self.token = self.service.create_session()

    def create(self, *, auto=False, expires_at=None):
        values = {"auto_approve_enabled": auto, "approval_threshold_minor": 75000 if auto else None}
        if expires_at:
            values["expires_at"] = expires_at
        return self.service.confirm_mandate(self.token, terms(**values))

    def quote(self, mandate, product_id="P01", ttl_seconds=300):
        return self.service.propose_quote(self.token, mandate.id, product_id, "card-1", ttl_seconds=ttl_seconds)

    def approved_quote(self):
        mandate = self.create()
        view = self.quote(mandate)
        approval = self.service.decide_quote(self.token, view.quote.id, approve=True)
        return mandate, view, approval

    def test_default_checkout_requires_explicit_approval(self):
        mandate = self.create()
        view = self.quote(mandate)
        self.assertEqual(view.policy.status, "ASK")
        with self.assertRaises(OperationDenied) as caught:
            self.service.checkout(self.token, view.quote.id, idempotency_key="purchase-1")
        self.assertEqual(caught.exception.code, "approval_required")
        self.assertEqual(self.service.get_mandate(self.token, mandate.id).status, "ACTIVE")
        approval = self.service.decide_quote(self.token, view.quote.id, approve=True)
        transaction = self.service.checkout(self.token, view.quote.id, idempotency_key="purchase-1")
        self.assertEqual(transaction.authorization_type, "USER_APPROVAL")
        self.assertEqual(transaction.approval_id, approval.id)
        self.assertEqual(self.service.get_mandate(self.token, mandate.id).status, "CONSUMED")

    def test_opted_in_729_checkout_uses_auto_policy(self):
        mandate = self.create(auto=True)
        view = self.quote(mandate)
        self.assertEqual(view.policy.status, "APPROVE")
        transaction = self.service.checkout(self.token, view.quote.id, idempotency_key="auto-1")
        self.assertEqual(transaction.authorization_type, "AUTO_POLICY")
        self.assertIsNone(transaction.approval_id)

    def test_770_requires_approval_even_when_auto_enabled(self):
        view = self.quote(self.create(auto=True), "P02")
        with self.assertRaises(OperationDenied):
            self.service.checkout(self.token, view.quote.id, idempotency_key="ask-1")
        self.service.decide_quote(self.token, view.quote.id, approve=True)
        transaction = self.service.checkout(self.token, view.quote.id, idempotency_key="ask-1")
        self.assertEqual(transaction.final_total_minor, 77000)
        self.assertEqual(transaction.authorization_type, "USER_APPROVAL")

    def test_block_cannot_be_approved_or_checked_out(self):
        mandate = self.create(auto=True)
        view = self.quote(mandate, "P04")
        self.assertEqual(view.policy.status, "BLOCK")
        with self.assertRaises(Conflict):
            self.service.decide_quote(self.token, view.quote.id, approve=True)
        with self.assertRaises(Conflict):
            self.service.checkout(self.token, view.quote.id, idempotency_key="blocked-1")
        self.assertEqual(self.service.list_transactions(self.token), ())
        self.assertEqual(self.service.get_mandate(self.token, mandate.id).status, "ACTIVE")

    def test_decline_closes_only_the_quote(self):
        mandate = self.create()
        old = self.quote(mandate)
        self.service.decide_quote(self.token, old.quote.id, approve=False)
        with self.assertRaises(Conflict):
            self.service.checkout(self.token, old.quote.id, idempotency_key="declined-1")
        fresh = self.quote(mandate)
        self.service.decide_quote(self.token, fresh.quote.id, approve=True)
        self.service.checkout(self.token, fresh.quote.id, idempotency_key="new-1")
        self.assertEqual(len(self.service.list_transactions(self.token)), 1)

    def test_approval_cannot_authorize_another_quote(self):
        mandate, first, _ = self.approved_quote()
        second = self.quote(mandate)
        with self.assertRaises(OperationDenied) as caught:
            self.service.checkout(self.token, second.quote.id, idempotency_key="different-quote")
        self.assertEqual(caught.exception.code, "approval_required")
        self.service.checkout(self.token, first.quote.id, idempotency_key="original-quote")

    def test_approval_rejects_non_boolean_action(self):
        view = self.quote(self.create())
        for value in ("false", 1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.service.decide_quote(self.token, view.quote.id, approve=value)

    def test_approved_quote_cannot_be_approved_again_or_flipped(self):
        _, view, _ = self.approved_quote()
        for action in (True, False):
            with self.subTest(action=action), self.assertRaises(Conflict):
                self.service.decide_quote(self.token, view.quote.id, approve=action)

    def test_price_change_invalidates_old_approval_even_if_lower(self):
        mandate, view, _ = self.approved_quote()
        self.service.install_catalogue(products=(product(version=2, price_minor=69899),))
        with self.assertRaises(OperationDenied):
            self.service.checkout(self.token, view.quote.id, idempotency_key="old-price")
        self.assertEqual(self.service.get_quote(self.token, view.quote.id).quote.status, "STALE")
        self.assertEqual(self.service.get_mandate(self.token, mandate.id).status, "ACTIVE")
        fresh = self.quote(mandate)
        with self.assertRaises(OperationDenied):
            self.service.checkout(self.token, fresh.quote.id, idempotency_key="new-price")

    def test_stock_trust_and_payment_changes_are_rechecked(self):
        for entity in ("stock", "trust", "fee", "payment_disabled"):
            with self.subTest(entity=entity):
                # Use separate fresh catalogue IDs to isolate each change.
                suffix = entity.replace("_", "-")
                seller = merchant(id="merchant-" + suffix)
                item = product(id="product-" + suffix, merchant_id=seller.id)
                method = payment(id="card-" + suffix)
                self.service.install_catalogue(merchants=(seller,), products=(item,), payments=(method,))
                mandate = self.create(auto=True)
                view = self.service.propose_quote(self.token, mandate.id, item.id, method.id)
                if entity == "stock":
                    self.service.install_catalogue(products=(updated(item, version=2, availability=False),))
                elif entity == "trust":
                    self.service.install_catalogue(merchants=(updated(seller, version=2, trusted=False),))
                elif entity == "fee":
                    self.service.install_catalogue(payments=(updated(method, version=2, fee_minor=1),))
                else:
                    self.service.install_catalogue(payments=(updated(method, version=2, enabled=False),))
                with self.assertRaises(OperationDenied):
                    self.service.checkout(self.token, view.quote.id, idempotency_key="changed-" + suffix)

    def test_quote_expiry_is_checked_after_human_approval(self):
        mandate = self.create()
        view = self.quote(mandate, ttl_seconds=5)
        self.service.decide_quote(self.token, view.quote.id, approve=True)
        self.now += timedelta(seconds=5)
        with self.assertRaises(OperationDenied):
            self.service.checkout(self.token, view.quote.id, idempotency_key="expired-quote")
        self.assertEqual(self.service.get_quote(self.token, view.quote.id).quote.status, "EXPIRED")
        self.assertEqual(self.service.get_mandate(self.token, mandate.id).status, "ACTIVE")

    def test_mandate_expiry_is_checked_after_human_approval(self):
        mandate = self.create(expires_at=NOW + timedelta(seconds=5))
        view = self.quote(mandate)
        self.service.decide_quote(self.token, view.quote.id, approve=True)
        self.now += timedelta(seconds=5)
        with self.assertRaises(OperationDenied):
            self.service.checkout(self.token, view.quote.id, idempotency_key="expired-mandate")
        with self.assertRaises(OperationDenied):
            self.quote(mandate)

    def test_expired_approval_attempt_is_rejected_and_audited(self):
        view = self.quote(self.create(), ttl_seconds=5)
        self.now += timedelta(seconds=5)
        with self.assertRaises(OperationDenied):
            self.service.decide_quote(self.token, view.quote.id, approve=True)
        self.assertEqual(self.service.get_quote(self.token, view.quote.id).quote.status, "EXPIRED")
        self.assertEqual(self.service.list_policy_decisions(self.token, view.quote.id)[-1].status, "BLOCK")

    def test_sessions_do_not_grant_access_to_other_owners(self):
        mandate, view, _ = self.approved_quote()
        other = self.service.create_session()
        operations = (
            lambda: self.service.get_mandate(other, mandate.id),
            lambda: self.service.get_quote(other, view.quote.id),
            lambda: self.service.propose_quote(other, mandate.id, "P01", "card-1"),
            lambda: self.service.decide_quote(other, view.quote.id, approve=True),
            lambda: self.service.checkout(other, view.quote.id, idempotency_key="other"),
            lambda: self.service.revoke_mandate(other, mandate.id),
            lambda: self.service.reconfirm_mandate(other, mandate.id, terms()),
            lambda: self.service.list_policy_decisions(other, view.quote.id),
        )
        for operation in operations:
            with self.assertRaises(ResourceNotFound):
                operation()
        self.assertEqual(self.service.list_audit(other)[-1].event_type, "session_created")
        self.assertEqual(self.service.list_transactions(other), ())

    def test_invalid_session_cannot_access_service(self):
        for token in (None, "", "not-a-real-session-token", 123):
            with self.subTest(token=token), self.assertRaises(AuthenticationRequired):
                self.service.list_transactions(token)

    def test_session_token_is_not_stored_in_plain_text(self):
        with self.database.transaction() as connection:
            for table in ("sessions", "audit_events"):
                rows = [dict(row) for row in connection.execute("SELECT * FROM " + table)]
                self.assertNotIn(self.token, str(rows))

    def test_expired_or_server_owned_confirmation_fields_are_rejected(self):
        with self.assertRaises(OperationDenied):
            self.service.confirm_mandate(self.token, terms(expires_at=NOW))
        # An unvalidated Pydantic copy must still be rejected at the service boundary.
        malformed = terms().model_copy(update={"auto_approve_enabled": "false"})
        with self.assertRaises(ValidationError):
            self.service.confirm_mandate(self.token, malformed)

    def test_same_key_same_request_returns_exact_completed_transaction(self):
        mandate, view, _ = self.approved_quote()
        first = self.service.checkout(self.token, view.quote.id, idempotency_key="repeat")
        audit_count = len(self.service.list_audit(self.token))
        self.now += timedelta(hours=2)
        second = self.service.checkout(self.token, view.quote.id, idempotency_key="repeat")
        self.assertEqual(first, second)
        self.assertEqual(len(self.service.list_transactions(self.token)), 1)
        self.assertEqual(len(self.service.list_audit(self.token)), audit_count)

    def test_same_key_different_quote_is_conflict(self):
        mandate, first, _ = self.approved_quote()
        second = self.quote(mandate)
        self.service.checkout(self.token, first.quote.id, idempotency_key="same-key")
        with self.assertRaises(Conflict) as caught:
            self.service.checkout(self.token, second.quote.id, idempotency_key="same-key")
        self.assertEqual(caught.exception.code, "idempotency_conflict")

    def test_distinct_key_cannot_purchase_again_from_consumed_mandate(self):
        mandate, view, _ = self.approved_quote()
        self.service.checkout(self.token, view.quote.id, idempotency_key="first")
        with self.assertRaises(Conflict):
            self.service.checkout(self.token, view.quote.id, idempotency_key="second")
        with self.assertRaises(Conflict):
            self.quote(mandate)
        with self.assertRaises(Conflict):
            self.service.reconfirm_mandate(self.token, mandate.id, terms())

    def test_idempotency_keys_are_scoped_to_session(self):
        _, first, _ = self.approved_quote()
        self.service.checkout(self.token, first.quote.id, idempotency_key="shared-key")
        other = self.service.create_session()
        mandate = self.service.confirm_mandate(other, terms(auto_approve_enabled=True, approval_threshold_minor=75000))
        second = self.service.propose_quote(other, mandate.id, "P01", "card-1")
        transaction = self.service.checkout(other, second.quote.id, idempotency_key="shared-key")
        self.assertEqual(transaction.session_id, mandate.session_id)
        self.assertEqual(len(self.service.list_transactions(other)), 1)

    def test_invalid_idempotency_keys_are_rejected(self):
        view = self.quote(self.create(auto=True))
        for key in (None, True, "", "x" * 129, "contains spaces", "a/b"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.service.checkout(self.token, view.quote.id, idempotency_key=key)

    def test_reconfirmation_invalidates_quotes_and_approvals(self):
        mandate, view, _ = self.approved_quote()
        changed = self.service.reconfirm_mandate(self.token, mandate.id, terms(max_spend_minor=90000))
        self.assertEqual(changed.version, mandate.version + 1)
        self.assertEqual(self.service.get_quote(self.token, view.quote.id).quote.status, "STALE")
        with self.assertRaises(Conflict):
            self.service.checkout(self.token, view.quote.id, idempotency_key="old-approval")
        fresh = self.quote(changed)
        with self.assertRaises(OperationDenied):
            self.service.checkout(self.token, fresh.quote.id, idempotency_key="fresh")
        self.service.decide_quote(self.token, fresh.quote.id, approve=True)
        self.service.checkout(self.token, fresh.quote.id, idempotency_key="fresh")

    def test_revocation_prevents_checkout(self):
        mandate, view, _ = self.approved_quote()
        self.service.revoke_mandate(self.token, mandate.id)
        with self.assertRaises(Conflict):
            self.service.checkout(self.token, view.quote.id, idempotency_key="revoked")

    def test_catalogue_updates_require_version_increment(self):
        self.service.install_catalogue(products=(product(),))  # Unchanged seed is idempotent.
        with self.assertRaises(Conflict):
            self.service.install_catalogue(products=(product(price_minor=1),))
        with self.assertRaises(ValueError):
            self.service.install_catalogue(products=(product(version=2, price_minor=1), product(version=2)))
        # Failed batches rolled back the first update.
        view = self.quote(self.create())
        self.assertEqual(view.quote.product_version, 1)
        self.assertEqual(view.quote.price_minor, 69900)

    def test_mid_checkout_failure_rolls_back_all_state(self):
        mandate, view, _ = self.approved_quote()
        original_audit = self.service._audit

        def fail_after_state_changes(connection, session_id, event_type, *args, **kwargs):
            if event_type == "checkout_completed":
                raise RuntimeError("injected storage failure")
            return original_audit(connection, session_id, event_type, *args, **kwargs)

        audit_before = len(self.service.list_audit(self.token))
        with patch.object(self.service, "_audit", side_effect=fail_after_state_changes):
            with self.assertRaises(RuntimeError):
                self.service.checkout(self.token, view.quote.id, idempotency_key="rollback")
        self.assertEqual(self.service.get_mandate(self.token, mandate.id).status, "ACTIVE")
        self.assertEqual(self.service.get_quote(self.token, view.quote.id).quote.status, "APPROVED")
        self.assertEqual(self.service.list_transactions(self.token), ())
        self.assertEqual(len(self.service.list_audit(self.token)), audit_before)
        self.service.checkout(self.token, view.quote.id, idempotency_key="rollback")

    def test_original_ask_record_is_not_rewritten_by_approval(self):
        _, view, _ = self.approved_quote()
        self.service.checkout(self.token, view.quote.id, idempotency_key="history")
        decisions = self.service.list_policy_decisions(self.token, view.quote.id)
        self.assertEqual(decisions[0], view.policy)
        self.assertTrue(all(decision.status == "ASK" for decision in decisions))
        kinds = [event.event_type for event in self.service.list_audit(self.token)]
        self.assertIn("quote_approved", kinds)
        self.assertIn("mandate_consumed", kinds)
        self.assertIn("checkout_completed", kinds)

    def test_completed_state_survives_a_new_service_instance(self):
        mandate, view, _ = self.approved_quote()
        original = self.service.checkout(self.token, view.quote.id, idempotency_key="durable")
        restarted = CheckoutService(Database(self.path), clock=lambda: self.now)
        self.assertEqual(restarted.list_transactions(self.token), (original,))
        self.assertEqual(restarted.get_mandate(self.token, mandate.id).status, "CONSUMED")
        self.assertEqual(restarted.checkout(self.token, view.quote.id, idempotency_key="durable"), original)

    def test_two_connections_racing_distinct_quotes_only_purchase_once(self):
        mandate = self.create(auto=True)
        quotes = (self.quote(mandate), self.quote(mandate))
        barrier = threading.Barrier(2)
        services = [CheckoutService(Database(self.path), clock=lambda: NOW) for _ in range(2)]

        def purchase(index):
            barrier.wait(timeout=10)
            try:
                services[index].checkout(self.token, quotes[index].quote.id, idempotency_key="race-" + str(index))
                return "completed"
            except Conflict:
                return "denied"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(purchase, range(2)))
        self.assertCountEqual(outcomes, ["completed", "denied"])
        self.assertEqual(len(self.service.list_transactions(self.token)), 1)

    def test_racing_same_idempotency_key_returns_one_transaction(self):
        view = self.quote(self.create(auto=True))
        barrier = threading.Barrier(2)

        def purchase(_):
            barrier.wait(timeout=10)
            return self.service.checkout(self.token, view.quote.id, idempotency_key="same-race")

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(purchase, range(2)))
        self.assertEqual(outcomes[0], outcomes[1])
        self.assertEqual(len(self.service.list_transactions(self.token)), 1)

    def test_two_processes_racing_only_purchase_once(self):
        mandate = self.create(auto=True)
        views = (self.quote(mandate), self.quote(mandate))
        context = multiprocessing.get_context("spawn")
        queue, barrier = context.Queue(), context.Barrier(2)
        processes = [context.Process(target=process_checkout,
                                    args=(str(self.path), self.token, views[index].quote.id,
                                          "process-" + str(index), barrier, queue)) for index in range(2)]
        try:
            for process in processes:
                process.start()
            outcomes = [queue.get(timeout=30), queue.get(timeout=30)]
            for process in processes:
                process.join(timeout=10)
                self.assertEqual(process.exitcode, 0)
            self.assertCountEqual([item[0] for item in outcomes], ["completed", "denied"])
            self.assertEqual(len(self.service.list_transactions(self.token)), 1)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            queue.close()
            queue.join_thread()


if __name__ == "__main__":
    unittest.main()
