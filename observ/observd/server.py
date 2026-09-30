"""observd: the self-hosted decision service every sensor talks to.

Runs next to the agent (sidecar / localhost / VPC). Screenshots and DOM data never leave
the customer's environment unless they configure a hosted decision backend.
"""

from __future__ import annotations

import base64
import html
from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel

from observ.audit import replay, verify_chain
from observ.engine import Engine


class DecideRequest(BaseModel):
    sensor: str = "extension"
    session: str = "default"
    snapshot: dict | None = None
    facts: dict | None = None
    image_b64: str | None = None


def create_app(engine: Engine) -> FastAPI:
    app = FastAPI(title="observd", version="0.1.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/v1/health")
    def health() -> dict:
        return {"ok": True, "bundle_hash": engine.bundle["hash"], "backend": getattr(engine.backend, "name", None)}

    @app.get("/v1/bundle")
    def get_bundle() -> dict:
        return engine.bundle

    @app.post("/v1/decide")
    def decide(req: DecideRequest) -> dict:
        if req.snapshot is None and req.facts is None:
            raise HTTPException(400, "send a snapshot or facts")
        image = base64.b64decode(req.image_b64) if req.image_b64 else None
        return engine.decide(facts=req.facts, snapshot=req.snapshot, sensor=req.sensor,
                             session=req.session, image=image)

    @app.get("/v1/holds")
    def list_holds(status: str | None = None) -> list[dict]:
        return [asdict(h) for h in engine.holds.values() if status is None or h.status == status]

    @app.get("/v1/holds/{hold_id}")
    def get_hold(hold_id: str) -> dict:
        if hold_id not in engine.holds:
            raise HTTPException(404, "no such hold")
        return asdict(engine.holds[hold_id])

    @app.post("/v1/holds/{hold_id}/consume")
    def consume(hold_id: str) -> dict:
        return {"consumed": engine.consume_grant(hold_id)}

    @app.post("/v1/holds/{hold_id}/{verdict}")
    def resolve(hold_id: str, verdict: str, by: str = "api"):
        if hold_id not in engine.holds:
            raise HTTPException(404, "no such hold")
        if verdict not in ("approve", "deny"):
            raise HTTPException(400, "verdict must be approve or deny")
        hold = engine.resolve_hold(hold_id, verdict == "approve", by=by)
        if by == "inbox":  # the HTML approvals inbox posts a form; send the reviewer back to it
            return RedirectResponse("/", status_code=303)
        return asdict(hold)

    @app.get("/v1/audit")
    def audit(limit: int = 100) -> list[dict]:
        return engine.audit.records()[-limit:]

    @app.get("/v1/audit/verify")
    def audit_verify() -> dict:
        ok, bad = verify_chain(engine.audit.records())
        count, mismatches = replay(engine.audit.dir)
        return {"chain_ok": ok, "first_bad_seq": bad, "replayed": count, "mismatches": mismatches}

    @app.get("/", response_class=HTMLResponse)
    def inbox() -> str:
        rows = []
        for h in sorted(engine.holds.values(), key=lambda h: -h.created):
            f = h.facts
            what = html.escape(f"{f.get('semantic_action')} {f.get('amount', '')} {f.get('payee', '')} @ {f.get('host', '')}")
            actions = (
                f"<form method=post action='/v1/holds/{h.id}/approve?by=inbox' style='display:inline'>"
                f"<button>Approve</button></form> "
                f"<form method=post action='/v1/holds/{h.id}/deny?by=inbox' style='display:inline'>"
                f"<button>Deny</button></form>"
                if h.status == "pending" else html.escape(h.status)
            )
            rows.append(f"<tr><td>{h.id}</td><td>{what}</td><td>{html.escape(h.reason)}</td><td>{actions}</td></tr>")
        return (
            "<!doctype html><meta charset=utf-8><title>Observ approvals</title>"
            "<meta http-equiv=refresh content=3>"
            "<style>body{font:14px system-ui;margin:24px}td,th{padding:6px 10px;border-bottom:1px solid #ddd;text-align:left}</style>"
            f"<h1>Observ approvals</h1><p>Bundle {engine.bundle['hash'][:12]}</p>"
            "<table><tr><th>Hold</th><th>Action</th><th>Why</th><th></th></tr>"
            + "".join(rows) + "</table>"
        )

    return app
