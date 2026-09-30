"""Static checks on a compiled rule, in the spirit of AgentCore's policy findings.

MATCHES_EVERYTHING  the rule has no conditions, so it blocks/holds every action
UNREACHABLE         its conditions contradict each other, so it can never fire
NO_EFFECT           an allow rule; the default is already allow
UNTESTABLE          no satisfying test action could be generated
"""

from __future__ import annotations


def lint(rule: dict, tests: list[dict]) -> list[str]:
    flags = []
    leaves = rule["when"].get("all", [])
    if not leaves:
        flags.append("MATCHES_EVERYTHING")
    if rule["effect"] == "allow":
        flags.append("NO_EFFECT")

    lo, hi = {}, {}
    eqs: dict[str, set] = {}
    for leaf in leaves:
        f, op, v = leaf["fact"], leaf["op"], leaf.get("value")
        if op in ("gt", "gte"):
            lo[f] = max(lo.get(f, v), v)
        elif op in ("lt", "lte"):
            hi[f] = min(hi.get(f, v), v)
        elif op == "eq":
            eqs.setdefault(f, set()).add(str(v).lower())
    if any(f in hi and lo[f] >= hi[f] for f in lo) or any(len(s) > 1 for s in eqs.values()):
        flags.append("UNREACHABLE")
    elif leaves and not tests:
        flags.append("UNTESTABLE")
    return flags


BLOCKING_FLAGS = {"MATCHES_EVERYTHING", "UNREACHABLE"}
