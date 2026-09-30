"""Deterministic facts from a DOM/accessibility snapshot of the action target.

The snapshot is produced by observ/assets/snapshot.js (injected by the browser extension
and by harness adapters that drive a browser). extension/src/facts.ts is a line-for-line
port; conformance/vectors.json pins both to the same output.

Snapshot shape:
    {"url": str, "event": "click"|"submit"|"key"|"type"|"navigate", "key": str|None,
     "element": {"tag", "type", "role", "label", "text", "name"},
     "form": {"action", "method", "submit_label",
              "fields": [{"name", "label", "type", "value"}]} | None}
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

RE_BANK = re.compile(r"routing|iban|swift|\bbic\b|sort code|account number|bank account", re.I)
RE_AMOUNT = re.compile(r"amount|total", re.I)
RE_PAY = re.compile(r"\bpay\b|payment|transfer|\bwire\b|remit", re.I)
RE_PAYEE = re.compile(r"payee|vendor|beneficiary", re.I)
RE_RECIPIENT = re.compile(r"\bto\b|recipient|\bcc\b|\bbcc\b", re.I)
RE_SEND = re.compile(r"\bsend\b", re.I)
RE_DELETE = re.compile(r"\bdelete\b|\bremove\b|\bdestroy\b", re.I)
RE_CURRENCY = re.compile(r"\b(usd|eur|gbp|inr|sgd|cad|aud)\b", re.I)
RE_NUMBER_JUNK = re.compile(r"[^0-9.\-]")
RE_SPLIT = re.compile(r"[,;\s]+")
RE_SPACE = re.compile(r"\s+")


def _clean(s: str | None, limit: int = 80) -> str:
    return RE_SPACE.sub(" ", s or "").strip()[:limit]


def _field_text(f: dict) -> str:
    return f"{f.get('name') or ''} {f.get('label') or ''}"


def parse_amount(raw: str | None) -> int | float | None:
    s = RE_NUMBER_JUNK.sub("", raw or "")
    if s in ("", "-", ".", "-."):
        return None
    try:
        x = float(s)
    except ValueError:
        return None
    return int(x) if x == int(x) else x


def _url_facts(url: str) -> dict:
    p = urlparse(url or "")
    return {
        "url": url or None,
        "host": (p.netloc or "").lower() or None,
        "path": p.path or "/",
    }


def _verb(snap: dict) -> str:
    event = snap.get("event")
    el = snap.get("element") or {}
    form = snap.get("form")
    if event == "submit":
        return "submit"
    if event == "key":
        return "submit" if (snap.get("key") == "Enter" and form) else "key"
    if event == "click":
        tag = (el.get("tag") or "").lower()
        typ = (el.get("type") or "").lower()
        is_submit = (tag == "button" and typ in ("", "submit")) or (tag == "input" and typ == "submit")
        return "submit" if (is_submit and form) else "click"
    if event in ("navigate", "type"):
        return event
    return "click"


def facts_from_snapshot(snap: dict, sensor: str = "extension") -> dict:
    el = snap.get("element") or {}
    form = snap.get("form") or {}
    fields = form.get("fields") or []
    verb = _verb(snap)
    label = _clean(el.get("label") or el.get("text"))
    facts: dict = {"sensor": sensor, "verb": verb, **_url_facts(snap.get("url", ""))}
    facts["element_label"] = label or None
    facts["element_role"] = el.get("role") or None

    amount_field = next((f for f in fields if RE_AMOUNT.search(_field_text(f))), None)
    amount = parse_amount(amount_field.get("value")) if amount_field else None
    payee_field = next((f for f in fields if RE_PAYEE.search(_field_text(f))), None)
    recipients: list[str] = []
    for f in fields:
        if RE_RECIPIENT.search(_field_text(f)) and "@" in (f.get("value") or ""):
            recipients += [a for a in RE_SPLIT.split(f["value"]) if "@" in a]
    domains = sorted({a.rsplit("@", 1)[1].strip().lower() for a in recipients})

    context = " ".join(
        [label, form.get("submit_label") or "", form.get("action") or ""]
        + [_field_text(f) for f in fields]
    )
    committing = verb == "submit" or (verb == "click" and bool(
        RE_DELETE.search(label) or RE_PAY.search(label) or RE_SEND.search(label)
    ))

    semantic = "none"
    if committing and verb == "submit" and (form.get("method") or "get").lower() == "get":
        semantic = "navigation"  # GET forms (search boxes) navigate; they don't commit
    elif committing:
        if any(RE_BANK.search(_field_text(f)) for f in fields):
            semantic = "payee_change"
        elif amount is not None or RE_PAY.search(context):
            semantic = "payment"
        elif domains:
            semantic = "email_send"
        elif RE_DELETE.search(label):
            semantic = "delete"
        elif any((f.get("type") or "").lower() == "password" for f in fields):
            semantic = "login"
        else:
            semantic = "unknown" if verb == "submit" else "none"
    elif verb == "navigate":
        semantic = "navigation"
    facts["semantic_action"] = semantic

    if semantic == "payment":
        facts["amount"] = amount
        cur = None
        if amount_field:
            m = RE_CURRENCY.search(_field_text(amount_field) + " " + (amount_field.get("value") or ""))
            if m:
                cur = m.group(1).upper()
            elif "$" in (amount_field.get("value") or "") + (amount_field.get("label") or ""):
                cur = "USD"
        facts["currency"] = cur
    if semantic in ("payment", "payee_change"):
        facts["payee"] = _clean(payee_field.get("value")) or None if payee_field else None
    if semantic == "email_send":
        # Several distinct domains collapse to a sentinel that never equals an allowed
        # domain, so "never email outside X" fails closed on mixed recipient lists.
        facts["recipient_domain"] = domains[0] if len(domains) == 1 else "(multiple)"
    return {k: v for k, v in facts.items() if v is not None}
