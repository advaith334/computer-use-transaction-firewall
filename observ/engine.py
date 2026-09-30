"""The decision engine behind every sensor.

decide(): facts (from a DOM snapshot or supplied by the sensor) -> decision tier for
facts a rule needs but the sensor couldn't derive -> egress UI-intent correlation ->
human-approval grants -> deterministic evaluation -> hold bookkeeping -> audit.

Everything stateful (holds, grants, intents) is resolved into plain facts / flags
*before* evaluation and recorded, so replaying the audit log is exact.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import dataclass, field

from observ.audit import AuditLog
from observ.decide.backend import QUESTIONS, DecisionBackend, DecisionState
from observ.evaluator import evaluate
from observ.facts import facts_from_snapshot
from observ.schema import COMMIT_ACTIONS

GRANT_TTL = 120.0
INTENT_TTL = 120.0
INTENT_DEDUPE = 5.0
UI_SENSORS = {"extension", "harness", "cdp", "desktop"}


def fingerprint(facts: dict) -> str:
    keys = ("semantic_action", "amount", "payee", "recipient_domain", "host")
    return json.dumps({k: facts.get(k) for k in keys}, sort_keys=True)


def referenced_facts(bundle: dict) -> set[str]:
    out: set[str] = set()

    def walk(c: dict) -> None:
        for key in ("all", "any"):
            for sub in c.get(key, []):
                walk(sub)
        if "not" in c:
            walk(c["not"])
        if "fact" in c:
            out.add(c["fact"])

    for r in bundle["rules"]:
        walk(r["when"])
    return out


def _state_text(facts: dict, snapshot: dict | None) -> str:
    parts = [f"{k}: {v}" for k, v in facts.items() if k not in ("sensor",)]
    if snapshot:
        form = snapshot.get("form") or {}
        el = snapshot.get("element") or {}
        parts.append(f"element: {el.get('role')} '{el.get('label') or el.get('text')}'")
        if form:
            parts.append(f"form action: {form.get('action')} submit: '{form.get('submit_label')}'")
            for f in form.get("fields", []):
                parts.append(f"field {f.get('name')} ({f.get('label')}): {f.get('value')}")
    return "\n".join(parts)


@dataclass
class Hold:
    id: str
    created: float
    facts: dict
    reason: str
    session: str
    status: str = "pending"  # pending | approved | denied
    decided_by: str | None = None


@dataclass
class Intent:
    ts: float
    facts: dict
    session: str
    consumed: bool = False


@dataclass
class Engine:
    bundle: dict
    audit: AuditLog
    backend: DecisionBackend | None = None
    holds: dict[str, Hold] = field(default_factory=dict)
    grants: dict[str, float] = field(default_factory=dict)  # fingerprint -> expiry
    intents: list[Intent] = field(default_factory=list)
    clock: callable = time.time

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self.audit.store_bundle(self.bundle)
        self._needed = referenced_facts(self.bundle)

    def set_bundle(self, bundle: dict) -> None:
        with self._lock:
            self.bundle = bundle
            self.audit.store_bundle(bundle)
            self._needed = referenced_facts(bundle)

    # -- decision tier ---------------------------------------------------------------
    def _enrich(self, facts: dict, snapshot: dict | None, image: bytes | None) -> tuple[dict, dict, dict | None]:
        confidence: dict = {}
        if self.backend is None:
            return facts, confidence, None
        sem = facts.get("semantic_action")
        committing = sem in COMMIT_ACTIONS or sem is None
        if not committing:
            return facts, confidence, None
        questions = []
        if "semantic_action" in self._needed and sem in (None, "unknown"):
            questions.append(QUESTIONS["semantic_action"])
        if "amount" in self._needed and facts.get("amount") is None and sem in (None, "unknown", "payment"):
            questions.append(QUESTIONS["amount"])
        if not questions:
            return facts, confidence, None
        t0 = time.perf_counter()
        answers = self.backend.ask(DecisionState(text=_state_text(facts, snapshot), image_png=image), questions)
        facts = dict(facts)
        for qid, ans in answers.items():
            facts[qid] = ans.value
            confidence[qid] = round(float(ans.confidence), 3)
        meta = {
            "backend": self.backend.name,
            "questions": [q.id for q in questions],
            "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        }
        return facts, confidence, meta

    # -- egress correlation ----------------------------------------------------------
    def _correlate(self, facts: dict, session: str) -> str:
        now = self.clock()
        self.intents = [i for i in self.intents if now - i.ts < INTENT_TTL and not i.consumed]
        for intent in reversed(self.intents):
            if intent.session != session or intent.facts.get("semantic_action") != facts.get("semantic_action"):
                continue
            for key in ("amount", "payee"):
                a, b = intent.facts.get(key), facts.get(key)
                if a is not None and b is not None and str(a).strip().lower() != str(b).strip().lower():
                    return "mismatch"
            intent.consumed = True
            return "matched"
        return "none"

    def _record_intent(self, facts: dict, session: str) -> None:
        # Two sensors vouching for the same click (harness + extension) is one intent, not two:
        # a spare intent could otherwise vouch for a later look-alike request.
        fp, now = fingerprint(facts), self.clock()
        for i in self.intents:
            if not i.consumed and i.session == session and fingerprint(i.facts) == fp and now - i.ts < INTENT_DEDUPE:
                return
        self.intents.append(Intent(ts=now, facts=dict(facts), session=session))

    # -- main entry ------------------------------------------------------------------
    def decide(
        self,
        facts: dict | None = None,
        snapshot: dict | None = None,
        sensor: str = "extension",
        session: str = "default",
        image: bytes | None = None,
    ) -> dict:
        t0 = time.perf_counter()
        with self._lock:
            if facts is None:
                facts = facts_from_snapshot(snapshot or {}, sensor=sensor)
            facts = {**facts, "sensor": facts.get("sensor", sensor)}
            facts, confidence, tier = self._enrich(facts, snapshot, image)
            if facts["sensor"] == "egress" and facts.get("semantic_action") in COMMIT_ACTIONS:
                facts["ui_intent"] = self._correlate(facts, session)

            # A human approval is single-use. An egress request that matches a visible UI
            # action inherits that action's outcome (it was allowed or approved upstream),
            # so hold rules are satisfied there too; block rules still apply.
            fp = fingerprint(facts)
            ui_grant = self.grants.get(fp, 0) > self.clock() and facts["sensor"] in UI_SENSORS
            if ui_grant:
                del self.grants[fp]
            grant = ui_grant or facts.get("ui_intent") == "matched"
            out = evaluate(self.bundle, facts, confidence, grant)

            hold_id = None
            if out["decision"] == "hold":
                hold_id = "h_" + secrets.token_hex(4)
                self.holds[hold_id] = Hold(hold_id, self.clock(), facts, out["reason"], session)
            if (
                out["decision"] == "allow"
                and not ui_grant  # the approval already recorded this intent
                and facts["sensor"] in UI_SENSORS
                and facts.get("semantic_action") in COMMIT_ACTIONS
            ):
                self._record_intent(facts, session)

            latency = round((time.perf_counter() - t0) * 1000, 3)
            rec = self.audit.append({
                "kind": "decision",
                "session": session,
                "sensor": facts["sensor"],
                "facts": facts,
                "confidence": confidence,
                "grant": grant,
                "decision": out["decision"],
                "reason": out["reason"],
                "rule_ids": out["rule_ids"],
                "shadow": [h["rule_id"] + ":" + h["effect"] for h in out["shadow"]],
                "bundle_hash": self.bundle["hash"],
                "hold_id": hold_id,
                "decision_tier": tier,
                "latency_ms": latency,
            })
            return {**out, "hold_id": hold_id, "facts": facts, "confidence": confidence,
                    "audit_seq": rec["seq"], "latency_ms": latency, "decision_tier": tier}

    # -- holds -----------------------------------------------------------------------
    def resolve_hold(self, hold_id: str, approve: bool, by: str = "cli") -> Hold:
        with self._lock:
            hold = self.holds[hold_id]
            if hold.status != "pending":
                return hold
            hold.status = "approved" if approve else "denied"
            hold.decided_by = by
            if approve:
                self.grants[fingerprint(hold.facts)] = self.clock() + GRANT_TTL
                if hold.facts.get("sensor") in UI_SENSORS:
                    self._record_intent(hold.facts, hold.session)
            self.audit.append({
                "kind": "hold_" + hold.status,
                "hold_id": hold_id,
                "by": by,
                "facts": hold.facts,
                "bundle_hash": self.bundle["hash"],
            })
            return hold

    def consume_grant(self, hold_id: str) -> bool:
        """Called by a sensor that executes an approved action directly (instead of
        re-deciding), so the approval can't be reused by a later look-alike action."""
        with self._lock:
            hold = self.holds.get(hold_id)
            if hold is None or hold.status != "approved":
                return False
            return self.grants.pop(fingerprint(hold.facts), None) is not None
