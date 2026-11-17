// Present quote lifecycle before its last recorded policy result.
export function quoteDisplayStatus(view, receipt = null, now = Date.now()) {
  if (!view) return null;
  const quote = view.quote;
  if (quote.status === "COMPLETED" || receipt?.quote_id === quote.id) return "COMPLETED";
  if (quote.status === "DECLINED") return "DECLINED";
  if (quote.status === "STALE") return "STALE";
  const expiry = Date.parse(quote.expires_at);
  if (quote.status === "EXPIRED" || !Number.isFinite(expiry) || expiry <= now) return "EXPIRED";
  return view.policy.status;
}

export function matchesCatalogue(product, query) {
  const text = `${product.id} ${product.name} ${product.attributes?.brand || ""} ${(product.attributes?.use_cases || []).join(" ")}`;
  return text.toLowerCase().includes(query.trim().toLowerCase());
}
