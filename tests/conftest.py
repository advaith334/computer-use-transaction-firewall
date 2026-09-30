from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from observ.audit import AuditLog
from observ.compile.pipeline import compile_policies, read_answers, read_policies
from observ.engine import Engine

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def bundle() -> dict:
    b, _ = compile_policies(read_policies(ROOT / "policies.txt"), read_answers(ROOT / "policies.answers.yaml"),
                            mode="enforce")
    return b


@pytest.fixture
def audit_dir(tmp_path) -> Path:
    return tmp_path / "audit"


@pytest.fixture
def engine(bundle, audit_dir) -> Engine:
    return Engine(bundle=bundle, audit=AuditLog(audit_dir))


class Clock:
    def __init__(self, t: float = 1_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def snap_pay(amount: str, payee: str = "Initech", url: str = "http://127.0.0.1:8765/invoices/INV-1"):
    return {"url": url, "event": "submit", "key": None,
            "element": {"tag": "button", "type": "submit", "role": "button", "label": "Pay invoice"},
            "form": {"action": "/pay", "method": "post", "submit_label": "Pay invoice",
                     "fields": [{"name": "payee", "label": "Payee", "type": "text", "value": payee},
                                {"name": "amount", "label": "Amount", "type": "text", "value": amount}]}}
