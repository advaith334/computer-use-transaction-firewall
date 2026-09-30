"""The `observ` command line."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import httpx
import typer
import yaml

from observ.compile import bundle as bundle_mod
from observ.compile.pipeline import (
    compile_policies,
    default_decomposer,
    read_answers,
    read_policies,
    run_tests,
)
from observ.compile.testgen import describe

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Observ: a transaction firewall for computer-use agents.")
audit_app = typer.Typer(no_args_is_help=True, help="Inspect and verify the audit log.")
app.add_typer(audit_app, name="audit")

STATUS_MARK = {"ok": "✓", "needs_clarification": "?", "invalid": "✗", "untranslatable": "✗", "rejected": "✗"}


def _fmt_when(cond: dict) -> str:
    if "all" in cond:
        return " AND ".join(_fmt_when(c) for c in cond["all"]) or "(everything)"
    if "any" in cond:
        return "(" + " OR ".join(_fmt_when(c) for c in cond["any"]) + ")"
    if "not" in cond:
        return "NOT " + _fmt_when(cond["not"])
    sym = {"eq": "=", "neq": "≠", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤"}.get(cond["op"], cond["op"])
    return f"{cond['fact']} {sym} {json.dumps(cond.get('value'))}"


@app.command("compile")
def compile_cmd(
    policies: Path = typer.Argument(Path("policies.txt"), help="One plain-language policy per line."),
    out: Path = typer.Option(Path("bundle.json"), "-o", "--out"),
    answers: Path = typer.Option(Path("policies.answers.yaml"), help="Answers to clarifying questions."),
    mode: str = typer.Option("shadow", help="shadow (log would-blocks) or enforce."),
    interactive: bool = typer.Option(True, help="Ask clarifying questions in the terminal."),
) -> None:
    """Compile policies.txt into a reviewed, tested, signed policy bundle."""
    lines = read_policies(policies)
    decomposer = default_decomposer()
    while True:
        ans = read_answers(answers)
        bundle, reports = compile_policies(lines, ans, decomposer=decomposer, mode=mode)
        pending = [q for r in reports for q in r.questions]
        if not (pending and interactive and sys.stdin.isatty()):
            break
        typer.echo("The compiler needs a few answers before it can enforce these policies:\n")
        data = yaml.safe_load(answers.read_text()) if answers.exists() else {}
        data = data or {}
        for q in pending:
            data[q["term"]] = typer.prompt(f"  {q['question']}")
        answers.write_text(yaml.safe_dump(data, sort_keys=False))
        typer.echo("")

    typer.echo(f"Compiled {len(lines)} policies ({'Claude + templates' if decomposer else 'templates only; set ANTHROPIC_API_KEY for free-form policies'}):\n")
    for r in reports:
        typer.echo(f"{STATUS_MARK[r.status]} {r.rule_id}  {r.line}")
        typer.echo(f"      via {r.source}; status {r.status}" + (f"; flags {', '.join(r.flags)}" if r.flags else ""))
        if r.rule:
            typer.echo(f"      {r.rule['effect'].upper()} when {_fmt_when(r.rule['when'])}"
                       + (f"  [sensors: {', '.join(r.rule['sensors'])}]" if r.rule.get("sensors") else ""))
            for t in r.tests:
                typer.echo(f"        test  {describe(t)}")
        for e in r.errors:
            typer.echo(f"      ! {e}")
        for q in r.questions:
            typer.echo(f"      ? {q['question']}   (answer in {answers} as `{q['term']}: ...`)")
        typer.echo("")

    failures = run_tests(bundle)
    bundle_mod.save(bundle, out)
    ok = sum(r.status == "ok" for r in reports)
    typer.echo(f"{ok}/{len(reports)} policies compiled, {len(bundle['tests'])} generated tests, "
               f"{len(failures)} failing. Mode: {mode}. Wrote {out} (hash {bundle['hash'][:12]}).")
    if mode == "shadow":
        typer.echo("Rules start in shadow mode (they log would-blocks). Enforce with: observ enforce --all")
    if failures:
        raise typer.Exit(1)


@app.command("test")
def test_cmd(bundle_path: Path = typer.Argument(Path("bundle.json"))) -> None:
    """Run the bundle's generated policy tests."""
    b = bundle_mod.load(bundle_path)
    failures = run_tests(b)
    for t in b["tests"]:
        bad = next((a for f, a in failures if f is t), None)
        typer.echo(f"{'✗' if bad else '✓'} {t['rule_id']} {describe(t)}" + (f"  (got {bad})" if bad else ""))
    typer.echo(f"\n{len(b['tests']) - len(failures)}/{len(b['tests'])} passed")
    if failures:
        raise typer.Exit(1)


