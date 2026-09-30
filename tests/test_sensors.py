import httpx
import pytest
from mitmproxy.test import tflow

from conftest import snap_pay
from observ.observd.server import create_app
from observ.sdk import ObservClient
from observ.sensors.egress import request_facts
from observ.sensors.egress_addon import ObservEgress
from observ.sensors.harness_anthropic import ComputerUseGuard, normalize

P = "http://127.0.0.1:8765"


# -- egress -------------------------------------------------------------------------------
def test_request_facts():
    f = request_facts(P + "/api/payments", "POST", "application/json", '{"amount": 7200, "payee": "Acme"}')
    assert (f["semantic_action"], f["amount"], f["payee"], f["verb"]) == ("payment", 7200, "Acme", "request")
    f = request_facts(P + "/mail/send", "POST", "application/x-www-form-urlencoded", "to=a%40evil.com")
    assert f["recipient_domain"] == "evil.com"
    assert request_facts(P + "/telemetry", "POST", "application/json", '{"e": 1}') is None
    assert request_facts(P + "/pay", "GET", "", "") is None


def _flow(url: str, body: str, ctype: str = "application/json"):
    f = tflow.tflow()
    f.request.method = "POST"
    f.request.url = url
    f.request.headers["content-type"] = ctype
    f.request.text = body
    return f


@pytest.fixture
def addon(engine):
    a = ObservEgress()
    a.url = "http://observd"
    a.observd_host = "observd"
    a.hold_wait = 0.2
    a.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(engine)))
    return a


async def test_addon_blocks_commit_without_ui_intent(addon):
    flow = _flow(P + "/api/payments", '{"amount": 200, "payee": "Globex"}')
    await addon.request(flow)
    assert flow.response.status_code == 403
    assert flow.response.headers["x-observ-decision"] == "block"


async def test_addon_passes_matched_commit(addon, engine):
    engine.decide(snapshot=snap_pay("$200", payee="Globex"))
    flow = _flow(P + "/pay", "payee=Globex&amount=%24200", "application/x-www-form-urlencoded")
    await addon.request(flow)
    assert flow.response is None


async def test_addon_fails_closed_when_observd_is_down(addon):
    addon.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down"))))
    flow = _flow(P + "/api/payments", '{"amount": 1, "payee": "x"}')
    await addon.request(flow)
    assert flow.response.status_code == 403


# -- harness ------------------------------------------------------------------------------
def test_normalize_both_tool_shapes():
    assert normalize({"name": "left_click", "input": {"coordinate": [1, 2]}}) == ("left_click", {"coordinate": [1, 2]})
    assert normalize({"name": "computer", "input": {"action": "key", "text": "Return"}}) == ("key", {"text": "Return"})


def test_guard_passes_passive_actions_without_asking(engine):
    guard = ComputerUseGuard(ObservClient(engine=engine))
    ran = []
    for block in [{"id": "1", "name": "screenshot", "input": {}}, {"id": "2", "name": "type", "input": {"text": "hi"}},
                  {"id": "3", "name": "key", "input": {"text": "Tab"}}]:
        r = guard.run(block, lambda a, p: ran.append(a) or "ok")
        assert not r.get("is_error")
    assert ran == ["screenshot", "type", "key"] and engine.audit.records() == []


def test_guard_without_page_holds_unknown_clicks_and_returns_error(engine):
    guard = ComputerUseGuard(ObservClient(engine=engine), hold_timeout=0.1)
    ran = []
    r = guard.run({"id": "t1", "name": "left_click", "input": {"coordinate": [5, 5]}}, lambda a, p: ran.append(a))
    assert r["is_error"] and r["content"].startswith("Held for human approval") and ran == []


class FakePage:
    """Stands in for a Playwright page: returns a DOM snapshot for any coordinate."""

    def __init__(self, snapshot):
        self.snapshot = snapshot

    def evaluate(self, js, arg=None):
        return self.snapshot if arg is not None else None


def test_guard_with_page_blocks_payee_change(engine):
    bank = {"url": P + "/vendors/acme/bank", "event": "click", "key": None,
            "element": {"tag": "button", "type": "submit", "role": "button", "label": "Save bank details"},
            "form": {"action": "/vendors/acme/bank", "method": "post", "submit_label": "Save bank details",
                     "fields": [{"name": "routing", "label": "Routing number", "type": "text", "value": "026009593"}]}}
    guard = ComputerUseGuard(ObservClient(engine=engine), page=FakePage(bank))
    ran = []
    r = guard.run({"id": "t2", "name": "left_click", "input": {"coordinate": [100, 382]}}, lambda a, p: ran.append(a))
    assert r["is_error"] and "p2" in r["content"] and ran == []


def test_guard_executes_after_approval(engine):
    import threading
    import time

    client = ObservClient(engine=engine)
    guard = ComputerUseGuard(client, page=FakePage(dict(snap_pay("$6,000"), event="click")), hold_timeout=5)

    def approve_soon():
        for _ in range(50):
            pending = [h for h in engine.holds.values() if h.status == "pending"]
            if pending:
                engine.resolve_hold(pending[0].id, True)
                return
            time.sleep(0.05)

    threading.Thread(target=approve_soon).start()
    r = guard.run({"id": "t3", "name": "left_click", "input": {"coordinate": [1, 1]}}, lambda a, p: "clicked")
    assert r == {"type": "tool_result", "tool_use_id": "t3", "content": "clicked"}
    assert engine.grants == {}, "the guard must consume the approval it used"
