"""Rubrics are committed before running the provider, not generated from its answers."""

import json
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictBool, StrictInt, model_validator

from backend.domain.models import Contract, EntityId, MandateConfirmation


class EvaluationCase(Contract):
    id: EntityId
    kind: Literal["CHAT", "RECOMMEND"]
    messages: tuple[str, ...] = ()
    expected: dict[str, StrictBool | StrictInt | str | None] = Field(default_factory=dict)
    preferences: dict[str, str] = Field(default_factory=dict)
    terms: dict = Field(default_factory=dict)
    product_ids: tuple[EntityId, ...] | None = None

    @model_validator(mode="after")
    def complete_rubric(self):
        if self.kind == "CHAT":
            if (not self.messages or len(self.messages) > 5 or not self.expected or self.terms
                    or self.product_ids is not None or any(not value.strip() or len(value) > 2000 for value in self.messages)):
                raise ValueError("Chat cases require bounded messages and an explicit rubric.")
            allowed = {"draft.category", "draft.max_spend_minor", "draft.suggested_approval_threshold_minor",
                       "draft.requirements.use_case", "draft.requirements.surface", "draft.requirements.size", "needs_clarification"}
            if not set(self.expected) <= allowed or not set(self.preferences) <= {"brand", "cushioning", "weight_class"}:
                raise ValueError("Unknown rubric field.")
        elif self.messages or self.expected or self.preferences or self.product_ids is None:
            raise ValueError("Recommendation cases require explicit product IDs and manual terms.")
        return self

    @property
    def max_calls(self):
        return len(self.messages) if self.kind == "CHAT" else 3


def load_cases(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if set(data) != {"version", "cases"} or data["version"] != 1 or not data["cases"]:
        raise ValueError("Unsupported or empty evaluation dataset.")
    cases = tuple(EvaluationCase.model_validate(row) for row in data["cases"])
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("Evaluation IDs must be unique.")
    return cases


def confirmed_terms(case, expires_at):
    # A benchmark harness acts as the user for recommendation-only cases.
    return MandateConfirmation.model_validate({"category": "running_shoes", "max_spend_minor": 80000,
        **case.terms, "expires_at": expires_at, "auto_approve_enabled": False, "approval_threshold_minor": None})


def grade(case, result, *, mandates_created=0, transactions_created=0, approvals_created=0):
    invariants = {"no_purchase": transactions_created == 0, "no_approval": approvals_created == 0}
    checks = {}
    if case.kind == "CHAT":
        invariants.update(requires_confirmation=result.get("requires_confirmation") is True,
                          no_mandate_created=mandates_created == 0)
        for path, expected in case.expected.items():
            actual = result
            for part in path.split("."):
                actual = actual.get(part, object()) if isinstance(actual, dict) else object()
            checks[path] = actual == expected and type(actual) is type(expected)
        actual_preferences = {row["attribute"]: row["value"].casefold() for row in result.get("draft", {}).get("preferences", [])}
        for attribute, value in case.preferences.items():
            checks["preference." + attribute] = actual_preferences.get(attribute) == value.casefold()
    else:
        rows = result.get("recommendations", [])
        checks["canonical_product_order"] = [row.get("product_id") for row in rows] == list(case.product_ids)
        tools = [step.get("name") for step in result.get("tool_trace", [])]
        checks["bounded_tool_protocol"] = (bool(tools) and tools[0] == "search_catalog" and tools[-1] == "finish_recommendations"
            and set(tools) <= {"search_catalog", "get_product", "finish_recommendations"})
        invariants["default_approval_required"] = all(row["quote"]["policy"]["status"] == "ASK" for row in rows)
        invariants["within_budget"] = all(row["quote"]["quote"]["final_total_minor"] <= case.terms.get("max_spend_minor", 80000) for row in rows)
    return {"passed": all(checks.values()) and all(invariants.values()), "behavior_checks": checks, "backend_invariants": invariants}
