// Runs every vector in conformance/vectors.json through the TypeScript port and requires
// byte-identical results to the Python reference implementation.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { evaluate, type Bundle, type Decision } from "../src/evaluator.ts";
import { factsFromSnapshot } from "../src/facts.ts";

const { bundle, vectors } = JSON.parse(
  readFileSync(new URL("../../conformance/vectors.json", import.meta.url), "utf8"),
) as { bundle: Bundle; vectors: any[] };

const summarize = (d: Decision) => ({
  decision: d.decision,
  reason: d.reason,
  rule_ids: d.rule_ids,
  shadow: d.shadow.map((h) => `${h.rule_id}:${h.effect}:${h.outcome}`),
});

for (const v of vectors) {
  test(v.name, () => {
    let facts = v.facts;
    if (v.snapshot) {
      facts = factsFromSnapshot(v.snapshot);
      assert.deepEqual(facts, v.expect_facts, "facts differ from Python");
    }
    assert.deepEqual(summarize(evaluate(bundle, facts, v.confidence, v.grant ?? false)), v.expect);
  });
}
