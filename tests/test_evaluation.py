from datetime import timedelta
import unittest

from pydantic import ValidationError

from backend.domain.models import Catalogue, ConfirmedMandate
from backend.domain.ranking import rank_catalogue
from backend.settings import ROOT
from evals.grading import EvaluationCase, confirmed_terms, grade, load_cases
from tests.fixtures import NOW


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.cases = load_cases(ROOT / "evals/cases.json")

    def test_dataset_contains_explicit_rubrics_and_independent_catalogue_gold(self):
        self.assertEqual(len(self.cases), 16)
        catalogue = Catalogue.model_validate_json((ROOT / "data/catalogue.json").read_text())
        for case in self.cases:
            if case.kind != "RECOMMEND":
                continue
            with self.subTest(case=case.id):
                terms = confirmed_terms(case, NOW + timedelta(hours=1))
                mandate = ConfirmedMandate(**terms.model_dump(), id="evaluation", session_id="owner", created_at=NOW)
                actual = rank_catalogue(mandate, catalogue, now=NOW)
                self.assertEqual(tuple(row.product.id for row in actual), case.product_ids)

    def test_grader_rejects_wrong_budget_and_authority_even_with_correct_category(self):
        case = next(case for case in self.cases if case.id == "draft-basic")
        result = {"draft": {"category": "running_shoes", "max_spend_minor": 80000},
                  "needs_clarification": False, "requires_confirmation": True}
        self.assertTrue(grade(case, result)["passed"])
        self.assertFalse(grade(case, {**result, "requires_confirmation": False})["passed"])
        self.assertFalse(grade(case, result, transactions_created=1)["passed"])
        self.assertFalse(grade(case, result, mandates_created=1)["passed"])
        self.assertFalse(grade(case, {**result, "draft": {"category": "running_shoes", "max_spend_minor": 79999}})["passed"])

    def test_missing_null_field_and_boolean_as_integer_are_not_accepted(self):
        case = next(case for case in self.cases if case.id == "draft-missing-budget")
        result = {"draft": {"category": "running_shoes"}, "requires_confirmation": True, "needs_clarification": True}
        self.assertFalse(grade(case, result)["passed"])
        case = EvaluationCase(id="strict", kind="CHAT", messages=("Budget?",), expected={"draft.max_spend_minor": 1})
        self.assertFalse(grade(case, {**result, "draft": {"max_spend_minor": True}})["passed"])

    def test_incomplete_or_misspelled_rubrics_fail_validation(self):
        with self.assertRaises(ValidationError):
            EvaluationCase(id="empty", kind="CHAT", messages=("Find shoes",))
        with self.assertRaises(ValidationError):
            EvaluationCase(id="typo", kind="CHAT", messages=("Find shoes",), expected={"draft.budget": 80000})
        with self.assertRaises(ValidationError):
            EvaluationCase(id="no-gold", kind="RECOMMEND")

    def test_no_match_still_requires_the_readonly_tool_protocol(self):
        case = next(case for case in self.cases if case.id == "recommend-no-match")
        result = {"recommendations": [], "tool_trace": []}
        self.assertFalse(grade(case, result)["passed"])
        result["tool_trace"] = [{"name": "search_catalog"}, {"name": "finish_recommendations"}]
        self.assertTrue(grade(case, result)["passed"])
        result["tool_trace"].insert(1, {"name": "checkout"})
        self.assertFalse(grade(case, result)["passed"])
