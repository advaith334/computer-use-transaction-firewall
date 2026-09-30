"""Regenerate conformance/vectors.json from the Python reference implementation.

    uv run python conformance/generate.py

The Python evaluator and facts extractor define the expected outputs; the TypeScript
port (extension/src) must reproduce every vector exactly (npm test in extension/).
tests/test_conformance.py fails if Python drifts from the committed vectors.
"""

from __future__ import annotations

import json
from pathlib import Path

from observ.compile.bundle import seal
from observ.compile.pipeline import compile_policies, read_answers, read_policies
from observ.evaluator import evaluate
from observ.facts import facts_from_snapshot

ROOT = Path(__file__).resolve().parent.parent


def conformance_bundle() -> dict:
    bundle, _ = compile_policies(
        read_policies(ROOT / "policies.txt"), read_answers(ROOT / "policies.answers.yaml"), mode="enforce"
    )
    # Extra rules exercising shadow mode, sensor scoping, glob and not_in.
    bundle["rules"] += [
        {"id": "x1", "source": "shadow: deletes anywhere", "effect": "block", "mode": "shadow",
         "on_unknown": "allow", "when": {"all": [{"fact": "semantic_action", "op": "eq", "value": "delete"}]}},
        {"id": "x2", "source": "harness may not navigate off-site", "effect": "block", "mode": "enforce",
         "on_unknown": "allow", "sensors": ["harness"],
         "when": {"all": [{"fact": "verb", "op": "eq", "value": "navigate"},
                          {"fact": "host", "op": "not_in", "value": ["127.0.0.1:8765", "portal:8000"]}]}},
        {"id": "x3", "source": "hold logins to *.corp.example", "effect": "hold", "mode": "enforce",
         "on_unknown": "allow",
         "when": {"any": [{"all": [{"fact": "semantic_action", "op": "eq", "value": "login"},
                                   {"fact": "host", "op": "glob", "value": "*.corp.example"}]},
                          {"not": {"fact": "element_label", "op": "exists"}}]}},
    ]
    bundle["tests"] = []
    return seal(bundle)


def field(name, label, value, type_="text"):
    return {"name": name, "label": label, "type": type_, "value": value}


def snap(url, event, form=None, element=None, key=None):
    return {"url": url, "event": event, "key": key, "element": element or {}, "form": form}


BTN = {"tag": "button", "type": "submit", "role": "button"}
P = "http://127.0.0.1:8765"

SNAPSHOTS = {
    "pay 7200 USD": snap(f"{P}/invoices/INV-1042", "submit", {
        "action": "/pay", "method": "post", "submit_label": "Pay invoice",
        "fields": [field("payee", "Payee", "Acme Supplies Ltd", "select-one"),
                   field("amount", "Amount (USD)", "$7,200.00"), field("memo", "Memo", "INV-1042")]},
        dict(BTN, label="Pay invoice", text="Pay invoice", name="")),
    "pay 200": snap(f"{P}/invoices/INV-1043", "submit", {
        "action": "/pay", "method": "post", "submit_label": "Pay invoice",
        "fields": [field("payee", "Payee", "Globex Corp"), field("amount", "Amount", "200")]},
        dict(BTN, label="Pay invoice", text="Pay invoice", name="")),
    "pay 5000.50 eur": snap(f"{P}/pay", "submit", {
        "action": "/pay", "method": "post", "submit_label": "Send",
        "fields": [field("vendor", "Vendor", "Initech"), field("total", "Total EUR", "5000.50")]},
        dict(BTN, label="Send", text="Send", name="")),
    "bank change": snap(f"{P}/vendors/acme/bank", "submit", {
        "action": "/vendors/acme/bank", "method": "post", "submit_label": "Save bank details",
        "fields": [field("vendor", "Vendor", "Acme Supplies Ltd"),
                   field("routing", "Routing number", "021000021"),
                   field("account", "Account number", "99887766")]},
        dict(BTN, label="Save bank details", text="Save bank details", name="")),
    "email external": snap(f"{P}/mail/compose", "submit", {
        "action": "/mail/send", "method": "post", "submit_label": "Send",
        "fields": [field("to", "To", "cfo@evil-corp.com"), field("subject", "Subject", "Invoices")]},
        dict(BTN, label="Send", text="Send", name="")),
    "email internal": snap(f"{P}/mail/compose", "submit", {
        "action": "/mail/send", "method": "post", "submit_label": "Send",
        "fields": [field("to", "To", "ap@acme.com"), field("subject", "Subject", "Hi")]},
        dict(BTN, label="Send", text="Send", name="")),
    "email subdomain": snap(f"{P}/mail/compose", "submit", {
        "action": "/mail/send", "method": "post", "submit_label": "Send",
        "fields": [field("to", "To", "ap@eu.acme.com")]},
        dict(BTN, label="Send", text="Send", name="")),
    "email mixed recipients": snap(f"{P}/mail/compose", "submit", {
        "action": "/mail/send", "method": "post", "submit_label": "Send",
        "fields": [field("to", "To", "ap@acme.com, x@gmail.com")]},
        dict(BTN, label="Send", text="Send", name="")),
    "admin delete click": snap(f"{P}/admin/users", "click", {
        "action": "/admin/users/delete", "method": "post", "submit_label": "Delete user",
        "fields": [field("user", "User", "jane@acme.com")]},
        dict(BTN, label="Delete user", text="Delete user", name="")),
    "search GET form": snap(f"{P}/search", "submit", {
        "action": "/search", "method": "get", "submit_label": "Search",
        "fields": [field("q", "Search", "invoices")]},
        dict(BTN, label="Search", text="Search", name="")),
    "unknown POST form": snap(f"{P}/misc", "submit", {
        "action": "/do", "method": "post", "submit_label": "Confirm",
        "fields": [field("ref_1", "Reference", "A-17")]},
        dict(BTN, label="Confirm", text="Confirm", name="")),
    "enter key in form": snap(f"{P}/invoices/INV-1042", "key", {
        "action": "/pay", "method": "post", "submit_label": "Pay invoice",
        "fields": [field("amount", "Amount", "9000")]},
        {"tag": "input", "type": "text", "role": "textbox", "label": "Amount", "text": "", "name": "amount"},
        key="Enter"),
    "plain link click": snap(f"{P}/", "click", None,
        {"tag": "a", "type": "", "role": "link", "label": "Invoices", "text": "Invoices", "name": ""}),
    "login": snap("https://sso.corp.example/login", "submit", {
        "action": "/login", "method": "post", "submit_label": "Sign in",
        "fields": [field("user", "Username", "agent"), field("pw", "Password", "********", "password")]},
        dict(BTN, label="Sign in", text="Sign in", name="")),
    "no form, no label": snap(f"{P}/x", "click", None, {"tag": "div", "type": "", "role": "div", "label": "", "text": "", "name": ""}),
}

