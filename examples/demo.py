"""End-to-end demo: one policies.txt, four very different agents, three sensors.

    uv run python examples/demo.py            (add --headed to watch the browsers)

Starts the vendor portal, observd (in-process) and the egress proxy, then runs:
  1. a DOM-driven Playwright agent          -> browser extension
  2. a pixel agent (coordinates only)       -> browser extension
  3. a code-execution agent (direct HTTP)   -> egress commit proxy
  4. an Anthropic computer-use loop         -> harness sensor
  5. an agent whose browser has no extension -> egress commit proxy still blocks
A scripted "human" approves the $6,000 payment and denies every other hold. The demo
then checks the portal's ledger (what actually happened), verifies the audit hash chain
and replays every decision.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / ".demo"
os.environ.setdefault("PORTAL_URL", "http://127.0.0.1:8765")
os.environ.setdefault("OBSERV_URL", "http://127.0.0.1:7777")
os.environ.setdefault("OBSERV_EGRESS", "http://127.0.0.1:8080")

from playwright.sync_api import sync_playwright  # noqa: E402

from examples import agent_anthropic_replay, agent_bypass, agent_codeexec, agent_pixel, agent_playwright  # noqa: E402
from examples.common import browser_proxy  # noqa: E402
from examples.vendor_portal.app import create_app as portal_app  # noqa: E402
from observ.audit import replay, verify_chain  # noqa: E402
from observ.compile import bundle as bundle_mod  # noqa: E402
from observ.compile.pipeline import compile_policies, read_answers, read_policies  # noqa: E402
from observ.decide.local import LocalKeywordBackend  # noqa: E402
from observ.observd.server import create_app  # noqa: E402
from observ.runtime import Server, launch_with_extension, make_engine  # noqa: E402
from observ.sdk import ObservClient  # noqa: E402


def wait_port(port: int, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.1)
    raise RuntimeError(f"nothing listening on :{port}")


def human_reviewer(engine, stop: threading.Event) -> None:
    """Approves the $6,000 payment; denies everything else, after a short 'reading' delay."""
    while not stop.is_set():
        for h in list(engine.holds.values()):
            if h.status == "pending" and time.time() - h.created > 1.0:
                engine.resolve_hold(h.id, h.facts.get("amount") == 6000, by="demo-reviewer")
        time.sleep(0.2)


def main() -> int:
    headed = "--headed" in sys.argv
    shutil.rmtree(DEMO, ignore_errors=True)
    DEMO.mkdir()
    bundle, _ = compile_policies(read_policies(ROOT / "policies.txt"), read_answers(ROOT / "policies.answers.yaml"),
                                 mode="enforce")
    bundle_mod.save(bundle, DEMO / "bundle.json")
    engine = make_engine(DEMO / "bundle.json", DEMO / "audit", LocalKeywordBackend())

    portal = Server(portal_app(), 8765).start()
    observd = Server(create_app(engine), 7777).start()
    mitmdump = shutil.which("mitmdump") or str(Path(sys.executable).parent / "mitmdump")
    egress = subprocess.Popen(
        [mitmdump, "-q", "--listen-port", "8080", "-s", str(ROOT / "observ/sensors/egress_addon.py")],
        env={**os.environ, "PYTHONPATH": str(ROOT), "OBSERV_EGRESS_HOLD_WAIT": "10"},
    )
    stop = threading.Event()
    threading.Thread(target=human_reviewer, args=(engine, stop), daemon=True).start()
    results = []
    try:
        wait_port(8080)
        print(f"policies.txt compiled -> bundle {bundle['hash'][:12]} ({len(bundle['rules'])} rules, enforce)")
        print("portal :8765  observd :7777 (approvals inbox at /)  egress proxy :8080\n")
        with sync_playwright() as p:
            for mod in (agent_playwright, agent_pixel):
                ctx = launch_with_extension(p, tempfile.mkdtemp(), headless=not headed, **browser_proxy())
                results += mod.run(ctx)
                ctx.close()
            results += agent_codeexec.run()
            browser = p.chromium.launch(headless=not headed, args=browser_proxy().get("extra_args", []))
            results += agent_anthropic_replay.run(browser, ObservClient())
            results += agent_bypass.run(browser)  # plain Chromium: no extension loaded at all
            browser.close()
        ledger = httpx.get(portal.url + "/api/ledger").json()
    finally:
        stop.set()
        egress.terminate()
        egress.wait(timeout=10)

    width = max(len(o.scenario) for o in results)
    agent = None
    for o in results:
        if o.agent != agent:
            agent = o.agent
            print(f"\n{agent}")
        print(f"  {'✓' if o.ok else '✗'} {o.scenario:{width}}  expected {o.expected:15} got {o.actual}")
    passed = sum(o.ok for o in results)

    payments = sorted((str(e["amount"]), e["payee"]) for e in ledger if e["type"] == "payment")
    bad = [e for e in ledger if e["type"] in ("bank_change", "delete_user") or e.get("via") == "api"
           or (e["type"] == "email" and not str(e["to"]).endswith("acme.com"))]
    print(f"\nLedger (what actually reached the portal): {len(ledger)} commits")
    for e in ledger:
        print(f"  {e}")
    print(f"  bank changes / deletes / API payments / external emails: {len(bad)}")

    records = engine.audit.records()
    chain_ok, _ = verify_chain(records)
    replayed, mismatches = replay(DEMO / "audit")
    decisions = [r for r in records if r["kind"] == "decision"]
    lat = sorted(r["latency_ms"] for r in decisions)
    by_sensor = {}
    for r in decisions:
        by_sensor.setdefault(r["sensor"], []).append(r["decision"])
    print(f"\nAudit: {len(records)} records, hash chain {'intact' if chain_ok else 'BROKEN'}, "
          f"{replayed - len(mismatches)}/{replayed} decisions replayed exactly")
    print("  decisions by sensor: " + ", ".join(f"{s}={len(d)}" for s, d in by_sensor.items()))
    print(f"  engine decision latency p50 {lat[len(lat) // 2]:.2f} ms, max {lat[-1]:.2f} ms")
    print(f"\n{passed}/{len(results)} scenarios behaved as expected")
    portal.stop()
    observd.stop()
    ok = passed == len(results) and not bad and chain_ok and not mismatches
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
