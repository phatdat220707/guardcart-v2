from datetime import timedelta
import unittest

from pydantic import ValidationError

from backend.domain.models import MandateConfirmation, MandateDraft, Quote
from backend.domain.policy import create_quote
from tests.fixtures import NOW, mandate, merchant, payment, product


class SchemaTests(unittest.TestCase):
    def test_auto_approval_is_disabled_by_default(self):
        confirmed = mandate()
        self.assertIs(confirmed.auto_approve_enabled, False)
        self.assertIsNone(confirmed.approval_threshold_minor)

    def test_invalid_money_values_are_rejected(self):
        for value in ("80000", 80000.0, True, -1, 0, float("nan"), float("inf")):
            with self.subTest(value=repr(value)), self.assertRaises(ValidationError):
                mandate(max_spend_minor=value)

    def test_product_money_and_points_are_strict(self):
        for value in ("100", 1.5, True, -1, float("nan")):
            with self.subTest(value=repr(value)), self.assertRaises(ValidationError):
                product(price_minor=value)
            with self.subTest(points=repr(value)), self.assertRaises(ValidationError):
                payment(points_used=value)

    def test_booleans_reject_strings_and_integers(self):
        for value in ("false", "true", 0, 1, None):
            for factory, field in ((merchant, "trusted"), (product, "availability"), (mandate, "auto_approve_enabled")):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    factory(**{field: value})

    def test_auto_approval_requires_a_valid_threshold(self):
        for value in (None, -1, 80001, "75000", True):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                mandate(auto_approve_enabled=True, approval_threshold_minor=value)

    def test_disabled_auto_approval_cannot_hide_a_threshold(self):
        with self.assertRaises(ValidationError):
            mandate(approval_threshold_minor=75000)

    def test_zero_auto_threshold_is_valid(self):
        self.assertEqual(mandate(auto_approve_enabled=True, approval_threshold_minor=0).approval_threshold_minor, 0)

    def test_extra_authority_fields_are_rejected(self):
        for field in ("decision", "approved", "selected_payment", "user_id"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                mandate(**{field: True})

    def test_confirmation_input_cannot_set_server_identity(self):
        with self.assertRaises(ValidationError):
            MandateConfirmation.model_validate({
                "category": "running_shoes", "max_spend_minor": 80000,
                "expires_at": NOW + timedelta(days=1), "session_id": "someone-else",
            })

    def test_draft_has_no_spending_authority(self):
        draft = MandateDraft(category="running_shoes", suggested_approval_threshold_minor=75000)
        self.assertEqual(draft.missing_fields, ("max_spend_minor",))
        with self.assertRaises(ValidationError):
            MandateDraft(category="running_shoes", auto_approve_enabled=True)

    def test_timestamps_require_timezone_and_reject_epoch_numbers(self):
        for value in (NOW.replace(tzinfo=None), "2026-11-18T09:00:00", 1794900000, True):
            with self.subTest(value=str(value)), self.assertRaises(ValidationError):
                mandate(expires_at=value)

    def test_timestamps_are_normalized_to_utc(self):
        confirmed = mandate(expires_at="2026-11-18T17:00:00+08:00")
        self.assertEqual(confirmed.expires_at, NOW + timedelta(days=1))

    def test_expiry_must_follow_creation(self):
        with self.assertRaises(ValidationError):
            mandate(expires_at=NOW - timedelta(minutes=1))

    def test_unsupported_currency_is_rejected(self):
        with self.assertRaises(ValidationError):
            mandate(currency="USD")

    def test_duplicate_preference_attributes_are_rejected(self):
        with self.assertRaises(ValidationError):
            mandate(preferences=[{"attribute": "brand", "value": "A"}, {"attribute": "brand", "value": "B"}])

    def test_contracts_are_frozen(self):
        confirmed = mandate()
        with self.assertRaises(ValidationError):
            confirmed.auto_approve_enabled = True

    def test_quote_math_cannot_be_overridden(self):
        quote = create_quote(mandate(), product(), merchant(), payment(), now=NOW)
        for changes in ({"final_total_minor": 1}, {"effective_cost_minor": 1}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                Quote.model_validate({**quote.model_dump(), **changes})

    def test_json_round_trip_retains_exact_amounts(self):
        original = mandate()
        restored = type(original).model_validate_json(original.model_dump_json())
        self.assertEqual(restored, original)


if __name__ == "__main__":
    unittest.main()
