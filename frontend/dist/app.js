import { parseMoney, moneyInput, money, localDateTime, displayTag } from "/format.mjs?v=0.2.1";
import { pendingRequest, resultMatches } from "/recovery.mjs?v=0.2.1";
import { quoteDisplayStatus, matchesCatalogue } from "/view-state.mjs?v=0.2.1";

const $ = (id) => document.getElementById(id);
const state = { connected: false, owner: null, csrf: null, ai: false, busy: null,
  catalogue: { products: [], merchants: [], payments: [] }, conversationId: null,
  mandate: null, dirty: false, recommendation: null, selected: null,
  receipt: null, transactions: [], audit: [], mode: "empty", approvalTarget: null, pendingAI: null };
const STORAGE_KEY = "guardcart.v2.workspace";
const OPEN_QUOTES = new Set(["CREATED", "EVALUATED", "AUTO_ALLOWED", "AWAITING_APPROVAL", "APPROVED"]);
let renderedDecisionStatus = null;

class ApiError extends Error {
  constructor(status, code, message) { super(message); this.status = status; this.code = code; }
}

function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

async function api(path, { method = "GET", body, headers = {} } = {}) {
  const requestHeaders = { ...headers };
  if (body !== undefined) requestHeaders["Content-Type"] = "application/json";
  if (method !== "GET" && state.csrf) requestHeaders["X-CSRF-Token"] = state.csrf;
  let response;
  try { response = await fetch(path, { method, credentials: "same-origin", cache: "no-store",
    headers: requestHeaders, ...(body !== undefined ? { body: JSON.stringify(body) } : {}) }); }
  catch { throw new ApiError(0, "network_error", "The local server could not be reached."); }
  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, data?.error?.code || "http_error",
    data?.error?.message || "The request could not be completed.");
  return data;
}

function notice(message = "") { $("notice").textContent = message; $("notice").hidden = !message; }

function showError(error) {
  const messages = {
    ai_unavailable: "The AI provider is unavailable. You can still use the mandate form and browse the catalogue.",
    ai_protocol_error: "The AI response did not pass validation. No purchase was authorized. You can use the form and browse manually.",
    ai_quota_exceeded: "The configured AI call limit has been reached. Manual catalogue browsing is still available.",
    ai_busy: "An AI request is already running. Please wait for it to finish.",
    ai_context_changed: "Your mandate or the catalogue changed while the Agent was working. Request fresh matches.",
    ai_previous_run_failed: "That AI request did not finish. Send a new message to retry intentionally.",
    validation_error: "Please check the form fields and try again. Amounts need no more than two decimal places.",
    quote_blocked: "This quote changed or no longer meets your mandate. Refresh it before continuing.",
    approval_required: "Your approval is required for this exact quote before checkout.",
    mandate_inactive: "This mandate is no longer active. Start a new mandate for another purchase.",
    authentication_required: "Your session expired or ended. Reconnect to start a new session.",
  };
  notice(messages[error.code] || error.message || "The operation could not be completed.");
  if (error.status === 401) {
    state.connected = false;
    $("connection").className = "connection error";
    $("connection").textContent = "Session expired";
    $("reconnect").hidden = false;
  }
}

function saveWorkspace() {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ owner: state.owner, conversationId: state.conversationId,
    mandateId: state.mandate?.id || null, recommendationRun: state.recommendation?.run?.run_id || null,
    selectedQuote: state.selected?.quote?.id || null, pendingAI: state.pendingAI })); } catch { /* Storage may be unavailable. */ }
}

