"""Local keyword classifier: the self-hosted / data-residency backend.

Deliberately simple and transparent. It scores the action's text context against
keyword sets and reports a confidence that reflects how unambiguous the evidence was.
Nothing leaves the machine.
"""

from __future__ import annotations

import re

from observ.decide.backend import Answer, DecisionState, Question

KEYWORDS: dict[str, list[tuple[str, float]]] = {
    "payee_change": [(r"routing|iban|swift|account number|bank details|remittance details", 0.95),
                     (r"\bbank\b|beneficiary", 0.7)],
    "payment": [(r"\bwire\b|remit|disburse|\bpay\b|payment|settle invoice|release[- ]funds", 0.9),
                (r"invoice|\$\s?\d|\busd\b|transfer", 0.7)],
    "email_send": [(r"[a-z0-9._-]+@[a-z0-9-]+\.[a-z]", 0.9),
                   (r"\bsend\b.*\b(mail|message)\b|compose", 0.7)],
    "delete": [(r"\bdelete\b|\bremove\b|\bdestroy\b|\bpurge\b|\bterminate\b", 0.9),
               (r"\brevoke\b|\bdeactivate\b|\brotate\b", 0.75)],
    "login": [(r"password|sign in|log in", 0.8)],
}
MONEY = re.compile(r"(?:\$|usd\s?)\s?([\d,]+(?:\.\d+)?)", re.I)


class LocalKeywordBackend:
    name = "local"

    def ask(self, state: DecisionState, questions: list[Question]) -> dict[str, Answer]:
        text = state.text.lower()
        out: dict[str, Answer] = {}
        for q in questions:
            if q.id == "semantic_action":
                scores = {}
                for action, pats in KEYWORDS.items():
                    best = max((conf for pat, conf in pats if re.search(pat, text)), default=0.0)
                    if best:
                        scores[action] = best
                if not scores:
                    # No money, bank, email, delete or login evidence anywhere in the context.
                    out[q.id] = Answer("none", 0.8)
                else:
                    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
                    top, conf = ranked[0]
                    if len(ranked) > 1 and ranked[1][1] >= conf - 0.05:
                        conf = round(conf - 0.25, 2)  # two competing readings: be unsure
                    out[q.id] = Answer(top, conf)
            elif q.id == "amount":
                amounts = [float(m.replace(",", "")) for m in MONEY.findall(state.text)]
                if len(amounts) == 1:
                    x = amounts[0]
                    out[q.id] = Answer(int(x) if x == int(x) else x, 0.85)
                elif amounts:
                    out[q.id] = Answer(max(amounts), 0.5)  # several figures on screen
        return out
