// Port of observ/facts.py: DOM snapshot -> typed facts. Pinned to the Python reference by
// conformance/vectors.json.

export interface Field {
  name?: string;
  label?: string;
  type?: string;
  value?: string;
}
export interface Snapshot {
  url?: string;
  event?: string;
  key?: string | null;
  element?: { tag?: string; type?: string; role?: string; label?: string; text?: string; name?: string };
  form?: { action?: string; method?: string; submit_label?: string; fields?: Field[] } | null;
}

const RE_BANK = /routing|iban|swift|\bbic\b|sort code|account number|bank account/i;
const RE_AMOUNT = /amount|total/i;
const RE_PAY = /\bpay\b|payment|transfer|\bwire\b|remit/i;
const RE_PAYEE = /payee|vendor|beneficiary/i;
const RE_RECIPIENT = /\bto\b|recipient|\bcc\b|\bbcc\b/i;
const RE_SEND = /\bsend\b/i;
const RE_DELETE = /\bdelete\b|\bremove\b|\bdestroy\b/i;
const RE_CURRENCY = /\b(usd|eur|gbp|inr|sgd|cad|aud)\b/i;

const clean = (s: string | undefined | null, limit = 80) => (s || "").replace(/\s+/g, " ").trim().slice(0, limit);
const fieldText = (f: Field) => `${f.name || ""} ${f.label || ""}`;
const nonEmpty = (o: object | null | undefined): boolean => !!o && Object.keys(o).length > 0;

export function parseAmount(raw: string | undefined | null): number | null {
  const s = (raw || "").replace(/[^0-9.\-]/g, "");
  if (s === "" || s === "-" || s === "." || s === "-.") return null;
  // Python's float() accepts only a single well-formed number; mirror that strictly.
  if (!/^-?(\d+\.?\d*|\.\d+)$/.test(s)) return null;
  const x = Number(s);
  return Number.isFinite(x) ? x : null;
}

function urlFacts(url: string): Record<string, unknown> {
  const m = /^[a-z][a-z0-9+.-]*:\/\/([^/?#]*)([^?#]*)/i.exec(url || "");
  const host = m ? m[1].toLowerCase() : "";
  const path = m ? m[2] : (url || "").split(/[?#]/)[0];
  return { url: url || null, host: host || null, path: path || "/" };
}

function verbOf(snap: Snapshot): string {
  const el = snap.element || {};
  const form = snap.form;
  const event = snap.event;
  if (event === "submit") return "submit";
  if (event === "key") return snap.key === "Enter" && nonEmpty(form) ? "submit" : "key";
  if (event === "click") {
    const tag = (el.tag || "").toLowerCase();
    const typ = (el.type || "").toLowerCase();
    const isSubmit = (tag === "button" && (typ === "" || typ === "submit")) || (tag === "input" && typ === "submit");
    return isSubmit && nonEmpty(form) ? "submit" : "click";
  }
  if (event === "navigate" || event === "type") return event;
  return "click";
}

export function factsFromSnapshot(snap: Snapshot, sensor = "extension"): Record<string, unknown> {
  const el = snap.element || {};
  const form = snap.form || {};
  const fields = form.fields || [];
  const verb = verbOf(snap);
  const label = clean(el.label || el.text);
  const facts: Record<string, unknown> = { sensor, verb, ...urlFacts(snap.url || "") };
  facts.element_label = label || null;
  facts.element_role = el.role || null;

  const amountField = fields.find((f) => RE_AMOUNT.test(fieldText(f)));
  const amount = amountField ? parseAmount(amountField.value) : null;
  const payeeField = fields.find((f) => RE_PAYEE.test(fieldText(f)));
  const recipients: string[] = [];
  for (const f of fields) {
    if (RE_RECIPIENT.test(fieldText(f)) && (f.value || "").includes("@")) {
      recipients.push(...f.value!.split(/[,;\s]+/).filter((a) => a.includes("@")));
    }
  }
  const domains = [...new Set(recipients.map((a) => a.slice(a.lastIndexOf("@") + 1).trim().toLowerCase()))].sort();

  const context = [label, form.submit_label || "", form.action || "", ...fields.map(fieldText)].join(" ");
  const committing =
    verb === "submit" || (verb === "click" && (RE_DELETE.test(label) || RE_PAY.test(label) || RE_SEND.test(label)));

  let semantic = "none";
  if (committing && verb === "submit" && (form.method || "get").toLowerCase() === "get") {
    semantic = "navigation";
  } else if (committing) {
    if (fields.some((f) => RE_BANK.test(fieldText(f)))) semantic = "payee_change";
    else if (amount !== null || RE_PAY.test(context)) semantic = "payment";
    else if (domains.length) semantic = "email_send";
    else if (RE_DELETE.test(label)) semantic = "delete";
    else if (fields.some((f) => (f.type || "").toLowerCase() === "password")) semantic = "login";
    else semantic = verb === "submit" ? "unknown" : "none";
  } else if (verb === "navigate") {
    semantic = "navigation";
  }
  facts.semantic_action = semantic;

  if (semantic === "payment") {
    facts.amount = amount;
    let cur: string | null = null;
    if (amountField) {
      const m = RE_CURRENCY.exec(fieldText(amountField) + " " + (amountField.value || ""));
      if (m) cur = m[1].toUpperCase();
      else if (((amountField.value || "") + (amountField.label || "")).includes("$")) cur = "USD";
    }
    facts.currency = cur;
  }
  if (semantic === "payment" || semantic === "payee_change") {
    facts.payee = payeeField ? clean(payeeField.value) || null : null;
  }
  if (semantic === "email_send") {
    facts.recipient_domain = domains.length === 1 ? domains[0] : "(multiple)";
  }
  for (const k of Object.keys(facts)) if (facts[k] === null || facts[k] === undefined) delete facts[k];
  return facts;
}

export const COMMIT_ACTIONS = ["payment", "payee_change", "email_send", "delete", "unknown"];
