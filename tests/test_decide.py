from types import SimpleNamespace

import pytest

from observ.audit import AuditLog, replay
from observ.decide.anthropic_haiku import HaikuBackend, _Answer, _Answers
from observ.decide.backend import QUESTIONS, DecisionState
from observ.decide.fixture import FixtureBackend
from observ.decide.jev import JevBackend
from observ.decide.local import LocalKeywordBackend
from observ.decide.openai_decisions import OpenAIDecisionsBackend
from observ.engine import Engine

OBSCURE = {"url": "http://127.0.0.1:8765/ops/1", "event": "submit", "key": None,
           "element": {"tag": "button", "type": "submit", "role": "button", "label": "Confirm"},
           "form": {"action": "/ops/1/x", "method": "post", "submit_label": "Confirm",
                    "fields": [{"name": "f1", "label": "Item", "type": "text", "value": "update remittance details"}]}}


def test_without_a_tier_unclassifiable_commits_hold(engine):
    assert engine.decide(snapshot=OBSCURE)["decision"] == "hold"


def test_low_confidence_answer_holds(bundle, tmp_path):
    backend = FixtureBackend({"semantic_action": ("payee_change", 0.55)})
    engine = Engine(bundle=bundle, audit=AuditLog(tmp_path), backend=backend)
    out = engine.decide(snapshot=OBSCURE)
    assert out["decision"] == "hold" and out["confidence"]["semantic_action"] == 0.55


def test_high_confidence_answer_decides_and_replays(bundle, tmp_path):
    backend = FixtureBackend({"semantic_action": ("payee_change", 0.97)})
    engine = Engine(bundle=bundle, audit=AuditLog(tmp_path), backend=backend)
    out = engine.decide(snapshot=OBSCURE)
    assert out["decision"] == "block" and out["decision_tier"]["backend"] == "fixture"
    count, mismatches = replay(tmp_path)  # the answer is in the audit record: replay is exact
    assert count == 1 and mismatches == []


def test_tier_is_not_asked_for_clear_actions(bundle, tmp_path):
    backend = FixtureBackend({})
    engine = Engine(bundle=bundle, audit=AuditLog(tmp_path), backend=backend)
    engine.decide(snapshot={"url": "http://x/", "event": "click", "element": {"tag": "a", "label": "Home"}})
    assert backend.calls == []


def test_local_backend():
    local = LocalKeywordBackend()
    qs = [QUESTIONS["semantic_action"], QUESTIONS["amount"]]
    a = local.ask(DecisionState("form action: /ops/release-funds\nfield Value: $8,400.00"), qs)
    assert a["semantic_action"].value == "payment" and a["amount"].value == 8400
    assert local.ask(DecisionState("Week 38 timesheet"), qs)["semantic_action"].value == "none"


def test_haiku_backend_maps_typed_answers():
    seen = {}

    class Messages:
        def parse(self, **kw):
            seen.update(kw)
            return SimpleNamespace(stop_reason="end_turn", parsed_output=_Answers(answers=[
                _Answer(question_id="semantic_action", value="payment", confidence=0.91),
                _Answer(question_id="amount", value="$8,400", confidence=1.4),
                _Answer(question_id="semantic_action", value="not-an-option", confidence=0.9),
            ]))

    backend = HaikuBackend(client=SimpleNamespace(messages=Messages()))
    out = backend.ask(DecisionState("release funds", image_png=b"\x89PNG"),
                      [QUESTIONS["semantic_action"], QUESTIONS["amount"]])
    assert seen["model"] == "claude-haiku-4-5"
    assert seen["messages"][0]["content"][0]["type"] == "image"
    assert out["amount"].value == 8400 and out["amount"].confidence == 1.0
    assert out["semantic_action"].value == "payment"  # the invalid option was dropped, not overwritten


@pytest.mark.parametrize("backend", [OpenAIDecisionsBackend(), JevBackend()])
def test_preview_backends_fail_loudly(backend):
    with pytest.raises(NotImplementedError):
        backend.ask(DecisionState("x"), [QUESTIONS["semantic_action"]])
