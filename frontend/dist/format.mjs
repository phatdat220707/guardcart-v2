export function parseMoney(value) {
  const text = String(value).trim();
  if (!/^\d+(?:\.\d{1,2})?$/.test(text)) throw new Error("Enter a non-negative amount with at most two decimal places.");
  const [whole, fraction = ""] = text.split(".");
  const amount = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  if (!Number.isSafeInteger(amount) || amount > 1_000_000_000_000) throw new Error("This amount is outside the supported range.");
  return amount;
}

export function moneyInput(amount) {
  if (!Number.isSafeInteger(amount) || amount < 0) throw new Error("Invalid amount.");
  return `${Math.floor(amount / 100)}.${String(amount % 100).padStart(2, "0")}`;
}

export function money(amount) {
  if (!Number.isSafeInteger(amount)) return "—";
  const sign = amount < 0 ? "-" : "";
  return `${sign}HK$${new Intl.NumberFormat("en", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(Math.abs(amount) / 100)}`;
}

export function localDateTime(instant) {
  const date = new Date(instant);
  if (Number.isNaN(date.getTime())) throw new Error("Choose a valid expiry time.");
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

export function displayTag(value) {
  return String(value).replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}
