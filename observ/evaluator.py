"""Deterministic policy evaluator (reference implementation).

The TypeScript port in extension/src/evaluator.ts must return byte-identical results;
conformance/vectors.json is run against both. Keep this file free of I/O, clocks and
randomness: same bundle + same facts => same decision, always.

Rule shape:
    {"id", "source", "effect": block|hold|allow, "when": <condition>,
     "mode": enforce|shadow, "on_unknown": block|hold|allow, "sensors": [..] | absent}
Condition shape:
    {"all": [...]} | {"any": [...]} | {"not": cond} | {"fact", "op", "value"}

Three-valued logic: a condition on a fact that is missing (or below the bundle's
min_confidence) is UNKNOWN, and a rule that evaluates to UNKNOWN applies its
on_unknown effect. That is how "uncertain" becomes "hold" instead of "allow".
"""

from __future__ import annotations

import re
from typing import Any

SEVERITY = {"allow": 0, "hold": 1, "block": 2}
_GLOB_SPECIAL = set("\\^$.|?+()[]{}")


def glob_to_regex(pattern: str) -> str:
    out = []
    for ch in pattern:
        if ch == "*":
            out.append(".*")
        elif ch in _GLOB_SPECIAL:
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def _norm(v: Any) -> Any:
    return v.lower() if isinstance(v, str) else v


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _leaf(cond: dict, facts: dict, unknown: set[str]) -> bool | None:
    name, op, value = cond["fact"], cond["op"], cond.get("value")
    fact = facts.get(name)
    if op == "exists":
        return fact is not None
    if fact is None:
        unknown.add(name)
        return None
    if op == "eq":
        return _norm(fact) == _norm(value)
    if op == "neq":
        return _norm(fact) != _norm(value)
    if op in ("in", "not_in"):
        hit = _norm(fact) in [_norm(v) for v in value]
        return hit if op == "in" else not hit
    if op in ("gt", "gte", "lt", "lte"):
        if not (_is_number(fact) and _is_number(value)):
            unknown.add(name)
            return None
        return {
            "gt": fact > value,
            "gte": fact >= value,
            "lt": fact < value,
            "lte": fact <= value,
        }[op]
    if op in ("glob", "not_glob", "ends_with", "not_ends_with"):
        # String ops on non-strings are unknown rather than stringified, so the Python and
        # TypeScript evaluators can't disagree on number formatting.
        if not isinstance(fact, str):
            unknown.add(name)
            return None
        if op in ("glob", "not_glob"):
            hit = re.fullmatch(glob_to_regex(str(value)), fact, re.IGNORECASE) is not None
            return hit if op == "glob" else not hit
        hit = fact.lower().endswith(str(value).lower())
        return hit if op == "ends_with" else not hit
    raise ValueError(f"unknown op {op!r}")


def eval_condition(cond: dict, facts: dict, unknown: set[str]) -> bool | None:
    if "all" in cond:
        results = [eval_condition(c, facts, unknown) for c in cond["all"]]
        if any(r is False for r in results):
            return False
        return None if any(r is None for r in results) else True
    if "any" in cond:
        results = [eval_condition(c, facts, unknown) for c in cond["any"]]
        if any(r is True for r in results):
            return True
        return None if any(r is None for r in results) else False
    if "not" in cond:
        r = eval_condition(cond["not"], facts, unknown)
        return None if r is None else not r
    return _leaf(cond, facts, unknown)


def effective_facts(facts: dict, confidence: dict | None, min_confidence: float) -> dict:
    """Facts that count as known: not null, not the literal "unknown", and at or above
    the bundle's confidence threshold. Everything else makes dependent conditions UNKNOWN."""
    confidence = confidence or {}
    return {
        k: v
        for k, v in facts.items()
        if v is not None and v != "unknown" and confidence.get(k, 1.0) >= min_confidence
    }


def evaluate(
    bundle: dict,
    facts: dict,
    confidence: dict | None = None,
    grant: bool = False,
) -> dict:
    """Decide allow | hold | block for one action.

    grant=True means a human already approved this exact action, so hold effects
    (including uncertainty holds) are satisfied. Block rules still apply.
    """
    min_conf = bundle.get("min_confidence", 0.8)
    eff = effective_facts(facts, confidence, min_conf)
    sensor = eff.get("sensor")

    hits: list[dict] = []
    shadow: list[dict] = []
    for rule in bundle["rules"]:
        sensors = rule.get("sensors")
        if sensors and sensor not in sensors:
            continue
        unknown: set[str] = set()
        result = eval_condition(rule["when"], eff, unknown)
        if result is False:
            continue
        if result is True:
            effect, outcome = rule["effect"], "match"
        else:
            effect, outcome = rule.get("on_unknown", "hold"), "unknown"
        if effect == "allow":
            continue
        if grant and effect == "hold":
            effect = "allow"
        hit = {
            "rule_id": rule["id"],
            "effect": effect,
            "outcome": outcome,
            "unknown_facts": sorted(unknown) if outcome == "unknown" else [],
        }
        if rule.get("mode", "enforce") == "shadow":
            shadow.append(hit)
        elif effect != "allow":
            hits.append(hit)

    decision = "allow"
    for h in hits:
        if SEVERITY[h["effect"]] > SEVERITY[decision]:
            decision = h["effect"]

    rules_by_id = {r["id"]: r for r in bundle["rules"]}
    reason = "no policy matched"
    if decision == "allow" and grant:
        reason = "approved by a human"
    for h in hits:
        if h["effect"] == decision:
            reason = f"{h['rule_id']}: {rules_by_id[h['rule_id']]['source']}"
            if h["outcome"] == "unknown":
                reason += " (could not determine: " + ", ".join(h["unknown_facts"]) + ")"
            break

    return {
        "decision": decision,
        "reason": reason,
        "rule_ids": [h["rule_id"] for h in hits if h["effect"] == decision],
        "hits": hits,
        "shadow": shadow,
        "bundle_hash": bundle.get("hash"),
    }
