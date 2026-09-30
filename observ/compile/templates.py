"""Deterministic templates for the most common policies (the built-in policy pack).

A matched template compiles with no LLM call at all, so the common case is instant,
free and reproducible. Anything else goes to the Claude decomposer.
"""

from __future__ import annotations

import re

from observ.compile.intent import Condition, Intent, VagueTerm
from observ.schema import COMMIT_ACTIONS

APPROVAL = re.compile(r"without (?:a |an )?(?:human )?(?:approval|sign[- ]?off|review)|needs? (?:a |an )?(?:human )?approval|requires? (?:a |an )?(?:human )?approval|\bask\b|\bapproval\b|\bconfirm|\breview\b|\bsign[- ]?off\b", re.I)
VAGUE_SIZE = re.compile(r"\b(large|big|huge|significant|high[- ]value|unusual)\b", re.I)
AMOUNT = re.compile(r"(?:over|above|greater than|more than|exceeding|in excess of|>)\s*(?:\$|usd\s*)?\s*([\d,]+(?:\.\d+)?)\s*(k|m)?\b", re.I)
PAYMENT = re.compile(r"\bpay(?:ment)?s?\b|\btransfers?\b|\bwires?\b|\bremit|\binvoices?\b", re.I)
BANK_CHANGE = re.compile(r"\b(change|edit|update|modify|replace|alter)\b.*\b(bank|banking|routing|iban|payee|account details|remittance)", re.I)
EMAIL_OUTSIDE = re.compile(r"\be-?mails?\b.*\b(?:outside|external to|other than|not (?:at|in))\s+(?:of\s+)?@?([a-z0-9.-]+\.[a-z]{2,})", re.I)
DELETE = re.compile(r"\b(never|don'?t|do not|no|block)\b.*\b(delete|remove|destroy)\b", re.I)
UI_INTENT = re.compile(r"(match|come from|originate from|correspond to|backed by)\b.*\b(screen|on-screen|ui|visible|user interface)", re.I)
NEEDS_APPROVAL_SUBJECT = re.compile(r"^(?:the\s+)?(.+?)\s+(?:needs|requires)\s+(?:a\s+|an\s+)?(?:human\s+)?(?:approval|sign[- ]?off)", re.I)


def _effect(line: str) -> str:
    return "hold" if APPROVAL.search(line) else "block"


def _amount(m: re.Match) -> str:
    n = float(m.group(1).replace(",", ""))
    mult = {"k": 1_000, "m": 1_000_000}.get((m.group(2) or "").lower(), 1)
    n *= mult
    return str(int(n)) if n == int(n) else str(n)


def match_template(line: str) -> tuple[str, Intent] | None:
    """Return (template_name, intent) for a policy line, or None."""
    text = line.strip()

    if UI_INTENT.search(text) and (PAYMENT.search(text) or BANK_CHANGE.search(text) or "bank" in text.lower()):
        return "ui-intent-correlation", Intent(
            effect=_effect(text),
            semantic_actions=["payment", "payee_change"],
            conditions=[Condition(fact="ui_intent", op="in", value="none,mismatch", value_type="list")],
            vague_terms=[],
            sensors=["egress"],
            untranslatable_reason=None,
            summary="Committing payment/bank requests must match a visible, approved UI action.",
        )

    if BANK_CHANGE.search(text):
        return "payee-change", Intent(
            effect=_effect(text),
            semantic_actions=["payee_change"],
            conditions=[],
            vague_terms=[],
            sensors=[],
            untranslatable_reason=None,
            summary="Changes to payee / bank details.",
        )

    if PAYMENT.search(text):
        m = AMOUNT.search(text)
        if m:
            return "payment-threshold", Intent(
                effect=_effect(text),
                semantic_actions=["payment"],
                conditions=[Condition(fact="amount", op="gt", value=_amount(m), value_type="number")],
                vague_terms=[],
                sensors=[],
                untranslatable_reason=None,
                summary=f"Payments over {_amount(m)}.",
            )
        v = VAGUE_SIZE.search(text)
        if v:
            return "payment-threshold", Intent(
                effect=_effect(text),
                semantic_actions=["payment"],
                conditions=[],
                vague_terms=[VagueTerm(
                    term=v.group(1).lower(),
                    question=f"What payment amount counts as '{v.group(1).lower()}'? (a number, e.g. 5000)",
                    fact="amount", op="gt", value_type="number",
                )],
                sensors=[],
                untranslatable_reason=None,
                summary="Payments above a threshold to be clarified.",
            )

    m = EMAIL_OUTSIDE.search(text)
    if m:
        domain = m.group(1).lower().strip(".")
        return "email-domain", Intent(
            effect=_effect(text),
            semantic_actions=["email_send"],
            conditions=[
                Condition(fact="recipient_domain", op="neq", value=domain, value_type="string"),
                Condition(fact="recipient_domain", op="not_ends_with", value="." + domain, value_type="string"),
            ],
            vague_terms=[],
            sensors=[],
            untranslatable_reason=None,
            summary=f"Emails to recipients outside {domain}.",
        )

    if DELETE.search(text):
        return "delete", Intent(
            effect=_effect(text),
            semantic_actions=["delete"],
            conditions=[],
            vague_terms=[],
            sensors=[],
            untranslatable_reason=None,
            summary="Delete / remove actions.",
        )

    m = NEEDS_APPROVAL_SUBJECT.search(text)
    if m:
        subject = m.group(1).strip().rstrip(".")
        return "needs-approval-on", Intent(
            effect="hold",
            semantic_actions=list(COMMIT_ACTIONS),
            conditions=[],
            vague_terms=[VagueTerm(
                term=subject.lower(),
                question=f"Which URLs are '{subject}'? (glob, e.g. admin.example.com/*)",
                fact="url", op="glob", value_type="string",
            )],
            sensors=[],
            untranslatable_reason=None,
            summary=f"Committing actions on {subject} need human approval.",
        )
    return None
