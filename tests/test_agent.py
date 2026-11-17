from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from backend.ai.config import AiSettings
from backend.ai.errors import AiBusy, AiContextChanged, AiProtocolError, AiQuotaExceeded, AiUnavailable
from backend.ai.models import ChatRequest, RecommendRequest
from backend.ai.service import AgentService
from backend.domain.models import Catalogue, MandateConfirmation
from backend.services.checkout import CheckoutService
from backend.services.errors import AuthenticationRequired, Conflict, OperationDenied, ResourceNotFound
from backend.settings import ROOT
from backend.storage import Database
from tests.ai_fakes import ScriptedProvider, draft_reply, finish_from_search, tool_reply
from tests.fixtures import NOW, updated


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="guardcart-agent-")
        self.addCleanup(self.directory.cleanup)
        self.database = Database(Path(self.directory.name) / "agent.sqlite3")
        self.now = NOW
        self.checkout = CheckoutService(self.database, clock=lambda: self.now)
        catalogue = Catalogue.model_validate_json((ROOT / "data/catalogue.json").read_text())
        self.checkout.install_catalogue(merchants=catalogue.merchants, products=catalogue.products, payments=catalogue.payments)
        self.token = self.checkout.create_session()

    def agent(self, *responses, **limits):
        provider = ScriptedProvider(*responses)
        return AgentService(self.checkout, provider=provider, settings=AiSettings(**limits)), provider

    def confirmed(self, token=None, **changes):
        terms = MandateConfirmation.model_validate({
            "category": "running_shoes", "max_spend_minor": 80000,
            "expires_at": NOW + timedelta(hours=1), **changes,
        })
        return self.checkout.confirm_mandate(token or self.token, terms)

    def count(self, table):
        with self.database.transaction() as connection:
            return connection.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]

    def chat(self, agent, key="chat-1", message="Find running shoes under HK$800.", conversation_id=None):
        return agent.chat(self.token, ChatRequest(request_id=key, message=message, conversation_id=conversation_id))

    def test_valid_chat_returns_unconfirmed_draft_without_authority(self):
        agent, provider = self.agent(draft_reply(suggested_approval_threshold_minor=75000))
        result = self.chat(agent)
        self.assertEqual(result.draft.max_spend_minor, 80000)
        self.assertTrue(result.requires_confirmation)
        self.assertFalse(result.needs_clarification)
        self.assertEqual(self.count("mandates"), 0)
        self.assertEqual(self.count("quotes"), 0)
        self.assertEqual(self.count("transactions"), 0)
        self.assertEqual(result.run.provider_calls, 1)
        self.assertEqual(len(provider.calls), 1)
        self.assertNotIn(self.token, json.dumps(provider.calls))

    def test_missing_budget_asks_for_clarification(self):
        agent, _ = self.agent(tool_reply("propose_mandate", {"draft": {"category": "running_shoes"}}))
        result = self.chat(agent, message="Find running shoes.")
        self.assertTrue(result.needs_clarification)
        self.assertIn("budget", result.message)
        self.assertIsNone(result.draft.max_spend_minor)

    def test_followup_includes_server_owned_history_and_draft(self):
        agent, provider = self.agent(tool_reply("propose_mandate", {"draft": {"category": "running_shoes"}}), draft_reply())
        first = self.chat(agent, message="Find running shoes.")
        second = self.chat(agent, key="chat-2", message="HK$800 including shipping.", conversation_id=first.conversation_id)
        self.assertEqual(first.conversation_id, second.conversation_id)
        self.assertEqual(len(agent.conversation(self.token, first.conversation_id).messages), 4)
        context = json.dumps(provider.calls[1]["messages"])
        self.assertIn("Find running shoes.", context)
        self.assertIn("Prior unconfirmed draft data", context)

    def test_ambiguous_size_has_an_english_clarification(self):
        reply = tool_reply("propose_mandate", {"draft": {"category": "running_shoes", "max_spend_minor": 80000},
                                              "clarification": "unsupported_size"})
        agent, _ = self.agent(reply)
        result = self.chat(agent, message="Size 9 please.")
        self.assertTrue(result.needs_clarification)
        self.assertIn("EU", result.message)

    def test_model_cannot_enable_auto_approval_in_draft(self):
        agent, _ = self.agent(draft_reply(auto_approve_enabled=True))
        with self.assertRaises(AiProtocolError):
            self.chat(agent)
        self.assertEqual(self.count("mandates"), 0)
        self.assertEqual(agent.run(self.token, request_id="chat-1").state, "FAILED")

    def test_unresolved_size_never_prefills_the_confirmable_eu_size(self):
        for index, (size, clarification) in enumerate((("US9", None), ("EU42", "unsupported_size"))):
            with self.subTest(size=size, clarification=clarification):
                agent, _ = self.agent(tool_reply("propose_mandate", {"draft": {"category": "running_shoes", "max_spend_minor": 80000,
                    "requirements": {"size": size, "surface": "road"}}, "clarification": clarification}))
                result = self.chat(agent, key=f"size-{index}", message="Running shoes under HK$800, US9.")
                self.assertIsNone(result.draft.requirements.size)
                self.assertEqual(result.draft.requirements.surface, "road")
                self.assertTrue(result.needs_clarification)
                self.assertEqual(self.count("mandates"), 0)

    def test_nonfinite_or_fractional_money_from_model_is_rejected(self):
        for value in (float("nan"), 80000.5, "80000"):
            with self.subTest(value=repr(value)):
                agent, _ = self.agent(draft_reply(max_spend_minor=value))
                with self.assertRaises(AiProtocolError):
                    self.chat(agent, key="bad-" + str(len(self.checkout.list_audit(self.token))))

    def test_chat_idempotency_does_not_charge_or_append_twice(self):
        agent, provider = self.agent(draft_reply())
        first, second = self.chat(agent), self.chat(agent)
        self.assertEqual(first, second)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(agent.conversation(self.token, first.conversation_id).messages), 2)
        with self.assertRaises(Conflict):
            self.chat(agent, message="Different input with the same request ID.")

    def test_failed_request_id_does_not_automatically_retry(self):
        agent, provider = self.agent(AiUnavailable())
        with self.assertRaises(AiUnavailable):
            self.chat(agent)
        with self.assertRaises(Conflict):
            self.chat(agent)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(agent.usage(self.token).calls_used, 1)

    def test_foreign_conversation_and_run_are_not_visible(self):
        agent, provider = self.agent(draft_reply())
        result = self.chat(agent)
        other = self.checkout.create_session()
        for operation in (
            lambda: agent.conversation(other, result.conversation_id),
            lambda: agent.run(other, run_id=result.run.run_id),
            lambda: agent.chat(other, ChatRequest(request_id="other", conversation_id=result.conversation_id, message="Hello")),
        ):
            with self.assertRaises(ResourceNotFound):
                operation()
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(agent.usage(other).calls_used, 0)

    def test_recommendations_use_tools_and_require_human_approval_by_default(self):
        mandate = self.confirmed(requirements={"use_case": "long_distance", "surface": "road", "size": "EU42"},
                                 preferences=[{"attribute": "cushioning", "value": "high"},
                                              {"attribute": "weight_class", "value": "light"}])
        agent, provider = self.agent(tool_reply("search_catalog", {}, "call-search"), finish_from_search)
        request = RecommendRequest(request_id="recommend-1", mandate_id=mandate.id)
        result = agent.recommend(self.token, request)
        self.assertEqual([item.product_id for item in result.recommendations], ["P02", "P10", "P01"])
        self.assertTrue(all(item.quote.policy.status == "ASK" for item in result.recommendations))
        self.assertEqual([step.name for step in result.tool_trace], ["search_catalog", "finish_recommendations"])
        self.assertEqual(result.run.provider_calls, 2)
        self.assertEqual(self.count("approvals"), 0)
        self.assertEqual(self.checkout.list_transactions(self.token), ())
        first = result.recommendations[0].quote.quote
        with self.assertRaises(OperationDenied):
            self.checkout.checkout(self.token, first.id, idempotency_key="purchase")
        self.checkout.decide_quote(self.token, first.id, approve=True)
        completed = self.checkout.checkout(self.token, first.id, idempotency_key="purchase")
        self.assertEqual(completed.authorization_type, "USER_APPROVAL")
        replay = agent.recommend(self.token, request)
        self.assertEqual(replay, result)
        self.assertEqual(len(provider.calls), 2)

    def test_no_match_is_explicit_and_does_not_expand_the_mandate(self):
        mandate = self.confirmed(requirements={"size": "EU99"})
        agent, _ = self.agent(tool_reply("search_catalog", {}, "call-search"), finish_from_search)
        result = agent.recommend(self.token, RecommendRequest(request_id="none", mandate_id=mandate.id))
        self.assertEqual(result.recommendations, ())
        self.assertIn("No catalogue products", result.message)
        self.assertEqual(self.count("quotes"), 0)

    def test_unknown_or_mutating_tool_is_never_executed(self):
        mandate = self.confirmed()
        for name in ("approve_quote", "checkout", "read_file", "confirm_mandate"):
            with self.subTest(name=name):
                agent, _ = self.agent(tool_reply(name, {"approved": True}))
                with self.assertRaises(AiProtocolError):
                    agent.recommend(self.token, RecommendRequest(request_id="unsafe-" + name, mandate_id=mandate.id))
        self.assertEqual(self.count("approvals"), 0)
        self.assertEqual(self.count("transactions"), 0)
        self.assertEqual(self.count("quotes"), 0)

    def test_fabricated_product_or_wrong_order_is_rejected(self):
        mandate = self.confirmed()
        agent, _ = self.agent(tool_reply("search_catalog", {}, "call-search"),
                              tool_reply("finish_recommendations", {"product_ids": ["P08"]}, "call-finish"))
        with self.assertRaises(AiProtocolError):
            agent.recommend(self.token, RecommendRequest(request_id="fabricated", mandate_id=mandate.id))
        self.assertEqual(self.count("quotes"), 0)

    def test_search_arguments_cannot_override_budget(self):
        mandate = self.confirmed()
        agent, _ = self.agent(tool_reply("search_catalog", {"max_spend_minor": 999999}, "call-search"))
        with self.assertRaises(AiProtocolError):
            agent.recommend(self.token, RecommendRequest(request_id="override", mandate_id=mandate.id))
        self.assertEqual(self.checkout.get_mandate(self.token, mandate.id).max_spend_minor, 80000)

    def test_finish_requires_an_observed_search_result_from_a_prior_round(self):
        mandate = self.confirmed()
        agent, _ = self.agent(tool_reply("finish_recommendations", {"product_ids": []}))
        with self.assertRaises(AiProtocolError):
            agent.recommend(self.token, RecommendRequest(request_id="no-search", mandate_id=mandate.id))

    def test_no_tool_response_is_not_presented_as_a_successful_agent_run(self):
        from backend.ai.provider import ProviderReply
        mandate = self.confirmed()
        agent, _ = self.agent(ProviderReply(message={"content": "I bought it!", "tool_calls": []}, usage={}))
        with self.assertRaises(AiProtocolError):
            agent.recommend(self.token, RecommendRequest(request_id="prose-only", mandate_id=mandate.id))

    def test_maximum_three_model_rounds(self):
        mandate = self.confirmed()
        agent, provider = self.agent(*(tool_reply("search_catalog", {}, "call-" + str(i)) for i in range(3)))
        with self.assertRaises(AiProtocolError):
            agent.recommend(self.token, RecommendRequest(request_id="loop", mandate_id=mandate.id))
        self.assertEqual(len(provider.calls), 3)

    def test_catalogue_or_mandate_change_during_ai_invalidates_result(self):
        for change in ("catalogue", "mandate"):
            with self.subTest(change=change):
                mandate = self.confirmed()

                def change_and_finish(messages, tools, choice):
                    if change == "catalogue":
                        item = next(item for item in self.checkout.get_catalogue().products if item.id == "P01")
                        self.checkout.install_catalogue(products=(updated(item, version=item.version + 1, price_minor=item.price_minor - 1),))
                    else:
                        self.checkout.revoke_mandate(self.token, mandate.id)
                    return finish_from_search(messages, tools, choice)

                agent, _ = self.agent(tool_reply("search_catalog", {}, "call-search"), change_and_finish)
                with self.assertRaises(AiContextChanged):
                    agent.recommend(self.token, RecommendRequest(request_id="changed-" + change, mandate_id=mandate.id))
        self.assertEqual(self.count("quotes"), 0)

    def test_quote_batch_and_run_result_are_committed_atomically(self):
        mandate = self.confirmed()
        agent, _ = self.agent(tool_reply("search_catalog", {}, "call-search"), finish_from_search)
        with patch.object(agent, "_complete", side_effect=RuntimeError("injected failure")):
            with self.assertRaises(AiProtocolError):
                agent.recommend(self.token, RecommendRequest(request_id="rollback", mandate_id=mandate.id))
        self.assertEqual(self.count("quotes"), 0)
        self.assertEqual(self.count("policy_decisions"), 0)
        self.assertEqual(agent.usage(self.token).calls_used, 2)

    def test_provider_failure_does_not_expose_error_content(self):
        agent, _ = self.agent(RuntimeError("secret-provider-body"))
        with self.assertRaises(AiProtocolError) as caught:
            self.chat(agent)
        self.assertNotIn("secret-provider-body", str(caught.exception))
        state = agent.run(self.token, request_id="chat-1")
        self.assertEqual(state.error_code, "ai_protocol_error")

    def test_global_quota_persists_across_agent_instances_and_failed_calls(self):
        agent, _ = self.agent(AiUnavailable(), global_call_limit=1)
        with self.assertRaises(AiUnavailable):
            self.chat(agent)
        restarted = AgentService(self.checkout, provider=ScriptedProvider(draft_reply()), settings=AiSettings(global_call_limit=1))
        with self.assertRaises(AiQuotaExceeded):
            self.chat(restarted, key="chat-2")
        self.assertEqual(restarted.usage(self.token).calls_used, 1)

    def test_quota_exhaustion_mid_run_does_not_create_quotes(self):
        mandate = self.confirmed()
        agent, provider = self.agent(tool_reply("search_catalog", {}, "call-search"), global_call_limit=1)
        with self.assertRaises(AiQuotaExceeded):
            agent.recommend(self.token, RecommendRequest(request_id="partial", mandate_id=mandate.id))
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(self.count("quotes"), 0)

    def test_call_rate_and_per_session_limits_are_enforced(self):
        agent, _ = self.agent(draft_reply(), calls_per_minute=1)
        self.chat(agent)
        with self.assertRaises(AiQuotaExceeded):
            self.chat(agent, key="too-fast")
        self.now += timedelta(seconds=61)
        agent.provider = ScriptedProvider(draft_reply())
        self.chat(agent, key="after-minute")
        limited = AgentService(self.checkout, provider=ScriptedProvider(), settings=AiSettings(session_call_limit=2))
        with self.assertRaises(AiQuotaExceeded):
            self.chat(limited, key="session-cap")

    def test_provider_wait_does_not_hold_db_lock_and_same_session_cannot_overlap(self):
        started, release = threading.Event(), threading.Event()

        def wait_reply(messages, tools, choice):
            started.set()
            if not release.wait(timeout=10):
                raise AssertionError("test provider was not released")
            return draft_reply()

        agent, _ = self.agent(wait_reply, concurrent_runs=1)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.chat, agent)
            try:
                self.assertTrue(started.wait(timeout=10))
                self.confirmed()  # Another write completes while the provider is waiting.
                with self.assertRaises(AiBusy):
                    self.chat(agent, key="overlap")
                other = self.checkout.create_session()
                with self.assertRaises(AiBusy):
                    agent.chat(other, ChatRequest(request_id="other", message="Find shoes."))
            finally:
                release.set()
            future.result(timeout=10)

    def test_concurrent_call_reservations_do_not_exceed_global_cap(self):
        agent, provider = self.agent(draft_reply(), global_call_limit=1)
        other = self.checkout.create_session()
        first, _, _ = agent._begin(self.token, "CHAT", ChatRequest(request_id="first", message="One"))
        second, _, _ = agent._begin(other, "CHAT", ChatRequest(request_id="second", message="Two"))
        barrier = threading.Barrier(2)

        def call(args):
            run_id, token = args
            barrier.wait(timeout=10)
            try:
                agent._call(run_id, token, [], [])
                return "called"
            except AiQuotaExceeded:
                return "limited"

        with ThreadPoolExecutor(max_workers=2) as executor:
            result = list(executor.map(call, [(first, self.token), (second, other)]))
        self.assertCountEqual(result, ["called", "limited"])
        self.assertEqual(len(provider.calls), 1)

    def test_expired_run_cannot_commit_a_model_result(self):
        def expire(messages, tools, choice):
            self.now += timedelta(minutes=4)
            return draft_reply()

        agent, _ = self.agent(expire)
        with self.assertRaises(Conflict):
            self.chat(agent)
        self.assertEqual(agent.run(self.token, request_id="chat-1").state, "FAILED")
        self.assertEqual(self.count("mandates"), 0)

    def test_logged_out_session_cannot_commit_inflight_draft(self):
        def logout(messages, tools, choice):
            self.checkout.end_session(self.token)
            return draft_reply()

        agent, _ = self.agent(logout)
        with self.assertRaises(AuthenticationRequired):
            self.chat(agent)
        self.assertEqual(self.count("mandates"), 0)

    def test_disabled_ai_is_explicit_and_makes_no_calls(self):
        agent = AgentService(self.checkout)
        with self.assertRaises(AiUnavailable):
            self.chat(agent)
        self.assertEqual(self.count("ai_calls"), 0)
