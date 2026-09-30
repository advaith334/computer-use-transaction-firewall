"""Generate allow/block test actions for a compiled rule, deterministically.

For a rule `all(leaf...)` we build one fact assignment that satisfies every leaf
(expect the rule's effect), one per leaf that violates just that leaf (expect allow),
and one with a fact missing (expect the rule's on_unknown effect). The author reviews
these as plain sentences; they then live in the bundle as regression tests.
"""

from __future__ import annotations

from observ.evaluator import evaluate
from observ.schema import FACTS

_STRING_OTHER = "example.org"


def _enum_other(fact: str, excluded: list) -> str | None:
    spec = FACTS.get(fact, {})
    values = spec.get("values", [])
    if "none" in values and "none" not in excluded:
        return "none"
    for v in values:
        if v not in excluded:
            return v
    return None


def _group(leaves: list[dict]) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = {}
    for leaf in leaves:
        by.setdefault(leaf["fact"], []).append(leaf)
    return by


def _satisfy(fact: str, leaves: list[dict]):
    """A value for `fact` satisfying all its leaves, or None if we can't find one."""
    lo, hi, lo_strict, hi_strict = None, None, False, False
    eq = None
    forbidden: list = []
    for leaf in leaves:
        op, v = leaf["op"], leaf.get("value")
        if op == "gt" and (lo is None or v >= lo):
            lo, lo_strict = v, True
        elif op == "gte" and (lo is None or v > lo):
            lo, lo_strict = v, False
        elif op == "lt" and (hi is None or v <= hi):
            hi, hi_strict = v, True
        elif op == "lte" and (hi is None or v < hi):
            hi, hi_strict = v, False
        elif op == "eq":
            if eq is not None and eq != v:
                return None
            eq = v
        elif op == "in":
            eq = eq if eq is not None else v[0]
        elif op == "glob":
            eq = eq if eq is not None else v.replace("*", "x")
        elif op == "ends_with":
            eq = eq if eq is not None else "x" + v
        elif op in ("neq", "not_ends_with", "not_glob"):
            forbidden.append(leaf)
        elif op == "not_in":
            forbidden.append(leaf)
    if lo is not None or hi is not None:
        if lo is not None and hi is not None:
            val = (lo + hi) / 2
        elif lo is not None:
            val = lo + 1 if lo_strict else lo
        else:
            val = hi - 1 if hi_strict else hi
        val = int(val) if val == int(val) else val
        return val
    if eq is not None:
        return eq
    if FACTS.get(fact, {}).get("type") == "enum":
        excluded = []
        for leaf in forbidden:
            excluded += leaf["value"] if isinstance(leaf["value"], list) else [leaf["value"]]
        return _enum_other(fact, excluded)
    return _STRING_OTHER


def _violate(leaf: dict):
    op, v = leaf["op"], leaf.get("value")
    if op == "gt":
        return v
    if op == "gte":
        return v - 1
    if op == "lt":
        return v
    if op == "lte":
        return v + 1
    if op in ("eq", "in"):
        excluded = v if isinstance(v, list) else [v]
        return _enum_other(leaf["fact"], excluded + ["unknown"]) or _STRING_OTHER
    if op == "neq":
        return v
    if op == "not_in":
        return v[0]
    if op == "not_ends_with":
        return "x" + v
    if op == "not_glob":
        return v.replace("*", "x")
    if op in ("glob", "ends_with"):
        return "unrelated.invalid"
    return None


def generate(rule: dict, max_tests: int = 5) -> list[dict]:
    leaves = rule["when"].get("all", [])
    base = {"sensor": (rule.get("sensors") or ["extension"])[0], "verb": "submit"}
    single = {"rules": [dict(rule, mode="enforce")], "min_confidence": 0.8}

    positive = dict(base)
    for fact, fl in _group(leaves).items():
        val = _satisfy(fact, fl)
        if val is None:
            return []
        positive[fact] = val

    tests = [{"rule_id": rule["id"], "name": "matches", "facts": positive, "expect": rule["effect"]}]
    for leaf in leaves:
        if len(tests) >= max_tests - 1:
            break
        neg = dict(positive)
        neg[leaf["fact"]] = _violate(leaf)
        if evaluate(single, neg)["decision"] == "allow":
            tests.append({
                "rule_id": rule["id"],
                "name": f"{leaf['fact']} {leaf['op']} not satisfied",
                "facts": neg,
                "expect": "allow",
            })
    # Uncertainty: drop the most specific fact (the last leaf) and expect on_unknown.
    droppable = [leaf for leaf in leaves if leaf["op"] != "exists"]
    if droppable:
        miss = dict(positive)
        miss.pop(droppable[-1]["fact"], None)
        on_unknown = rule.get("on_unknown", "hold")
        tests.append({
            "rule_id": rule["id"],
            "name": f"{droppable[-1]['fact']} unknown",
            "facts": miss,
            "expect": on_unknown if on_unknown != "allow" else "allow",
        })
    return tests[:max_tests]


def describe(test: dict) -> str:
    f = test["facts"]
    parts = [f"{k}={v}" for k, v in f.items() if k not in ("sensor", "verb")]
    return f"{test['expect'].upper():5}  {f.get('verb')} via {f.get('sensor')}: " + (", ".join(parts) or "(any)")
