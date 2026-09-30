"""Shared helpers for the demo agents."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

PORTAL = os.environ.get("PORTAL_URL", "http://127.0.0.1:8765")
OBSERVD = os.environ.get("OBSERV_URL", "http://127.0.0.1:7777")
EGRESS = os.environ.get("OBSERV_EGRESS", "")  # e.g. http://127.0.0.1:8080; empty = no proxy


@dataclass
class Outcome:
    agent: str
    scenario: str
    expected: str  # committed | blocked | held->approved | held->denied
    actual: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.expected == self.actual


def browser_proxy() -> dict:
    """Launch kwargs routing the browser through the egress proxy. Chrome bypasses proxies
    for loopback by default; <-loopback> removes that so the demo's localhost portal is proxied."""
    if not EGRESS:
        return {}
    return {"proxy": {"server": EGRESS}, "extra_args": ["--proxy-bypass-list=<-loopback>"]}


def observ_state(page) -> dict | None:
    raw = page.get_attribute("html", "data-observ")
    return json.loads(raw) if raw else None


def settle(page, timeout: float = 20.0) -> tuple[str, str]:
    """Wait for a UI action to resolve. Returns (outcome, detail) where outcome is
    committed | blocked | held->approved | held->denied."""
    seen_hold = False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if page.query_selector("#result"):
                text = page.inner_text("#result")
                return ("held->approved" if seen_hold else "committed"), text
            st = observ_state(page)
        except Exception:  # navigation in progress
            time.sleep(0.1)
            continue
        if not st:
            try:
                body = page.inner_text("body")
            except Exception:
                body = ""
            if body.lstrip().startswith('{"observ"'):  # the egress proxy rejected the request
                return "blocked", "egress: " + json.loads(body)["observ"]["reason"]
        if st:
            if st["decision"] == "hold":
                seen_hold = True
            elif st["decision"] == "block":
                return ("held->denied" if seen_hold else "blocked"), st["reason"]
        time.sleep(0.1)
    return "timeout", ""
