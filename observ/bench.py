"""`observ bench`: measure what the firewall is worth on labelled scenarios.

Configurations:
  none           no firewall (every action executes)
  rules          deterministic facts + compiled rules; unclassifiable commits are held
  rules+local    + local keyword decision tier (nothing leaves the machine)
  rules+<model>  + a hosted decision tier (e.g. anthropic), when credentials exist

Metrics: violations stopped (recall), false stops on legitimate actions, human holds
(review burden), decision-tier calls, latency p50/p99, and time-to-first-block from a
plain policies.txt.
"""

from __future__ import annotations

import statistics
import tempfile
import time
from pathlib import Path

from bench.scenarios import SCENARIOS
from observ.audit import AuditLog
from observ.compile.pipeline import compile_policies, read_answers, read_policies
from observ.decide.backend import DecisionBackend
from observ.engine import Engine


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))]


def run_config(bundle: dict, backend: DecisionBackend | None, firewall: bool = True) -> dict:
    engine = Engine(bundle=bundle, audit=AuditLog(tempfile.mkdtemp(prefix="observ-bench-")), backend=backend)
    rows, latencies, tier_calls = [], [], 0
    for sc in SCENARIOS:
        if not firewall:
            rows.append({**sc, "decision": "allow"})
            continue
        t0 = time.perf_counter()
        out = engine.decide(snapshot=sc["snapshot"], sensor="extension")
        latencies.append((time.perf_counter() - t0) * 1000)
        tier_calls += 1 if out.get("decision_tier") else 0
        rows.append({**sc, "decision": out["decision"], "reason": out["reason"]})
    violations = [r for r in rows if r["stop"]]
    legit = [r for r in rows if not r["stop"]]
    return {
        "rows": rows,
        "recall": sum(r["decision"] != "allow" for r in violations) / len(violations),
        "false_stop": sum(r["decision"] != "allow" for r in legit) / len(legit),
        "holds": sum(r["decision"] == "hold" for r in rows),
        "tier_calls": tier_calls,
        "p50": _pct(latencies, 0.5),
        "p99": _pct(latencies, 0.99),
    }


def time_to_first_block(policies: Path, answers: Path) -> float:
    """Seconds from reading a plain policies.txt to the first enforced block."""
    t0 = time.perf_counter()
    bundle, _ = compile_policies(read_policies(policies), read_answers(answers), mode="enforce")
    engine = Engine(bundle=bundle, audit=AuditLog(tempfile.mkdtemp(prefix="observ-ttfb-")))
    injected = next(s for s in SCENARIOS if "injected" in s["name"])
    out = engine.decide(snapshot=injected["snapshot"])
    assert out["decision"] == "block", out
    return time.perf_counter() - t0


def report(policies: Path, answers: Path, extra_backends: dict[str, DecisionBackend]) -> str:
    bundle, _ = compile_policies(read_policies(policies), read_answers(answers), mode="enforce")
    from observ.decide.local import LocalKeywordBackend

    configs = {"none": run_config(bundle, None, firewall=False), "rules": run_config(bundle, None),
               "rules+local": run_config(bundle, LocalKeywordBackend())}
    for name, backend in extra_backends.items():
        configs[f"rules+{name}"] = run_config(bundle, backend)

    n_v = sum(s["stop"] for s in SCENARIOS)
    lines = [
        "# Observ bench",
        "",
        f"{len(SCENARIOS)} scenarios ({n_v} should be stopped, {len(SCENARIOS) - n_v} legitimate), "
        f"policies from `{policies.name}`.",
        "",
        "> Caveat: these scenarios and the local keyword classifier were written together for this proof of concept, "
        "so treat the numbers as a regression harness, not an independent benchmark. Run with `--with-backend anthropic` "
        "to measure a hosted decision tier, and replace the scenarios with recorded traffic from real agents.",
        "",
        "| config | violations stopped | false stops | human holds | decision-tier calls | p50 ms | p99 ms |",
        "|---|---|---|---|---|---|---|",
    ]
    for name, c in configs.items():
        lines.append(
            f"| {name} | {c['recall']:.0%} | {c['false_stop']:.0%} | {c['holds']} | {c['tier_calls']} "
            f"| {c['p50']:.2f} | {c['p99']:.2f} |"
        )
    lines += ["", f"Time to first block from a plain policies.txt: **{time_to_first_block(policies, answers) * 1000:.0f} ms** "
              "(compile + load + first decision, no LLM: all policies matched built-in templates).", "",
              "## Per-scenario decisions", "",
              "| scenario | should stop | " + " | ".join(configs) + " |",
              "|---|---|" + "---|" * len(configs)]
    for i, sc in enumerate(SCENARIOS):
        cells = [configs[k]["rows"][i]["decision"] for k in configs]
        lines.append(f"| {sc['name']} | {'yes' if sc['stop'] else 'no'} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
