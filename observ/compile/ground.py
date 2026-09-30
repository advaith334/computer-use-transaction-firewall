"""Ground a structured intent in the fact schema, producing an evaluator rule."""

from __future__ import annotations

from observ.compile.intent import Condition, Intent
from observ.schema import FACTS, LIST_OPS, NUMERIC_OPS, OPS


def _parse_value(value: str, value_type: str, op: str) -> tuple[object, str | None]:
    if op in LIST_OPS or value_type == "list":
        return [v.strip() for v in value.split(",") if v.strip()], None
    if op in NUMERIC_OPS or value_type == "number":
        cleaned = value.replace(",", "").replace("$", "").strip()
        try:
            x = float(cleaned)
        except ValueError:
            return None, f"'{value}' is not a number"
        return (int(x) if x == int(x) else x), None
    return value.strip(), None


def ground_condition(c: Condition) -> tuple[dict | None, list[str]]:
    errors = []
    if c.fact not in FACTS:
        errors.append(f"unknown fact '{c.fact}'")
    if c.op not in OPS:
        errors.append(f"unknown operator '{c.op}'")
    if errors:
        return None, errors
    value, err = _parse_value(c.value, c.value_type, c.op)
    if err:
        return None, [err]
    spec = FACTS[c.fact]
    if spec["type"] == "enum":
        vals = value if isinstance(value, list) else [value]
        bad = [v for v in vals if v not in spec["values"]]
        if bad:
            return None, [f"{c.fact} has no value {bad} (allowed: {spec['values']})"]
    if c.op in NUMERIC_OPS and spec["type"] != "number":
        return None, [f"'{c.op}' needs a numeric fact, '{c.fact}' is {spec['type']}"]
    if c.fact == "url" and c.op in ("glob", "not_glob") and "://" not in value and not value.startswith("*"):
        value = "*://" + value
    return {"fact": c.fact, "op": c.op, "value": value}, []


def ground(intent: Intent, answers: dict[str, str]) -> tuple[dict | None, list[str], list[dict]]:
    """Return (when-condition, errors, open_questions)."""
    leaves: list[dict] = []
    errors: list[str] = []
    questions: list[dict] = []

    acts = list(dict.fromkeys(intent.semantic_actions))
    if len(acts) == 1:
        leaves.append({"fact": "semantic_action", "op": "eq", "value": acts[0]})
    elif acts:
        leaves.append({"fact": "semantic_action", "op": "in", "value": acts})

    for c in intent.conditions:
        leaf, errs = ground_condition(c)
        errors += errs
        if leaf:
            leaves.append(leaf)

    for v in intent.vague_terms:
        answer = answers.get(v.term.lower())
        if answer is None:
            questions.append({"term": v.term, "question": v.question})
            continue
        leaf, errs = ground_condition(Condition(fact=v.fact, op=v.op, value=answer, value_type=v.value_type))
        errors += [f"answer for '{v.term}': {e}" for e in errs]
        if leaf:
            leaves.append(leaf)

    if errors or questions:
        return None, errors, questions
    return {"all": leaves}, [], []
