"""The committed vectors are what the TypeScript port is tested against; Python must not drift."""

import json
from pathlib import Path

from conformance.generate import FACT_CASES, SNAPSHOTS, conformance_bundle, summarize
from observ.evaluator import evaluate
from observ.facts import facts_from_snapshot

VECTORS = json.loads((Path(__file__).resolve().parent.parent / "conformance" / "vectors.json").read_text())


def test_bundle_matches_committed():
    assert conformance_bundle()["rules"] == VECTORS["bundle"]["rules"]


def test_python_reproduces_every_vector():
    bundle = VECTORS["bundle"]
    assert len(VECTORS["vectors"]) == len(SNAPSHOTS) + len(FACT_CASES)
    for v in VECTORS["vectors"]:
        facts = v.get("facts")
        if "snapshot" in v:
            facts = facts_from_snapshot(v["snapshot"])
            assert facts == v["expect_facts"], v["name"]
        out = evaluate(bundle, facts, v.get("confidence"), v.get("grant", False))
        assert summarize(out) == v["expect"], v["name"]