def _set_mode(rule_ids: list[str], all_rules: bool, bundle_path: Path, mode: str) -> None:
    b = bundle_mod.load(bundle_path)
    changed = bundle_mod.set_mode(b, None if all_rules else rule_ids, mode)
    bundle_mod.save(b, bundle_path)
    typer.echo(f"{mode}: {', '.join(changed) or 'nothing changed'} (bundle hash {b['hash'][:12]}). Restart or reload observd.")


@app.command()
def enforce(rule_ids: list[str] = typer.Argument(None), all_rules: bool = typer.Option(False, "--all"),
            bundle_path: Path = typer.Option(Path("bundle.json"), "--bundle")) -> None:
    """Switch rules from shadow to enforce."""
    _set_mode(rule_ids or [], all_rules, bundle_path, "enforce")


@app.command()
def shadow(rule_ids: list[str] = typer.Argument(None), all_rules: bool = typer.Option(False, "--all"),
           bundle_path: Path = typer.Option(Path("bundle.json"), "--bundle")) -> None:
    """Switch rules back to shadow mode."""
    _set_mode(rule_ids or [], all_rules, bundle_path, "shadow")


@app.command()
def serve(
    bundle_path: Path = typer.Option(Path("bundle.json"), "--bundle"),
    audit_dir: Path = typer.Option(Path(".observ"), "--audit"),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(7777),
    backend: str = typer.Option(os.environ.get("OBSERV_DECISION_BACKEND", "local"),
                                help="Decision tier: local | anthropic | openai | jev | none"),
) -> None:
    """Run observd (decisions, holds, approval inbox, audit)."""
    import uvicorn

    from observ.decide.backend import backend_from_env
    from observ.observd.server import create_app
    from observ.runtime import make_engine

    os.environ["OBSERV_DECISION_BACKEND"] = backend
    engine = make_engine(bundle_path, audit_dir, backend_from_env())
    typer.echo(f"observd on http://{host}:{port}  bundle {engine.bundle['hash'][:12]}  backend {backend}  "
               f"audit {audit_dir}/audit.jsonl  approvals inbox: http://{host}:{port}/")
    uvicorn.run(create_app(engine), host=host, port=port, log_level="warning")


@app.command()
def egress(port: int = typer.Option(8080), observd: str = typer.Option("http://127.0.0.1:7777"),
           hold_wait: float = typer.Option(30.0)) -> None:
    """Run the egress commit proxy (mitmproxy) in front of the sandbox's traffic."""
    mitmdump = shutil.which("mitmdump") or str(Path(sys.executable).parent / "mitmdump")
    script = Path(__file__).parent / "sensors" / "egress_addon.py"
    env = {**os.environ, "OBSERV_URL": observd, "OBSERV_EGRESS_HOLD_WAIT": str(hold_wait),
           "PYTHONPATH": os.pathsep.join([str(Path(__file__).resolve().parent.parent), os.environ.get("PYTHONPATH", "")])}
    typer.echo(f"egress proxy on :{port} -> observd {observd}")
    raise typer.Exit(subprocess.call([mitmdump, "-q", "--listen-port", str(port), "-s", str(script)], env=env))


def _observd(url: str) -> httpx.Client:
    return httpx.Client(base_url=url, timeout=10)


