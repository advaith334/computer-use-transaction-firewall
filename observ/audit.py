"""Append-only, hash-chained audit log, and deterministic replay.

Every decision record stores exactly the inputs the evaluator saw (facts, confidences,
grant flag, bundle hash). Replaying a record re-runs the evaluator on those inputs and
must reproduce the recorded decision; editing any record breaks the hash chain.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from observ.compile.bundle import canonical
from observ.evaluator import evaluate

GENESIS = "0" * 64


class AuditLog:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "bundles").mkdir(exist_ok=True)
        self.path = self.dir / "audit.jsonl"
        self._lock = threading.Lock()
        self._prev, self._seq = GENESIS, 0
        if self.path.exists():
            for line in self.path.read_text().splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self._prev, self._seq = rec["hash"], rec["seq"]

    def store_bundle(self, bundle: dict) -> None:
        p = self.dir / "bundles" / f"{bundle['hash']}.json"
        if not p.exists():
            p.write_text(json.dumps(bundle, indent=2))

    def append(self, record: dict) -> dict:
        with self._lock:
            self._seq += 1
            rec = {"seq": self._seq, "ts": round(time.time(), 3), **record, "prev": self._prev}
            rec["hash"] = hashlib.sha256((self._prev + canonical(rec)).encode()).hexdigest()
            with self.path.open("a") as f:
                f.write(canonical(rec) + "\n")
            self._prev = rec["hash"]
            return rec

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]


def verify_chain(records: list[dict]) -> tuple[bool, int | None]:
    prev = GENESIS
    for rec in records:
        body = {k: v for k, v in rec.items() if k != "hash"}
        if rec.get("prev") != prev:
            return False, rec.get("seq")
        if hashlib.sha256((prev + canonical(body)).encode()).hexdigest() != rec.get("hash"):
            return False, rec.get("seq")
        prev = rec["hash"]
    return True, None


def replay(directory: str | Path) -> tuple[int, list[dict]]:
    """Re-evaluate every decision record. Returns (count, mismatches)."""
    d = Path(directory)
    log = AuditLog(d)
    bundles: dict[str, dict] = {}
    count, mismatches = 0, []
    for rec in log.records():
        if rec.get("kind") != "decision":
            continue
        h = rec["bundle_hash"]
        if h not in bundles:
            bundles[h] = json.loads((d / "bundles" / f"{h}.json").read_text())
        out = evaluate(bundles[h], rec["facts"], rec.get("confidence"), rec.get("grant", False))
        count += 1
        if out["decision"] != rec["decision"] or out["rule_ids"] != rec["rule_ids"]:
            mismatches.append({"seq": rec["seq"], "recorded": rec["decision"], "replayed": out["decision"]})
    return count, mismatches