function savedWorkspace() { try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null"); } catch { return null; } }

function addBubble(role, text) {
  const bubble = node("div", `chat-bubble ${role}`);
  bubble.append(node("span", "bubble-label", role === "user" ? "You" : "GuardCart"), node("span", "", text));
  $("chat-messages").append(bubble);
  $("chat-messages").scrollTop = $("chat-messages").scrollHeight;
}

function greeting() {
  $("chat-messages").replaceChildren();
  addBubble("assistant", "Tell me what kind of running shoes you need and your total budget. I will prepare a draft for you to review. You stay in control of every permission.");
}

function chooseOption(id, value) {
  const select = $(id);
  if (value && ![...select.options].some((option) => option.value === value)) select.add(new Option(displayTag(value), value));
  select.value = value || "";
}

function applyTerms(terms, { confirmed = false } = {}) {
  if (terms.max_spend_minor !== null && terms.max_spend_minor !== undefined) $("budget").value = moneyInput(terms.max_spend_minor);
  else $("budget").value = "";
  chooseOption("use-case", terms.requirements?.use_case);
  chooseOption("surface", terms.requirements?.surface);
  $("size").value = terms.requirements?.size || "";
  chooseOption("cushioning", ""); chooseOption("weight-class", ""); $("brand").value = "";
  for (const preference of terms.preferences || []) {
    if (preference.attribute === "cushioning") chooseOption("cushioning", preference.value);
    if (preference.attribute === "weight_class") chooseOption("weight-class", preference.value);
    if (preference.attribute === "brand") $("brand").value = preference.value;
  }
  $("auto-enabled").checked = confirmed && terms.auto_approve_enabled === true;
  const threshold = confirmed ? terms.approval_threshold_minor : terms.suggested_approval_threshold_minor;
  if (threshold !== null && threshold !== undefined) $("threshold").value = moneyInput(threshold);
  $("threshold-suggestion").hidden = confirmed || threshold === null || threshold === undefined;
  $("threshold-suggestion").textContent = threshold !== null && threshold !== undefined
    ? `Suggested threshold: ${money(threshold)}. Automatic approval remains off until you enable it.` : "";
  if (confirmed) {
    $("points").value = String(terms.max_points);
    $("trusted-only").checked = terms.trusted_merchants_only;
    $("expiry").value = localDateTime(terms.expires_at);
  }
  toggleThreshold();
}

function toggleThreshold() {
  $("threshold-field").hidden = !$("auto-enabled").checked;
  $("threshold").disabled = !$("auto-enabled").checked;
}

function readTerms() {
  const budget = parseMoney($("budget").value);
  if (budget <= 0) throw new Error("Maximum checkout total must be greater than zero.");
  const auto = $("auto-enabled").checked;
  const threshold = auto ? parseMoney($("threshold").value) : null;
  if (auto && threshold > budget) throw new Error("The automatic approval threshold cannot exceed your maximum spend.");
  if (!/^\d+$/.test($("points").value.trim())) throw new Error("Maximum points must be a whole non-negative number.");
  const points = Number($("points").value.trim());
  if (!Number.isSafeInteger(points) || points > 1_000_000_000) throw new Error("Maximum points is outside the supported range.");
  const expiry = new Date($("expiry").value);
  if (Number.isNaN(expiry.getTime()) || expiry.getTime() <= Date.now()) throw new Error("Choose an expiry time in the future.");
  let size = $("size").value.trim().toUpperCase();
  if (/^\d{2}(?:\.\d)?$/.test(size)) size = `EU${size}`;
  const preferences = [];
  for (const [id, attribute] of [["cushioning", "cushioning"], ["weight-class", "weight_class"], ["brand", "brand"]]) {
    const value = $(id).value.trim();
    if (value) preferences.push({ attribute, value });
  }
  return { category: "running_shoes", currency: "HKD", max_spend_minor: budget,
    auto_approve_enabled: auto, approval_threshold_minor: threshold, max_points: points,
    trusted_merchants_only: $("trusted-only").checked, expires_at: expiry.toISOString(),
    requirements: { use_case: $("use-case").value || null, surface: $("surface").value || null, size: size || null }, preferences };
}

function activeMandate() { return state.connected && state.mandate?.status === "ACTIVE" && !state.dirty && Date.parse(state.mandate.expires_at) > Date.now(); }

function controls() {
  const busy = Boolean(state.busy);
  const unresolved = Boolean(state.pendingAI);
  $("send-chat").disabled = busy || unresolved || !state.connected || !state.ai;
  $("chat-input").disabled = busy || !state.connected || !state.ai;
  $("mandate-fields").disabled = busy || !state.connected;
  $("confirm-mandate").disabled = busy || unresolved || !state.connected;
  $("find-matches").disabled = busy || unresolved || !activeMandate() || !state.ai;
  $("browse-catalogue").disabled = busy || !state.connected;
  $("new-mandate").disabled = busy || unresolved || !state.connected;
  $("clear-chat").disabled = busy || unresolved;
  $("recover-ai").hidden = !unresolved;
  $("recover-ai").disabled = busy || !state.connected;
  $("refresh-activity").disabled = busy || !state.connected;
  $("manual-payment").disabled = busy;
  for (const button of document.querySelectorAll(".product-card button")) button.disabled = busy || !activeMandate();
  $("busy-banner").hidden = !busy;
  $("ai-status").textContent = state.ai ? "AI enabled · Your confirmation is required" : "AI off · Use the mandate form and catalogue";
  $("confirm-mandate").textContent = state.mandate?.status === "ACTIVE" ? "Confirm revised mandate" : "Confirm mandate";
  const status = !state.mandate ? "Unconfirmed" : state.dirty ? "Edits not confirmed" : displayTag(state.mandate.status.toLowerCase());
  $("mandate-status").textContent = status;
  $("mandate-status").className = `pill ${state.mandate?.status === "ACTIVE" && !state.dirty ? "success" : "neutral"}`;
  $("limit-budget").textContent = state.mandate ? money(state.mandate.max_spend_minor) : "Not confirmed";
  $("limit-approval").textContent = state.mandate?.auto_approve_enabled ? `Auto up to ${money(state.mandate.approval_threshold_minor)}` : "Always ask";
  $("limit-state").textContent = status;
  $("mandate-help").textContent = state.dirty && state.mandate && state.mandate.status !== "ACTIVE"
    ? "Confirm these terms to create a new mandate. Previous purchases remain recorded."
    : state.mandate?.status === "CONSUMED" ? "This mandate has been used. Start a new mandate for another purchase."
    : state.dirty && state.mandate ? "Confirm your edits before selecting or purchasing. Previous quotes will be invalidated."
    : "One mandate permits one completed simulated purchase.";
  $("results-note").textContent = state.dirty ? "Confirm your mandate edits first." : activeMandate()
    ? "Agent recommendations do not execute a purchase." : "Confirm an active mandate to evaluate products.";
  toggleThreshold();
  decisionControls();
}

async function action(label, operation) {
  if (state.busy) return;
  state.busy = label; $("busy-title").textContent = label; notice(); controls();
  try { await operation(); }
  catch (error) {
    showError(error);
    if (state.selected && state.connected) {
      try {
        state.selected = await api(`/api/quotes/${state.selected.quote.id}`);
        if (state.mandate) state.mandate = await api(`/api/mandates/${state.mandate.id}`);
        await refreshActivity();
      } catch { /* Keep the original failure visible. */ }
    }
  } finally { state.busy = null; controls(); renderDecision(); saveWorkspace(); }
}

async function refreshActivity() {
  if (!state.connected) return;
  const [audit, transactions, usage] = await Promise.all([api("/api/audit"), api("/api/transactions"), api("/api/agent/usage")]);
  state.audit = audit; state.transactions = transactions;
  $("usage-label").textContent = `AI calls ${usage.calls_used}/${usage.session_call_limit}`;
  $("activity-summary").textContent = `${transactions.length} completed simulation${transactions.length === 1 ? "" : "s"} · ${audit.length} recorded events`;
  $("audit-list").replaceChildren();
  for (const event of [...audit].reverse().slice(0, 30)) {
    const item = node("li", "");
    item.append(node("strong", "", displayTag(event.event_type)));
    const bits = Object.entries(event.details).flatMap(([key, value]) => {
      if (key === "final_total_minor") return [money(value)];
      if (key === "auto_approve_enabled") return [`Auto approval ${value ? "on" : "off"}`];
      if (["status", "reason", "kind", "authorization_type"].includes(key)) return [displayTag(String(value))];
      if (key === "provider_calls") return [`${value} model call${value === 1 ? "" : "s"}`];
      if (key === "version") return [`Mandate version ${value}`];
      return [];
    }).join(" · ");
    if (bits) item.append(node("p", "", bits));
    const time = node("time", "", new Date(event.timestamp).toLocaleTimeString("en", { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    time.dateTime = event.timestamp; item.append(time); $("audit-list").append(item);
  }
  if (state.selected) state.receipt = transactions.find((item) => item.quote_id === state.selected.quote.id) || null;
}

function attributeTags(product) {
  const tags = node("div", "attribute-tags");
  for (const value of [...(product?.attributes?.use_cases || []).slice(0, 2), ...(product?.attributes?.surfaces || []).slice(0, 1)]) tags.append(node("span", "", displayTag(value)));
  return tags;
}

function breakdown(parent, rows) {
  const list = node("dl", "price-breakdown");
  for (const [label, value] of rows) list.append(node("dt", "", label), node("dd", "", value));
  parent.append(list);
}

function productCard(product, index, recommendation = null) {
  const selected = recommendation && state.selected?.quote.id === recommendation.quote.quote.id;
  const card = node("article", `product-card${selected ? " selected" : ""}${recommendation && index === 0 ? " featured" : ""}`);
  const top = node("div", "product-card-top");
  top.append(node("span", "rank-label", recommendation ? index === 0 ? "Top catalogue match" : `Alternative ${index + 1}` : "Demo catalogue"),
    node("span", "product-type", product.id)); card.append(top, node("h3", "", product.name));
  const merchant = state.catalogue.merchants.find((entry) => entry.id === product.merchant_id);
  const merchantLine = node("div", "merchant-line", merchant?.name || recommendation?.merchant_name || "Demo merchant");
  merchantLine.append(node("span", `trust-text${merchant?.trusted ? "" : " untrusted"}`, merchant?.trusted ? "✓ Trusted" : "Unverified"));
  card.append(merchantLine, attributeTags(product));
  if (recommendation) {
    const quote = recommendation.quote.quote;
    breakdown(card, [["Product", money(quote.price_minor)], ["Shipping", money(quote.shipping_minor)],
      ["Payment fee", money(quote.fee_minor)], ["Demo reward", `−${money(quote.reward_minor)}`]]);
    const total = node("div", "total-line"); total.append(node("span", "", "Checkout total"), node("strong", "", money(quote.final_total_minor))); card.append(total);
    card.append(node("p", "effective-line", `Effective cost ${money(quote.effective_cost_minor)}`), node("p", "product-explanation", recommendation.explanation));
    const button = node("button", `button ${index === 0 ? "primary" : "secondary"}`, selected ? "Selected" : "Review this option");
    button.type = "button"; button.disabled = !activeMandate() || Boolean(state.busy);
    button.addEventListener("click", () => action("Checking the stored quote…", async () => {
      state.selected = await api(`/api/quotes/${quote.id}`); state.receipt = null; renderMatches();
      await refreshActivity(); renderDecision(); $("decision-section").scrollIntoView({ behavior: "smooth", block: "start" });
    })); card.append(button);
  } else {
    breakdown(card, [["Product", money(product.price_minor)], ["Shipping", money(product.shipping_minor)],
      ["Availability", product.availability ? "In stock" : "Out of stock"]]);
    const total = node("div", "total-line"); total.append(node("span", "", "Before payment fees"), node("strong", "", money(product.price_minor + product.shipping_minor))); card.append(total);
    card.append(node("p", "product-explanation", product.description));
    const button = node("button", "button secondary", "Evaluate this option"); button.type = "button";
    button.disabled = !activeMandate() || Boolean(state.busy);
    button.addEventListener("click", () => action("Evaluating this product…", async () => {
      state.selected = await api("/api/quotes", { method: "POST", body: { mandate_id: state.mandate.id,
        product_id: product.id, payment_id: $("manual-payment").value } });
      state.receipt = null; await refreshActivity(); renderDecision();
      $("decision-section").scrollIntoView({ behavior: "smooth", block: "start" });
    })); card.append(button);
  }
  return card;
}

function renderMatches() {
  $("empty-results").hidden = state.mode !== "empty";
  $("browse-controls").hidden = state.mode !== "browse";
  $("agent-trace").hidden = state.mode !== "matches" || !state.recommendation;
  $("product-grid").replaceChildren();
  if (state.mode === "matches" && state.recommendation) {
    $("results-title").textContent = "Your catalogue matches.";
    $("results-subtitle").textContent = state.recommendation.message;
    for (const [index, entry] of state.recommendation.recommendations.entries()) {
      const product = state.catalogue.products.find((item) => item.id === entry.product_id);
      if (product) $("product-grid").append(productCard(product, index, entry));
    }
    const trace = $("agent-trace-content"); trace.replaceChildren();
    trace.append(node("p", "", `Gateway model ID: ${state.recommendation.run.requested_model} · ${state.recommendation.run.provider_calls} calls · ${(state.recommendation.run.elapsed_ms / 1000).toFixed(1)}s. Ranking, costs and permission checks come from the backend.`));
    const list = node("ol", "");
    for (const step of state.recommendation.tool_trace) list.append(node("li", "", `${step.name} — ${JSON.stringify(step.arguments)}`));
    trace.append(list);
  } else if (state.mode === "browse") {
    $("results-title").textContent = "Explore the demo catalogue.";
    $("results-subtitle").textContent = "Manual browsing uses no AI calls. The backend evaluates every chosen option.";
    const query = $("catalogue-search").value.trim().toLowerCase();
    const products = state.catalogue.products.filter((item) => matchesCatalogue(item, query));
    for (const [index, product] of products.entries()) $("product-grid").append(productCard(product, index));
    if (!products.length) $("product-grid").append(node("p", "muted", "No products match this catalogue filter."));
  } else {
    $("results-title").textContent = "Find the right fit.";
    $("results-subtitle").textContent = "Meet your needs first. Compare the cost second.";
  }
}

function decisionControls() {
  const view = state.selected;
  if (!view) return;
  const quote = view.quote;
  const status = quoteDisplayStatus(view, state.receipt);
  const expired = status === "EXPIRED";
  const ready = activeMandate() && !state.busy && !expired && OPEN_QUOTES.has(quote.status) && !state.receipt;
  $("approve-purchase").hidden = status !== "ASK" || quote.status === "APPROVED" || !OPEN_QUOTES.has(quote.status);
  $("simulate-purchase").hidden = !((status === "APPROVE" || (status === "ASK" && quote.status === "APPROVED")) && OPEN_QUOTES.has(quote.status));
  $("decline-purchase").hidden = quote.status !== "AWAITING_APPROVAL" || !["ASK", "APPROVE"].includes(status);
  $("approve-purchase").disabled = !ready; $("simulate-purchase").disabled = !ready; $("decline-purchase").disabled = !ready;
  $("refresh-quote").disabled = !activeMandate() || Boolean(state.busy) || Boolean(state.receipt);
  $("quote-caption").hidden = status === "COMPLETED";
  $("decision-actions").hidden = status === "COMPLETED";
  if (status !== "COMPLETED") $("quote-caption").textContent = state.dirty ? "Mandate edits are not confirmed. Confirm them before continuing."
    : status === "STALE" ? "This quote was invalidated. Confirm an active mandate and request a fresh quote."
    : status === "DECLINED" ? "This quote was declined. Nothing was purchased."
    : expired ? "This quote has expired. Refresh it before continuing."
    : `Quote expires ${new Date(quote.expires_at).toLocaleTimeString("en", { hour: "2-digit", minute: "2-digit" })}. Price and stock are checked again at checkout.`;
}

function renderDecision() {
  const view = state.selected;
  const status = quoteDisplayStatus(view, state.receipt);
  renderedDecisionStatus = status;
  for (const id of ["decision-amount", "quote-caption", "decision-actions", "policy-details"]) $(id).hidden = !view;
  $("receipt").hidden = !state.receipt;
  if (!view) { $("decision-title").textContent = "Permission before purchase."; $("decision-description").textContent = "Choose a product to see exactly what your mandate allows."; $("decision-badge").className = "pill neutral"; $("decision-badge").textContent = "No quote"; return; }
  const quote = view.quote;
  const done = status === "COMPLETED", declined = status === "DECLINED";
  $("decision-badge").textContent = { ASK: "Needs approval", APPROVE: "Auto allowed", BLOCK: "Blocked", COMPLETED: "Simulation complete", DECLINED: "Declined", EXPIRED: "Expired", STALE: "Needs fresh quote" }[status] || status;
  $("decision-badge").className = `pill ${["APPROVE", "COMPLETED"].includes(status) ? "success" : status === "ASK" ? "ask" : status === "BLOCK" ? "block" : "neutral"}`;
  $("decision-title").textContent = { ASK: "Your approval is needed.", APPROVE: "Within your permission.", BLOCK: "This purchase is blocked.", COMPLETED: "Purchased in simulation.", DECLINED: "You declined this option.", EXPIRED: "This quote has expired.", STALE: "Request a fresh quote." }[status];
  $("decision-description").textContent = done ? "One purchase completed. This mandate is now consumed; another purchase needs a new mandate."
    : declined ? "Nothing was purchased. You can evaluate a different option under this mandate."
    : status === "EXPIRED" ? "The old quote cannot be approved or purchased. Refresh it to check current terms."
    : status === "STALE" ? "The mandate or catalogue changed. This quote no longer grants purchase permission."
    : view.policy.reasons.join(" ");
  $("decision-total").textContent = money(quote.final_total_minor);
  $("decision-effective").textContent = `Effective cost ${money(quote.effective_cost_minor)} after ${money(quote.reward_minor)} demo reward`;
  $("policy-checks").replaceChildren();
  for (const check of view.policy.checks) {
    const item = node("li", "");
    item.append(node("span", `rule-icon ${check.outcome === "FAIL" ? "fail" : check.outcome === "ASK" ? "ask" : check.outcome === "NOT_EVALUATED" ? "skipped" : ""}`,
      { PASS: "✓", FAIL: "×", ASK: "?", NOT_EVALUATED: "—" }[check.outcome]));
    const description = node("div", ""); description.append(node("strong", "", check.label), node("p", "", check.detail)); item.append(description); $("policy-checks").append(item);
  }
  if (done && state.receipt) $("receipt").textContent = `Transaction ${state.receipt.id} · ${state.receipt.authorization_type === "USER_APPROVAL" ? "Your explicit approval" : "Your automatic approval rule"} · No real charge.`;
  decisionControls();
}

async function selectFreshQuote() {
  const original = state.selected.quote;
  state.selected = await api("/api/quotes", { method: "POST", body: { mandate_id: state.mandate.id,
    product_id: original.product_id, payment_id: original.payment_id } });
  state.receipt = null; await refreshActivity(); renderDecision();
}

async function completePurchase(quoteId) {
  const result = await api(`/api/quotes/${quoteId}/checkout`, { method: "POST", body: {},
    headers: { "Idempotency-Key": `checkout-${quoteId}` } });
  state.receipt = result; state.selected = await api(`/api/quotes/${quoteId}`);
  state.mandate = await api(`/api/mandates/${result.mandate_id}`);
  await refreshActivity(); renderDecision(); renderMatches();
}

async function applyAIResult(kind, result) {
  if (!resultMatches(kind, result)) throw new Error("The stored AI result could not be validated.");
  if (kind === "CHAT") {
    state.conversationId = result.conversation_id;
    const conversation = await api(`/api/conversations/${result.conversation_id}`);
    $("chat-messages").replaceChildren();
    for (const message of conversation.messages) addBubble(message.role, message.content);
    applyTerms(result.draft); state.dirty = true;
  } else {
    state.recommendation = result; state.mode = "matches";
    state.selected = result.recommendations[0]?.quote || null; state.receipt = null;
    renderMatches(); renderDecision();
  }
  state.pendingAI = null; saveWorkspace(); await refreshActivity();
}

async function runAI(kind, path, body) {
  if (state.pendingAI) throw new Error("Recover the previous AI request before starting another.");
  const requestId = crypto.randomUUID();
  state.pendingAI = { requestId, kind }; saveWorkspace(); controls();
  const result = await api(path, { method: "POST", body: { ...body, request_id: requestId } });
  await applyAIResult(kind, result);
}

async function recoverAI() {
  const pending = state.pendingAI;
  if (!pending) return;
  let run;
  try { run = await api(`/api/agent/requests/${pending.requestId}`); }
  catch (error) {
    if (error.status !== 404) throw error;
    state.pendingAI = null; saveWorkspace();
    notice("No saved run was found. You can send your request again intentionally. Recovery made no model call.");
    return;
  }
  if (run.kind !== pending.kind) throw new Error("The saved request type does not match this workspace.");
  if (run.state === "RUNNING") {
    notice("Your AI request is still running. Check again shortly; recovery does not start another model call.");
    return;
  }
  if (run.state === "FAILED") {
    state.pendingAI = null; saveWorkspace();
    notice("The previous AI request failed. You may send a new request intentionally. No purchase was authorized by the Agent.");
    return;
  }
  if (run.state !== "COMPLETED") throw new Error("The saved request has an unknown state.");
  await applyAIResult(pending.kind, await api(`/api/agent/runs/${run.id}/result`));
  notice("Recovered the saved AI result without another model call.");
}

$("recover-ai").addEventListener("click", () => action("Recovering your saved result…", recoverAI));
$("chat-form").addEventListener("submit", (event) => {
  event.preventDefault(); const message = $("chat-input").value.trim(); if (!message) return;
  action("Understanding your request…", async () => {
    addBubble("user", message); $("chat-input").value = "";
    await runAI("CHAT", "/api/chat", { message, conversation_id: state.conversationId });
  });
});
$("chat-input").addEventListener("keydown", (event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("chat-form").requestSubmit(); } });
for (const button of document.querySelectorAll("[data-example]")) button.addEventListener("click", () => { $("chat-input").value = button.dataset.example; $("chat-input").focus(); });
$("clear-chat").addEventListener("click", () => { state.conversationId = null; greeting(); saveWorkspace(); });

$("mandate-fields").addEventListener("input", () => { state.dirty = true; controls(); renderMatches(); });
$("mandate-fields").addEventListener("change", () => { state.dirty = true; controls(); renderMatches(); });
$("mandate-form").addEventListener("submit", (event) => {
  event.preventDefault(); $("form-error").hidden = true;
  let terms; try { terms = readTerms(); } catch (error) { $("form-error").textContent = error.message; $("form-error").hidden = false; return; }
  action("Confirming your mandate…", async () => {
    const path = state.mandate?.status === "ACTIVE" ? `/api/mandates/${state.mandate.id}/reconfirm` : "/api/mandates";
    state.mandate = await api(path, { method: "POST", body: terms }); state.dirty = false;
    state.recommendation = null; state.selected = null; state.receipt = null;
    if (state.mode !== "browse") state.mode = "empty";
    applyTerms(state.mandate, { confirmed: true }); renderMatches(); await refreshActivity();
  });
});
$("find-matches").addEventListener("click", () => action("Searching the controlled catalogue…", async () => {
  await runAI("RECOMMEND", "/api/agent/recommend", { mandate_id: state.mandate.id });
}));
$("browse-catalogue").addEventListener("click", () => { state.mode = "browse"; renderMatches(); });
$("catalogue-search").addEventListener("input", renderMatches);
$("refresh-quote").addEventListener("click", () => action("Refreshing the quote…", selectFreshQuote));
$("decline-purchase").addEventListener("click", () => action("Recording your decision…", async () => {
  const id = state.selected.quote.id;
  await api(`/api/quotes/${id}/approval`, { method: "POST", body: { approve: false } });
  state.selected = await api(`/api/quotes/${id}`); await refreshActivity();
}));
$("approve-purchase").addEventListener("click", () => {
  if (!activeMandate() || state.busy) return;
  state.approvalTarget = state.selected.quote.id;
  const product = state.catalogue.products.find((item) => item.id === state.selected.quote.product_id);
  $("approval-description").textContent = product?.name || "Selected catalogue product";
  $("approval-amount").textContent = money(state.selected.quote.final_total_minor);
  $("approval-dialog").showModal();
});
$("approval-cancel").addEventListener("click", () => $("approval-dialog").close());
$("approval-confirm").addEventListener("click", () => {
  const id = state.approvalTarget; $("approval-dialog").close();
  action("Checking and completing your simulation…", async () => {
    await api(`/api/quotes/${id}/approval`, { method: "POST", body: { approve: true } });
    await completePurchase(id);
  });
});
$("simulate-purchase").addEventListener("click", () => action("Completing your simulated checkout…", () => completePurchase(state.selected.quote.id)));
$("refresh-activity").addEventListener("click", () => action("Refreshing your activity…", async () => {
  if (state.mandate) state.mandate = await api(`/api/mandates/${state.mandate.id}`);
  if (state.selected) state.selected = await api(`/api/quotes/${state.selected.quote.id}`);
  await refreshActivity();
}));
$("new-mandate").addEventListener("click", () => action("Starting a new mandate…", async () => {
  if (state.mandate?.status === "ACTIVE") await api(`/api/mandates/${state.mandate.id}/revoke`, { method: "POST", body: {} });
  state.mandate = null; state.selected = null; state.receipt = null; state.recommendation = null; state.dirty = false; state.mode = "empty";
  $("auto-enabled").checked = false; $("threshold-suggestion").hidden = true;
  $("points").value = "0"; $("trusted-only").checked = true;
  $("expiry").value = localDateTime(Date.now() + 24 * 60 * 60 * 1000);
  renderMatches(); await refreshActivity(); $("mandate-section").scrollIntoView({ behavior: "smooth" });
}));

async function initialize() {
  notice(); state.connected = false; controls(); $("connection").textContent = "Connecting…";
  try {
    const [health, catalogue] = await Promise.all([api("/api/health"), api("/api/catalogue")]);
    state.ai = health.ai_enabled; state.catalogue = catalogue;
    const session = await api("/api/session", { method: "POST", body: {} });
    state.owner = session.id; state.csrf = session.csrf_token; state.connected = true;
    $("connection").className = "connection ready"; $("connection").textContent = "Session active"; $("reconnect").hidden = true;
    $("manual-payment").replaceChildren();
    for (const payment of catalogue.payments.filter((entry) => entry.enabled)) $("manual-payment").add(new Option(payment.name, payment.id));
    $("manual-payment").value = catalogue.payments.some((entry) => entry.id === "demo-card") ? "demo-card" : catalogue.payments[0]?.id || "";
    const saved = savedWorkspace();
    if (saved?.owner === state.owner) {
      state.pendingAI = pendingRequest(saved.pendingAI);
      if (saved.conversationId) {
        try { const conversation = await api(`/api/conversations/${saved.conversationId}`); state.conversationId = conversation.id;
          $("chat-messages").replaceChildren(); for (const message of conversation.messages) addBubble(message.role, message.content);
        } catch { state.conversationId = null; }
      }
      if (saved.mandateId) {
        try { state.mandate = await api(`/api/mandates/${saved.mandateId}`); applyTerms(state.mandate, { confirmed: true }); state.dirty = false; } catch { state.mandate = null; }
      }
      if (saved.recommendationRun) {
        try { const result = await api(`/api/agent/runs/${saved.recommendationRun}/result`);
          if (resultMatches("RECOMMEND", result)) { state.recommendation = result; state.mode = "matches"; }
        } catch { state.recommendation = null; }
      }
      if (saved.selectedQuote) { try { state.selected = await api(`/api/quotes/${saved.selectedQuote}`); } catch { state.selected = null; } }
    } else {
      state.mandate = null; state.selected = null; state.recommendation = null; state.conversationId = null;
      state.receipt = null; state.mode = "empty"; state.dirty = false; state.pendingAI = null;
      $("mandate-form").reset(); $("threshold-suggestion").hidden = true;
      $("expiry").value = localDateTime(Date.now() + 24 * 60 * 60 * 1000); greeting();
    }
    if (state.pendingAI) await recoverAI();
    await refreshActivity(); renderMatches(); renderDecision(); saveWorkspace();
  } catch (error) { showError(error); $("connection").textContent = "Server unavailable"; $("connection").className = "connection error"; $("reconnect").hidden = false; }
  controls();
}

$("reconnect").addEventListener("click", initialize);
$("expiry").value = localDateTime(Date.now() + 24 * 60 * 60 * 1000);
greeting(); controls(); initialize();
let previouslyActive = false;
setInterval(() => {
  const active = Boolean(activeMandate());
  if (active !== previouslyActive) { previouslyActive = active; controls(); }
  if (!state.selected) return;
  if (quoteDisplayStatus(state.selected, state.receipt) !== renderedDecisionStatus) renderDecision();
  else decisionControls();
}, 1000);
