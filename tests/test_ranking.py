import unittest

from backend.domain.models import Catalogue
from backend.domain.ranking import explain, rank_catalogue
from tests.fixtures import NOW, mandate, merchant, payment, product


class RankingTests(unittest.TestCase):
    def catalogue(self, *items, payments=None):
        return Catalogue(merchants=(merchant(),), products=items, payments=payments or (payment(),))

    def test_purpose_fit_excludes_cheaper_walking_only_product(self):
        terms = mandate(requirements={"use_case": "long_distance"})
        walking = product(id="walk", price_minor=10000, attributes={"use_cases": ["walking"]})
        running = product(id="run", attributes={"use_cases": ["long_distance"]})
        self.assertEqual([row.product.id for row in rank_catalogue(terms, self.catalogue(walking, running), now=NOW)], ["run"])

    def test_preferences_take_priority_over_cost(self):
        terms = mandate(preferences=[{"attribute": "cushioning", "value": "high"}])
        cheap = product(id="cheap", price_minor=10000, attributes={"cushioning": "low"})
        suitable = product(id="suitable", price_minor=60000, attributes={"cushioning": "high"})
        rows = rank_catalogue(terms, self.catalogue(cheap, suitable), now=NOW)
        self.assertEqual([row.product.id for row in rows], ["suitable", "cheap"])
        self.assertEqual(len(rows[0].matched_preferences), 1)
        self.assertIn("cushioning=high", explain(rows[0]))

    def test_equal_preference_matches_use_cost_then_product_id(self):
        a = product(id="a", price_minor=60000)
        b = product(id="b", price_minor=60000)
        c = product(id="c", price_minor=70000)
        self.assertEqual([row.product.id for row in rank_catalogue(mandate(), self.catalogue(c, b, a), now=NOW)], ["a", "b", "c"])

    def test_missing_attributes_are_unknown_not_matches(self):
        terms = mandate(preferences=[{"attribute": "brand", "value": "RoadLab"}])
        row = rank_catalogue(terms, self.catalogue(product()), now=NOW)[0]
        self.assertEqual(len(row.unknown_preferences), 1)
        self.assertEqual(row.matched_preferences, ())

    def test_preference_values_are_case_insensitive(self):
        terms = mandate(preferences=[{"attribute": "brand", "value": "roadlab"}])
        row = rank_catalogue(terms, self.catalogue(product(attributes={"brand": "RoadLab"})), now=NOW)[0]
        self.assertEqual(len(row.matched_preferences), 1)

    def test_budget_and_stock_failures_are_not_recommendations(self):
        items = (product(id="over", price_minor=80000, shipping_minor=2000), product(id="sold", availability=False))
        self.assertEqual(rank_catalogue(mandate(), self.catalogue(*items), now=NOW), ())

    def test_approval_threshold_does_not_exclude_in_budget_match(self):
        terms = mandate(auto_approve_enabled=True, approval_threshold_minor=75000)
        row = rank_catalogue(terms, self.catalogue(product(price_minor=76000, shipping_minor=1000)), now=NOW)[0]
        self.assertEqual(row.ranked_payment.policy.status, "ASK")

    def test_duplicate_or_unknown_catalogue_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            rank_catalogue(mandate(), self.catalogue(product(), product()), now=NOW)
        with self.assertRaises(ValueError):
            rank_catalogue(mandate(), self.catalogue(product(merchant_id="unknown")), now=NOW)

    def test_zero_total_is_not_a_supported_checkout_candidate(self):
        rows = rank_catalogue(mandate(), self.catalogue(product(price_minor=0, shipping_minor=0)), now=NOW)
        self.assertEqual(rows, ())
