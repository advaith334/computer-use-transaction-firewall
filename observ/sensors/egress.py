"""Egress commit sensor: turn a committing HTTP request into facts.

Catches commits however they were produced (clicks, page JS, an agent writing code or
calling curl). The request body is read as a form and fed through the same fact
extractor as the UI sensors, with the URL path as the "button label" (/pay, /mail/send,
/admin/users/delete). observd then correlates the request with recent UI intents
(ui_intent = matched | none | mismatch).

Only known commit types are gated (payment, payee_change, email_send, delete); other
POSTs (telemetry, search, autosave) pass, since holding every unclassified request would
break normal browsing. APP_PARSERS is the extension point for per-app precision.
"""

from __future__ import annotations

import json
from typing import Callable
from urllib.parse import parse_qsl, urlparse

from observ.facts import facts_from_snapshot

GATED = {"payment", "payee_change", "email_send", "delete"}
COMMIT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# (host glob-free substring, path prefix) -> function(fields) -> facts overrides
APP_PARSERS: dict[tuple[str, str], Callable[[dict], dict]] = {}


def _fields(content_type: str, body: str) -> dict | None:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct == "application/x-www-form-urlencoded":
        return dict(parse_qsl(body, keep_blank_values=True))
    if ct == "application/json" or (not ct and body.strip().startswith("{")):
        try:
            data = json.loads(body or "{}")
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return {k: v for k, v in data.items() if isinstance(v, (str, int, float))}
    return None


def request_facts(url: str, method: str, content_type: str, body: str) -> dict | None:
    """Facts for a committing request, or None if it isn't one we gate."""
    if method.upper() not in COMMIT_METHODS:
        return None
    fields = _fields(content_type, body)
    if fields is None:
        return None
    path = urlparse(url).path or "/"
    snapshot = {
        "url": url,
        "event": "submit",
        "element": {"tag": "request", "type": "", "role": "request",
                    "label": path.replace("/", " ").replace("-", " ").replace("_", " ").strip()},
        "form": {
            "action": path,
            "method": "post",
            "submit_label": "",
            "fields": [{"name": k, "label": "", "type": "text", "value": str(v)} for k, v in fields.items()],
        },
    }
    facts = facts_from_snapshot(snapshot, sensor="egress")
    facts["verb"] = "request"
    host = (urlparse(url).netloc or "").lower()
    for (h, prefix), parser in APP_PARSERS.items():
        if h in host and path.startswith(prefix):
            facts.update(parser(fields))
    if facts.get("semantic_action") not in GATED:
        return None
    return facts
