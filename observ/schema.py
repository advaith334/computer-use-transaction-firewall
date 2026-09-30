"""The fact schema: every typed fact a sensor can produce about a proposed action.

Policies compile to conditions over these facts only, so a rule means the same thing
whichever sensor (browser extension, harness hook, egress proxy) evaluates it.
"""

from __future__ import annotations

SEMANTIC_ACTIONS = [
    "payment",
    "payee_change",
    "email_send",
    "delete",
    "login",
    "navigation",
    "none",
    "unknown",
]

# Actions that commit something irreversible. Only these ever reach the decision tier.
COMMIT_ACTIONS = ["payment", "payee_change", "email_send", "delete", "unknown"]

FACTS: dict[str, dict] = {
    "sensor": {
        "type": "enum",
        "values": ["extension", "harness", "egress", "cdp", "desktop", "platform"],
        "doc": "Which sensor observed the action.",
    },
    "verb": {
        "type": "enum",
        "values": ["click", "type", "key", "submit", "navigate", "request"],
        "doc": "The raw interaction.",
    },
    "semantic_action": {
        "type": "enum",
        "values": SEMANTIC_ACTIONS,
        "doc": "What the action does in business terms.",
    },
    "amount": {"type": "number", "doc": "Monetary amount the action commits, if any."},
    "currency": {"type": "string", "doc": "ISO currency code of amount."},
    "payee": {"type": "string", "doc": "Payee / vendor / beneficiary name."},
    "recipient_domain": {"type": "string", "doc": "Domain of an email recipient."},
    "url": {"type": "string", "doc": "Full URL where the action happens."},
    "host": {"type": "string", "doc": "Host (and port) of the URL."},
    "path": {"type": "string", "doc": "URL path."},
    "element_label": {"type": "string", "doc": "Accessible label of the target element."},
    "element_role": {"type": "string", "doc": "Accessible role of the target element."},
    "ui_intent": {
        "type": "enum",
        "values": ["matched", "none", "mismatch"],
        "doc": "Egress only: did a visible UI action precede this committing request?",
    },
}

OPS = {
    "eq": "equals",
    "neq": "not equal",
    "gt": "greater than",
    "gte": "greater than or equal",
    "lt": "less than",
    "lte": "less than or equal",
    "in": "one of",
    "not_in": "none of",
    "glob": "matches glob pattern (* wildcard, case-insensitive)",
    "not_glob": "does not match glob pattern",
    "ends_with": "ends with (case-insensitive)",
    "not_ends_with": "does not end with (case-insensitive)",
    "exists": "fact is known (value is true/false)",
}

NUMERIC_OPS = {"gt", "gte", "lt", "lte"}
LIST_OPS = {"in", "not_in"}

EFFECTS = ["block", "hold", "allow"]


def export() -> dict:
    return {
        "version": 1,
        "facts": FACTS,
        "ops": OPS,
        "semantic_actions": SEMANTIC_ACTIONS,
        "commit_actions": COMMIT_ACTIONS,
        "effects": EFFECTS,
    }
