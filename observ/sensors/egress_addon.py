"""mitmproxy addon: the egress commit proxy.

    mitmdump -s observ/sensors/egress_addon.py --listen-port 8080      (or: observ egress)

Point the sandbox's HTTP(S) proxy at it (E2B egressProxy, Vercel forwardURL, Docker
HTTP_PROXY, Chrome --proxy-server). For HTTPS, install mitmproxy's CA in the sandbox.
Environment: OBSERV_URL (observd), OBSERV_EGRESS_HOLD_WAIT (seconds to hold a request
open for approval before rejecting it; default 30).
"""

from __future__ import annotations

import asyncio
import json
import os
from urllib.parse import urlparse

import httpx
from mitmproxy import http

from observ.sensors.egress import request_facts


class ObservEgress:
    def __init__(self) -> None:
        self.url = os.environ.get("OBSERV_URL", "http://127.0.0.1:7777").rstrip("/")
        self.hold_wait = float(os.environ.get("OBSERV_EGRESS_HOLD_WAIT", "30"))
        self.session = os.environ.get("OBSERV_SESSION", "default")
        self.observd_host = urlparse(self.url).netloc
        self.client = httpx.AsyncClient(timeout=10)

    def _reject(self, flow: http.HTTPFlow, verdict: dict) -> None:
        flow.response = http.Response.make(
            403,
            json.dumps({"observ": {k: verdict.get(k) for k in ("decision", "reason", "rule_ids", "hold_id")}}),
            {"content-type": "application/json", "x-observ-decision": verdict.get("decision", "block")},
        )

    async def request(self, flow: http.HTTPFlow) -> None:
        req = flow.request
        if req.host_header == self.observd_host or f"{req.host}:{req.port}" == self.observd_host:
            return
        facts = request_facts(req.pretty_url, req.method, req.headers.get("content-type", ""), req.get_text() or "")
        if facts is None:
            return
        try:
            r = await self.client.post(f"{self.url}/v1/decide",
                                       json={"sensor": "egress", "session": self.session, "facts": facts})
            r.raise_for_status()
            verdict = r.json()
        except httpx.HTTPError as e:
            self._reject(flow, {"decision": "block", "reason": f"observd unreachable ({e.__class__.__name__}); failing closed"})
            return
        if verdict["decision"] == "allow":
            return
        if verdict["decision"] == "hold":
            status = await self._wait(verdict["hold_id"])
            if status == "approved":
                return
            verdict["reason"] += f" (hold {status})"
        self._reject(flow, verdict)

    async def _wait(self, hold_id: str) -> str:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.hold_wait
        while loop.time() < deadline:
            r = await self.client.get(f"{self.url}/v1/holds/{hold_id}")
            status = r.json().get("status")
            if status != "pending":
                return status
            await asyncio.sleep(0.5)
        return "pending"


addons = [ObservEgress()]