FACT_CASES = [
    ("egress payment, no intent", {"sensor": "egress", "verb": "request", "semantic_action": "payment", "amount": 200, "ui_intent": "none", "element_label": "api"}, None, False),
    ("egress payment, matched intent", {"sensor": "egress", "verb": "request", "semantic_action": "payment", "amount": 200, "ui_intent": "matched", "element_label": "api", "url": f"{P}/api/payments"}, None, False),
    ("egress payment, mismatch", {"sensor": "egress", "verb": "request", "semantic_action": "payment", "amount": 7200, "ui_intent": "mismatch", "element_label": "api"}, None, True),
    ("low confidence semantic", {"sensor": "harness", "verb": "click", "semantic_action": "payment", "amount": 9000, "element_label": "Go"}, {"semantic_action": 0.55}, False),
    ("high confidence semantic", {"sensor": "harness", "verb": "click", "semantic_action": "payment", "amount": 9000, "element_label": "Go", "url": f"{P}/invoices/INV-9"}, {"semantic_action": 0.93, "amount": 0.9}, False),
    ("granted hold", {"sensor": "extension", "verb": "submit", "semantic_action": "payment", "amount": 6000, "element_label": "Pay"}, None, True),
    ("granted still blocks payee change", {"sensor": "extension", "verb": "submit", "semantic_action": "payee_change", "element_label": "Save"}, None, True),
    ("harness navigates off-site", {"sensor": "harness", "verb": "navigate", "host": "evil.example", "semantic_action": "navigation", "element_label": "x"}, None, False),
    ("extension navigates off-site (rule scoped to harness)", {"sensor": "extension", "verb": "navigate", "host": "evil.example", "semantic_action": "navigation", "element_label": "x"}, None, False),
    ("amount as string is unknown for gt", {"sensor": "extension", "verb": "submit", "semantic_action": "payment", "amount": "7200", "element_label": "Pay"}, None, False),
    ("literal unknown semantic", {"sensor": "extension", "verb": "submit", "semantic_action": "unknown", "url": f"{P}/admin/x", "element_label": "Go"}, None, False),
]


def summarize(out: dict) -> dict:
    return {
        "decision": out["decision"],
        "reason": out["reason"],
        "rule_ids": out["rule_ids"],
        "shadow": [f"{h['rule_id']}:{h['effect']}:{h['outcome']}" for h in out["shadow"]],
    }


def main() -> None:
    bundle = conformance_bundle()
    vectors = []
    for name, s in SNAPSHOTS.items():
        facts = facts_from_snapshot(s)
        vectors.append({"name": name, "snapshot": s, "expect_facts": facts,
                        "expect": summarize(evaluate(bundle, facts))})
    for name, facts, conf, grant in FACT_CASES:
        vectors.append({"name": name, "facts": facts, "confidence": conf, "grant": grant,
                        "expect": summarize(evaluate(bundle, facts, conf, grant))})
    out = {"bundle": bundle, "vectors": vectors}
    (ROOT / "conformance" / "vectors.json").write_text(json.dumps(out, indent=1) + "\n")
    print(f"wrote {len(vectors)} vectors")


if __name__ == "__main__":
    main()
