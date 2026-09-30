import json

from conftest import Clock, snap_pay
from observ.audit import AuditLog, replay, verify_chain
from observ.engine import GRANT_TTL, Engine


def egress_facts(amount, payee="Initech"):
    return {"sensor": "egress", "verb": "request", "semantic_action": "payment", "amount": amount, "payee": payee,
            "url": "http://127.0.0.1:8765/pay", "host": "127.0.0.1:8765", "path": "/pay"}


def test_snapshot_decisions(engine):
    assert engine.decide(snapshot=snap_pay("$200"))["decision"] == "allow"
    out = engine.decide(snapshot=snap_pay("$7,200.00"))
    assert out["decision"] == "hold" and out["hold_id"] in engine.holds


def test_hold_approval_is_single_use(bundle, audit_dir):
    clock = Clock()
    engine = Engine(bundle=bundle, audit=AuditLog(audit_dir), clock=clock)
    first = engine.decide(snapshot=snap_pay("$6,000"), sensor="harness")
    engine.resolve_hold(first["hold_id"], True)
    retry = engine.decide(snapshot=snap_pay("$6,000"), sensor="harness")
    assert retry["decision"] == "allow" and retry["reason"] == "approved by a human"
    again = engine.decide(snapshot=snap_pay("$6,000"), sensor="harness")
    assert again["decision"] == "hold", "an approval must not approve a second action"


def test_consumed_grant_cannot_be_reused(engine):
    h = engine.decide(snapshot=snap_pay("$6,000"))["hold_id"]
    engine.resolve_hold(h, True)
    assert engine.consume_grant(h) is True
    assert engine.decide(snapshot=snap_pay("$6,000"), sensor="harness")["decision"] == "hold"
    assert engine.consume_grant(h) is False


def test_grants_expire(bundle, audit_dir):
    clock = Clock()
    engine = Engine(bundle=bundle, audit=AuditLog(audit_dir), clock=clock)
    h = engine.decide(snapshot=snap_pay("$6,000"))["hold_id"]
    engine.resolve_hold(h, True)
    clock.t += GRANT_TTL + 1
    assert engine.decide(snapshot=snap_pay("$6,000"))["decision"] == "hold"


def test_denied_hold_grants_nothing(engine):
    h = engine.decide(snapshot=snap_pay("$6,000"))["hold_id"]
    engine.resolve_hold(h, False)
    assert engine.decide(snapshot=snap_pay("$6,000"))["decision"] == "hold"


def test_egress_correlation(engine):
    # No UI action preceded this commit.
    out = engine.decide(facts=egress_facts(200), sensor="egress")
    assert out["facts"]["ui_intent"] == "none" and out["decision"] == "block"
    # A UI allow creates an intent the egress request matches (once).
    engine.decide(snapshot=snap_pay("$200"))
    out = engine.decide(facts=egress_facts(200), sensor="egress")
    assert out["facts"]["ui_intent"] == "matched" and out["decision"] == "allow"
    assert engine.decide(facts=egress_facts(200), sensor="egress")["decision"] == "block"


def test_egress_mismatch_blocks(engine):
    engine.decide(snapshot=snap_pay("$720"))
    out = engine.decide(facts=egress_facts(7200), sensor="egress")
    assert out["facts"]["ui_intent"] == "mismatch" and out["decision"] == "block"


def test_approved_hold_covers_the_egress_request(engine):
    h = engine.decide(snapshot=snap_pay("$6,000"))["hold_id"]
    engine.resolve_hold(h, True)
    engine.consume_grant(h)  # the extension replays the submit itself
    assert engine.decide(facts=egress_facts(6000), sensor="egress")["decision"] == "allow"


def test_audit_chain_and_replay(engine, audit_dir):
    engine.decide(snapshot=snap_pay("$200"))
    h = engine.decide(snapshot=snap_pay("$9,000"))["hold_id"]
    engine.resolve_hold(h, True)
    engine.decide(snapshot=snap_pay("$9,000"), sensor="harness")
    engine.decide(facts=egress_facts(9000), sensor="egress")
    records = engine.audit.records()
    assert verify_chain(records) == (True, None)
    count, mismatches = replay(audit_dir)
    assert count == 4 and mismatches == []


def test_audit_tamper_is_detected(engine, audit_dir):
    engine.decide(snapshot=snap_pay("$200"))
    engine.decide(snapshot=snap_pay("$9,000"))
    path = audit_dir / "audit.jsonl"
    lines = path.read_text().splitlines()
    rec = json.loads(lines[1])
    rec["decision"] = "allow"
    lines[1] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")
    ok, bad_seq = verify_chain(AuditLog(audit_dir).records())
    assert not ok and bad_seq == 2