@app.command()
def holds(url: str = typer.Option("http://127.0.0.1:7777", "--observd")) -> None:
    """List pending holds."""
    for h in _observd(url).get("/v1/holds", params={"status": "pending"}).json():
        f = h["facts"]
        typer.echo(f"{h['id']}  {f.get('semantic_action')} {f.get('amount', '')} {f.get('payee', '')}  {h['reason']}")


@app.command()
def approve(hold_id: str, url: str = typer.Option("http://127.0.0.1:7777", "--observd")) -> None:
    """Approve a held action."""
    typer.echo(_observd(url).post(f"/v1/holds/{hold_id}/approve", params={"by": "cli"}).json()["status"])


@app.command()
def deny(hold_id: str, url: str = typer.Option("http://127.0.0.1:7777", "--observd")) -> None:
    """Deny a held action."""
    typer.echo(_observd(url).post(f"/v1/holds/{hold_id}/deny", params={"by": "cli"}).json()["status"])


@audit_app.command("verify")
def audit_verify(audit_dir: Path = typer.Option(Path(".observ"), "--audit")) -> None:
    """Verify the hash chain and replay every decision."""
    from observ.audit import AuditLog, replay, verify_chain

    records = AuditLog(audit_dir).records()
    ok, bad = verify_chain(records)
    count, mismatches = replay(audit_dir)
    typer.echo(f"hash chain: {'intact' if ok else f'BROKEN at seq {bad}'} ({len(records)} records)")
    typer.echo(f"replay: {count - len(mismatches)}/{count} decisions reproduced exactly")
    for m in mismatches:
        typer.echo(f"  mismatch seq {m['seq']}: recorded {m['recorded']}, replayed {m['replayed']}")
    if not ok or mismatches:
        raise typer.Exit(1)


@audit_app.command("tail")
def audit_tail(audit_dir: Path = typer.Option(Path(".observ"), "--audit"), n: int = typer.Option(20, "-n")) -> None:
    """Show the last decisions."""
    from observ.audit import AuditLog

    for r in AuditLog(audit_dir).records()[-n:]:
        if r["kind"] == "decision":
            f = r["facts"]
            typer.echo(f"#{r['seq']:<4} {r['sensor']:9} {r['decision']:5} {f.get('semantic_action', ''):12} "
                       f"{str(f.get('amount', '')):>7}  {r['reason'][:70]}")
        else:
            typer.echo(f"#{r['seq']:<4} {r['kind']} {r.get('hold_id')} by {r.get('by')}")


@app.command()
def init(path: Path = typer.Argument(Path(".")), write: bool = typer.Option(True)) -> None:
    """Detect the agent stack in a project and pick the sensors to install."""
    from observ.init import WIRING, detect, plan, write_config

    found = detect(path)
    if not found:
        typer.echo("No computer-use agent stack detected. Point init at your agent's source directory.")
        raise typer.Exit(1)
    typer.echo("Detected:")
    for d in found:
        typer.echo(f"  {d.note:70} ({', '.join(d.files[:2])}{'…' if len(d.files) > 2 else ''})")
    sensors = plan(found)
    typer.echo("\nSensors to install:")
    for s in sensors:
        typer.echo(f"  {s:9} {WIRING[s]}")
    if write:
        typer.echo(f"\nWrote {write_config(path, found, sensors)}")
    typer.echo("\nNext: write policies.txt, run `observ compile`, `observ serve`, then `observ test`.")


@app.command()
def bench(
    policies: Path = typer.Option(Path("policies.txt")),
    answers: Path = typer.Option(Path("policies.answers.yaml")),
    out: Path = typer.Option(Path("bench/report.md")),
    with_backend: Optional[str] = typer.Option(None, help="Also bench a hosted decision tier, e.g. anthropic."),
) -> None:
    """Measure block rate, false stops, holds and latency on labelled scenarios."""
    from observ.bench import report

    extra = {}
    if with_backend:
        os.environ["OBSERV_DECISION_BACKEND"] = with_backend
        from observ.decide.backend import backend_from_env

        extra[with_backend] = backend_from_env()
    text = report(policies, answers, extra)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    typer.echo(text)
    typer.echo(f"Wrote {out}")


if __name__ == "__main__":
    app()
