"""Client used by Python sensors: talks to observd over HTTP, or runs the engine in-process.

    client = ObservClient()                     # http://127.0.0.1:7777 (OBSERV_URL)
    client = ObservClient(engine=engine)        # in-process, no server
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import asdict

import httpx

from observ.engine import Engine


class ObservClient:
    def __init__(self, url: str | None = None, engine: Engine | None = None, timeout: float = 10.0):
        self.engine = engine
        self.url = (url or os.environ.get("OBSERV_URL") or "http://127.0.0.1:7777").rstrip("/")
        self._http = None if engine else httpx.Client(base_url=self.url, timeout=timeout)

    def decide(
        self,
        *,
        sensor: str,
        facts: dict | None = None,
        snapshot: dict | None = None,
        session: str = "default",
        image: bytes | None = None,
    ) -> dict:
        if self.engine:
            return self.engine.decide(facts=facts, snapshot=snapshot, sensor=sensor, session=session, image=image)
        body = {"sensor": sensor, "session": session, "facts": facts, "snapshot": snapshot,
                "image_b64": base64.b64encode(image).decode() if image else None}
        r = self._http.post("/v1/decide", json=body)
        r.raise_for_status()
        return r.json()

    def hold(self, hold_id: str) -> dict:
        if self.engine:
            return asdict(self.engine.holds[hold_id])
        r = self._http.get(f"/v1/holds/{hold_id}")
        r.raise_for_status()
        return r.json()

    def resolve(self, hold_id: str, approve: bool, by: str = "sdk") -> dict:
        if self.engine:
            return asdict(self.engine.resolve_hold(hold_id, approve, by=by))
        r = self._http.post(f"/v1/holds/{hold_id}/{'approve' if approve else 'deny'}", params={"by": by})
        r.raise_for_status()
        return r.json()

    def consume(self, hold_id: str) -> bool:
        if self.engine:
            return self.engine.consume_grant(hold_id)
        r = self._http.post(f"/v1/holds/{hold_id}/consume")
        r.raise_for_status()
        return r.json()["consumed"]

    def wait_for_hold(self, hold_id: str, timeout: float, poll: float = 0.5) -> str:
        """Block until the hold is approved/denied or the timeout passes. Returns the status."""
        deadline = time.monotonic() + timeout
        while True:
            status = self.hold(hold_id)["status"]
            if status != "pending" or time.monotonic() >= deadline:
                return status
            time.sleep(poll)
