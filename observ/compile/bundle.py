"""The signed policy bundle every sensor carries.

The hash covers everything that can change a decision (rules, modes, threshold), so an
audit record's bundle_hash pins exactly which policy decided it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

DEV_KEY = "observ-dev-signing-key"


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _signing_key() -> bytes:
    return os.environ.get("OBSERV_SIGNING_KEY", DEV_KEY).encode()


def seal(bundle: dict) -> dict:
    core = {k: bundle[k] for k in ("schema_version", "min_confidence", "rules")}
    digest = hashlib.sha256(canonical(core).encode()).hexdigest()
    bundle["hash"] = digest
    bundle["signature"] = hmac.new(_signing_key(), digest.encode(), hashlib.sha256).hexdigest()
    return bundle


def verify(bundle: dict) -> bool:
    expected = dict(bundle)
    seal(expected)
    return hmac.compare_digest(expected["hash"], bundle.get("hash", "")) and hmac.compare_digest(
        expected["signature"], bundle.get("signature", "")
    )


def load(path: str | Path) -> dict:
    bundle = json.loads(Path(path).read_text())
    if not verify(bundle):
        raise ValueError(f"{path}: bundle signature does not verify (edited by hand? recompile it)")
    return bundle


def save(bundle: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(seal(bundle), indent=2) + "\n")


def set_mode(bundle: dict, rule_ids: list[str] | None, mode: str) -> list[str]:
    changed = []
    for rule in bundle["rules"]:
        if rule_ids is None or rule["id"] in rule_ids:
            if rule.get("mode") != mode:
                rule["mode"] = mode
                changed.append(rule["id"])
    seal(bundle)
    return changed
