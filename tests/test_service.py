"""observd HTTP API, the CLI, and `observ init`."""

import json

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from conftest import ROOT, snap_pay
from observ.cli import app as cli
from observ.init import detect, plan
from observ.observd.server import create_app


def test_observd_api(engine):
    c = TestClient(create_app(engine))
    assert c.get("/v1/health").json()["bundle_hash"] == engine.bundle["hash"]
    assert c.post("/v1/decide", json={}).status_code == 400
    held = c.post("/v1/decide", json={"snapshot": snap_pay("$9,000")}).json()
    assert held["decision"] == "hold"
    assert [h["id"] for h in c.get("/v1/holds", params={"status": "pending"}).json()] == [held["hold_id"]]
    assert "Approve" in c.get("/").text
    assert c.post(f"/v1/holds/{held['hold_id']}/approve").json()["status"] == "approved"
    assert c.post(f"/v1/holds/{held['hold_id']}/consume").json() == {"consumed": True}
    assert c.post(f"/v1/holds/{held['hold_id']}/maybe").status_code == 400
    v = c.get("/v1/audit/verify").json()
    assert v["chain_ok"] and v["mismatches"] == [] and v["replayed"] == 1


def test_cli_compile_enforce_test(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "policies.txt").write_text((ROOT / "policies.txt").read_text())
    (tmp_path / "policies.answers.yaml").write_text((ROOT / "policies.answers.yaml").read_text())
    runner = CliRunner()
    r = runner.invoke(cli, ["compile", "--no-interactive"])
    assert r.exit_code == 0, r.output
    assert "5/5 policies compiled" in r.output
    assert all(rule["mode"] == "shadow" for rule in json.loads((tmp_path / "bundle.json").read_text())["rules"])
    assert runner.invoke(cli, ["enforce", "--all"]).exit_code == 0
    assert all(rule["mode"] == "enforce" for rule in json.loads((tmp_path / "bundle.json").read_text())["rules"])
    r = runner.invoke(cli, ["test"])
    assert r.exit_code == 0 and "20/20 passed" in r.output


def test_init_detects_the_example_agents():
    names = {d.name for d in detect(ROOT / "examples")}
    assert {"playwright", "anthropic-computer-use", "direct-http"} <= names
    assert {"extension", "harness", "egress"} <= set(plan(detect(ROOT / "examples")))


def test_init_maps_stacks_to_sensors(tmp_path):
    (tmp_path / "agent.py").write_text("from browser_use import Agent\n")
    (tmp_path / "requirements.txt").write_text("browser-use\nbrowserbase\n")
    found = detect(tmp_path)
    assert {d.name for d in found} >= {"browser-use", "browserbase"}
    assert plan(found) == ["extension", "egress"]
    (tmp_path / "cloud.py").write_text("BASE='https://api.browser-use.com'\n")
    assert "cdp" in plan(detect(tmp_path))


def test_spec_files_match_code():
    from observ.schema import export

    assert json.loads((ROOT / "spec" / "fact_schema.json").read_text()) == json.loads(json.dumps(export()))
