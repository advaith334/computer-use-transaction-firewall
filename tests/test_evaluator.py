from observ.evaluator import evaluate, glob_to_regex


def rule(id, effect, when, **kw):
    return {"id": id, "source": f"rule {id}", "effect": effect, "when": when, **kw}


def leaf(fact, op, value=None):
    return {"fact": fact, "op": op, "value": value}


B = {
    "min_confidence": 0.8,
    "rules": [
        rule("r1", "hold", {"all": [leaf("semantic_action", "eq", "payment"), leaf("amount", "gt", 5000)]}),
        rule("r2", "block", {"all": [leaf("semantic_action", "eq", "payee_change")]}),
        rule("r3", "block", {"all": [leaf("verb", "eq", "navigate")]}, sensors=["harness"], on_unknown="allow"),
        rule("r4", "block", {"all": [leaf("semantic_action", "eq", "delete")]}, mode="shadow"),
    ],
}


def test_match_and_no_match():
    assert evaluate(B, {"semantic_action": "payment", "amount": 7200})["decision"] == "hold"
    assert evaluate(B, {"semantic_action": "payment", "amount": 200})["decision"] == "allow"


def test_block_beats_hold():
    b = {"rules": B["rules"] + [rule("r5", "block", {"all": [leaf("amount", "gt", 7000)]})]}
    out = evaluate(b, {"semantic_action": "payment", "amount": 7200})
    assert out["decision"] == "block" and out["rule_ids"] == ["r5"]


def test_missing_fact_is_unknown_and_holds():
    out = evaluate(B, {"semantic_action": "payment"})
    assert out["decision"] == "hold"
    assert "could not determine: amount" in out["reason"]


def test_literal_unknown_counts_as_missing():
    assert evaluate(B, {"semantic_action": "unknown"})["decision"] == "hold"


def test_low_confidence_fact_is_unknown():
    facts = {"semantic_action": "payee_change"}
    assert evaluate(B, facts, {"semantic_action": 0.95})["decision"] == "block"
    assert evaluate(B, facts, {"semantic_action": 0.5})["decision"] == "hold"


def test_false_short_circuits_unknown():
    # semantic_action is known and different, so the missing amount doesn't matter.
    assert evaluate(B, {"semantic_action": "email_send"})["decision"] == "allow"


def test_grant_satisfies_holds_but_not_blocks():
    assert evaluate(B, {"semantic_action": "payment", "amount": 9000}, grant=True)["decision"] == "allow"
    assert evaluate(B, {"semantic_action": "payee_change"}, grant=True)["decision"] == "block"


def test_sensor_scoping_and_shadow():
    assert evaluate(B, {"sensor": "harness", "verb": "navigate", "semantic_action": "navigation"})["decision"] == "block"
    assert evaluate(B, {"sensor": "extension", "verb": "navigate", "semantic_action": "navigation"})["decision"] == "allow"
    out = evaluate(B, {"semantic_action": "delete"})
    assert out["decision"] == "allow" and out["shadow"][0]["rule_id"] == "r4"


def test_any_not_exists_and_glob():
    b = {"rules": [rule("g", "block", {"any": [
        leaf("url", "glob", "*://admin.*/*"),
        {"not": leaf("element_label", "exists")},
    ]})]}
    assert evaluate(b, {"url": "https://admin.acme.com/x", "element_label": "a"})["decision"] == "block"
    assert evaluate(b, {"url": "https://www.acme.com/x", "element_label": "a"})["decision"] == "allow"
    assert evaluate(b, {"url": "https://www.acme.com/x"})["decision"] == "block"
    assert glob_to_regex("a.b*(c)") == r"a\.b.*\(c\)"


def test_string_ops_on_numbers_are_unknown():
    b = {"rules": [rule("s", "block", {"all": [leaf("amount", "ends_with", "00")]})]}
    assert evaluate(b, {"amount": 7200})["decision"] == "hold"


def test_deterministic():
    facts = {"semantic_action": "payment", "amount": 7200, "sensor": "extension"}
    assert all(evaluate(B, facts) == evaluate(B, facts) for _ in range(50))
