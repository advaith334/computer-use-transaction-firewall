import json
from types import SimpleNamespace

import pytest

from observ.compile import bundle as bundle_mod
from observ.compile.intent import Condition, Intent, VagueTerm
from observ.compile.lint import lint
from observ.compile.llm import decompose
from observ.compile.pipeline import compile_policies, run_tests
from observ.compile.templates import match_template


def test_templates_cover_the_policy_pack():
    cases = {
        "Never submit a payment over $5,000 without human approval": ("payment-threshold", "hold"),
        "Never wire money over $10k": ("payment-threshold", "block"),
        "Never change a vendor's bank details": ("payee-change", "block"),
        "Never email anyone outside acme.com": ("email-domain", "block"),
        "Don't delete anything": ("delete", "block"),
        "The admin console needs human approval": ("needs-approval-on", "hold"),
        "Block any payment that doesn't match an on-screen action": ("ui-intent-correlation", "block"),
    }
    for line, (name, effect) in cases.items():
        got = match_template(line)
        assert got is not None, line
        assert (got[0], got[1].effect) == (name, effect), line
    assert match_template("Be careful with anything sensitive") is None


def test_amount_suffixes():
    _, intent = match_template("Never wire money over $10k")
    assert intent.conditions[0].value == "10000"


def test_sample_policies_compile_and_generated_tests_pass(bundle):
    assert [r["id"] for r in bundle["rules"]] == ["p1", "p2", "p3", "p4", "p5"]
    assert bundle["tests"] and run_tests(bundle) == []
    assert bundle_mod.verify(bundle)


def test_vague_terms_need_clarification_then_compile():
    lines = ["Hold large payments for review"]
    _, reports = compile_policies(lines)
    assert reports[0].status == "needs_clarification"
    assert "large" in reports[0].questions[0]["question"]
    b, reports = compile_policies(lines, {"large": "2500"})
    assert reports[0].status == "ok"
    assert {"fact": "amount", "op": "gt", "value": 2500} in b["rules"][0]["when"]["all"]


def test_bad_clarification_answer_is_invalid():
    _, reports = compile_policies(["Hold large payments for review"], {"large": "a lot"})
    assert reports[0].status == "invalid"


def test_untranslatable_without_llm():
    b, reports = compile_policies(["Be careful with anything sensitive"])
    assert reports[0].status == "untranslatable" and b["rules"] == []


def test_lint_flags():
    everything = {"id": "a", "source": "", "effect": "block", "when": {"all": []}}
    assert "MATCHES_EVERYTHING" in lint(everything, [])
    contradiction = {"id": "b", "source": "", "effect": "block", "when": {"all": [
        {"fact": "amount", "op": "gt", "value": 100}, {"fact": "amount", "op": "lt", "value": 50}]}}
    assert "UNREACHABLE" in lint(contradiction, [])


def test_llm_intents_are_grounded_and_rejected_when_invalid():
    good = Intent(effect="block", semantic_actions=["delete"], vague_terms=[], sensors=[],
                  untranslatable_reason=None, summary="no deletes on prod",
                  conditions=[Condition(fact="url", op="glob", value="prod.acme.com/*", value_type="string")])
    bad = Intent(effect="block", semantic_actions=[], vague_terms=[], sensors=[], untranslatable_reason=None,
                 summary="", conditions=[Condition(fact="mood", op="eq", value="angry", value_type="string")])
    fake = {"Never delete things on prod.acme.com": good, "Block angry agents": bad}
    b, reports = compile_policies(list(fake), decomposer=fake.__getitem__)
    # "Never delete ..." matches the delete template first; the template wins over the LLM.
    assert reports[0].source == "template:delete"
    assert reports[1].source == "claude" and reports[1].status == "invalid"
    assert "unknown fact 'mood'" in reports[1].errors[0]


def test_llm_url_glob_gets_scheme_wildcard():
    intent = Intent(effect="hold", semantic_actions=["payment"], vague_terms=[
        VagueTerm(term="prod", question="?", fact="url", op="glob", value_type="string")],
        sensors=[], untranslatable_reason=None, summary="", conditions=[])
    b, _ = compile_policies(["Pay nothing from the prod tenant please"], {"prod": "prod.acme.com/*"},
                            decomposer=lambda _: intent)
    assert {"fact": "url", "op": "glob", "value": "*://prod.acme.com/*"} in b["rules"][0]["when"]["all"]


def test_decompose_calls_claude_with_structured_output():
    calls = {}
    parsed = Intent(effect="hold", semantic_actions=["payment"], conditions=[], vague_terms=[], sensors=[],
                    untranslatable_reason=None, summary="s")

    class Messages:
        def parse(self, **kw):
            calls.update(kw)
            return SimpleNamespace(stop_reason="end_turn", parsed_output=parsed)

    client = SimpleNamespace(beta=SimpleNamespace(messages=Messages()))
    assert decompose("Hold payments", client=client) is parsed
    assert calls["model"] == "claude-opus-5-5" and calls["output_format"] is Intent
    assert calls["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in calls["betas"]


def test_decompose_refusal_is_untranslatable():
    class Messages:
        def parse(self, **kw):
            return SimpleNamespace(stop_reason="refusal", parsed_output=None)

    intent = decompose("x", client=SimpleNamespace(beta=SimpleNamespace(messages=Messages())))
    assert intent.untranslatable_reason


def test_tampered_bundle_fails_verification(tmp_path, bundle):
    path = tmp_path / "bundle.json"
    bundle_mod.save(bundle, path)
    data = json.loads(path.read_text())
    data["rules"][0]["effect"] = "allow"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="signature"):
        bundle_mod.load(path)


def test_enforce_changes_hash(bundle):
    shadow = json.loads(json.dumps(bundle))
    before = shadow["hash"]
    bundle_mod.set_mode(shadow, ["p1"], "shadow")
    assert shadow["hash"] != before and bundle_mod.verify(shadow)
