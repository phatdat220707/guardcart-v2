"""Validate the committed rubric offline, or opt in to a capped real-provider evaluation."""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
from time import monotonic
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.ai.config import AiSettings
from backend.ai.errors import AiQuotaExceeded
from backend.ai.models import ChatRequest, RecommendRequest
from backend.ai.provider import ChatCompletionsProvider
from backend.ai.service import AgentService
from backend.domain.models import Catalogue
from backend.services.checkout import CheckoutService
from backend.services.errors import ServiceError
from backend.storage import Database
from evals.grading import confirmed_terms, grade, load_cases


class CappedProvider:
    def __init__(self, provider, cap):
        self.provider, self.cap, self.calls = provider, cap, 0
        self.model_id = provider.model_id

    def complete(self, messages, tools, tool_choice):
        if self.calls >= self.cap:
            raise AiQuotaExceeded()
        self.calls += 1  # Failed/timeout attempts also consume the local allowance.
        return self.provider.complete(messages, tools, tool_choice)


def source_hash(relative):
    return sha256((ROOT / relative).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--live", action="store_true", help="Make metered provider calls; never approves or checks out.")
    parser.add_argument("--case", action="append", default=[], dest="ids")
    parser.add_argument("--max-provider-calls", type=int, default=8)
    args = parser.parse_args()
    if args.live and args.validate_only:
        parser.error("Choose validation or live evaluation.")
    if not 1 <= args.max_provider_calls <= 100:
        parser.error("The call allowance must be 1 to 100.")
    dataset = ROOT / "evals/cases.json"
    cases = load_cases(dataset)
    unknown = set(args.ids) - {case.id for case in cases}
    if unknown:
        parser.error("Unknown evaluation case ID.")
    selected = [case for case in cases if not args.ids or case.id in args.ids]
    catalogue = Catalogue.model_validate_json((ROOT / "data/catalogue.json").read_text(encoding="utf-8"))
    for case in cases:
        if case.kind == "RECOMMEND":
            confirmed_terms(case, datetime.now(timezone.utc) + timedelta(hours=1))
            if not set(case.product_ids) <= {product.id for product in catalogue.products}:
                raise ValueError("Rubric refers to a missing catalogue product.")
    if not args.live:
        print(json.dumps({"mode": "validation_only", "dataset_cases": len(cases), "selected_cases": len(selected),
                          "provider_calls": 0, "model_quality_evaluated": False}))
        return 0
    try:
        settings = AiSettings.from_environment(ROOT / ".env")
        if not settings.enabled:
            raise ValueError("AI disabled")
    except Exception:
        print("Live evaluation requires valid backend AI configuration. Credentials are not displayed.")
        return 2
    provider = CappedProvider(ChatCompletionsProvider(settings), args.max_provider_calls)
    report = {"mode": "live", "timestamp": datetime.now(timezone.utc).isoformat(), "requested_model": settings.model,
              "usage_verified": False, "billing_verified": False, "model_identity_verified": False,
              "call_cap": args.max_provider_calls, "dataset_cases": len(cases), "selected_cases": len(selected),
              "hashes": {path: source_hash(path) for path in ("evals/cases.json", "evals/grading.py", "data/catalogue.json",
                         "backend/ai/service.py", "backend/ai/provider.py", "backend/domain/ranking.py")}, "results": []}
    with tempfile.TemporaryDirectory(prefix="guardcart-evaluation-") as directory:
        database = Database(Path(directory) / "evaluation.sqlite3")
        checkout = CheckoutService(database)
        checkout.install_catalogue(merchants=catalogue.merchants, products=catalogue.products, payments=catalogue.payments)
        agent = AgentService(checkout, provider=provider, settings=settings)
        for case in selected:
            if provider.cap - provider.calls < case.max_calls:
                report["results"].append({"id": case.id, "status": "SKIPPED", "reason": "insufficient_remaining_call_allowance"})
                continue
            print(f"Evaluating {case.id}...", flush=True)
            token, started, calls_before = checkout.create_session(), monotonic(), provider.calls
            session_id = checkout.get_session(token).id
            try:
                if case.kind == "CHAT":
                    conversation_id = None
                    for message in case.messages:
                        result = agent.chat(token, ChatRequest(request_id=str(uuid4()), message=message, conversation_id=conversation_id))
                        conversation_id = result.conversation_id
                else:
                    mandate = checkout.confirm_mandate(token, confirmed_terms(case, datetime.now(timezone.utc) + timedelta(hours=1)))
                    result = agent.recommend(token, RecommendRequest(request_id=str(uuid4()), mandate_id=mandate.id))
                with database.transaction() as connection:
                    counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id=?", (session_id,)).fetchone()[0]
                              for table in ("mandates", "transactions", "approvals")}
                assessment = grade(case, result.model_dump(mode="json"), mandates_created=counts["mandates"],
                                   transactions_created=counts["transactions"], approvals_created=counts["approvals"])
                row = {"id": case.id, "status": "PASS" if assessment["passed"] else "FAIL", **assessment}
            except Exception as exc:
                # Only fixed service codes are retained. Never include provider bodies or arbitrary exception text.
                row = {"id": case.id, "status": "ERROR", "error_code": exc.code if isinstance(exc, ServiceError) else "evaluation_error"}
            row.update(provider_calls=provider.calls - calls_before, elapsed_ms=round((monotonic() - started) * 1000))
            report["results"].append(row)
            print(f"  {row['status']} ({row['provider_calls']} calls)", flush=True)
    report["provider_calls"] = provider.calls
    report["counts"] = {status: sum(row["status"] == status for row in report["results"]) for status in ("PASS", "FAIL", "ERROR", "SKIPPED")}
    destination = ROOT / "reports" / ("evaluation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8] + ".json")
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(destination), "provider_calls": provider.calls, **report["counts"], "billing_verified": False}))
    return int(bool(report["counts"]["FAIL"] or report["counts"]["ERROR"]))


if __name__ == "__main__":
    raise SystemExit(main())
