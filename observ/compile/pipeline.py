"""policies.txt -> reviewed, tested, signed bundle.

Per line: template (deterministic) or Claude (authoring time) -> ground in the fact
schema -> clarify vague terms -> generate tests -> lint -> include or reject.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from observ.compile import bundle as bundle_mod
from observ.compile.ground import ground
from observ.compile.intent import Intent
from observ.compile.lint import BLOCKING_FLAGS, lint
from observ.compile.templates import match_template
from observ.compile.testgen import generate
from observ.evaluator import evaluate


@dataclass
class PolicyReport:
    line: str
    rule_id: str
    source: str = ""  # "template:<name>" | "claude" | "none"
    status: str = "ok"  # ok | needs_clarification | invalid | untranslatable | rejected
    summary: str = ""
    rule: dict | None = None
    tests: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)


def read_policies(path: str | Path) -> list[str]:
    lines = []
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line.lstrip("-* ").strip())
    return lines


def read_answers(path: str | Path | None) -> dict[str, str]:
    if not path or not Path(path).exists():
        return {}
    data = yaml.safe_load(Path(path).read_text()) or {}
    return {str(k).lower(): str(v) for k, v in data.items()}


Decomposer = Callable[[str], Intent]


def default_decomposer() -> Decomposer | None:
    """Claude, when credentials are available; otherwise templates only."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("OBSERV_USE_CLAUDE")):
        return None
    from observ.compile.llm import decompose

    return decompose


def compile_policies(
    lines: list[str],
    answers: dict[str, str] | None = None,
    decomposer: Decomposer | None = None,
    mode: str = "shadow",
    min_confidence: float = 0.8,
) -> tuple[dict, list[PolicyReport]]:
    answers = answers or {}
    reports: list[PolicyReport] = []
    rules: list[dict] = []
    tests: list[dict] = []

    for i, line in enumerate(lines, start=1):
        rep = PolicyReport(line=line, rule_id=f"p{i}")
        reports.append(rep)
        matched = match_template(line)
        if matched:
            name, intent = matched
            rep.source = f"template:{name}"
        elif decomposer:
            intent = decomposer(line)
            rep.source = "claude"
        else:
            rep.source = "none"
            rep.status = "untranslatable"
            rep.errors = ["no built-in template matches; set ANTHROPIC_API_KEY to compile it with Claude"]
            continue

        rep.summary = intent.summary
        if intent.untranslatable_reason:
            rep.status = "untranslatable"
            rep.errors = [intent.untranslatable_reason]
            continue
        when, errors, questions = ground(intent, answers)
        if questions:
            rep.status, rep.questions = "needs_clarification", questions
            continue
        if errors:
            rep.status, rep.errors = "invalid", errors
            continue

        rule = {
            "id": rep.rule_id,
            "source": line,
            "effect": intent.effect,
            "when": when,
            "mode": mode,
            "on_unknown": "hold" if intent.effect != "allow" else "allow",
        }
        if intent.sensors:
            rule["sensors"] = list(intent.sensors)
        rule_tests = generate(rule)
        rep.rule, rep.tests = rule, rule_tests
        rep.flags = lint(rule, rule_tests)
        if BLOCKING_FLAGS & set(rep.flags):
            rep.status = "rejected"
            continue
        if "NO_EFFECT" in rep.flags:
            rep.status = "rejected"
            rep.errors = ["allow policies are not supported yet: everything not blocked or held is allowed"]
            continue
        rules.append(rule)
        tests += rule_tests

    bundle = {
        "version": 1,
        "schema_version": 1,
        "min_confidence": min_confidence,
        "rules": rules,
        "tests": tests,
        "policies": [
            {"line": r.line, "rule_id": r.rule_id if r.status == "ok" else None, "status": r.status}
            for r in reports
        ],
    }
    return bundle_mod.seal(bundle), reports


def run_tests(bundle: dict) -> list[tuple[dict, str]]:
    """Run each rule's tests against that rule in isolation. Returns (test, actual) failures."""
    failures = []
    rules = {r["id"]: r for r in bundle["rules"]}
    for t in bundle.get("tests", []):
        rule = rules.get(t["rule_id"])
        if rule is None:
            continue
        single = {"rules": [dict(rule, mode="enforce")], "min_confidence": bundle.get("min_confidence", 0.8)}
        actual = evaluate(single, t["facts"])["decision"]
        if actual != t["expect"]:
            failures.append((t, actual))
    return failures
