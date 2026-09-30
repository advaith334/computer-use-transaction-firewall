// Port of observ/evaluator.py. Must return identical results for every vector in
// conformance/vectors.json (npm test). Keep it pure: no I/O, clocks or randomness.

export type Facts = Record<string, unknown>;
export type Condition =
  | { all: Condition[] }
  | { any: Condition[] }
  | { not: Condition }
  | { fact: string; op: string; value?: unknown };

export interface Rule {
  id: string;
  source: string;
  effect: "block" | "hold" | "allow";
  when: Condition;
  mode?: "enforce" | "shadow";
  on_unknown?: "block" | "hold" | "allow";
  sensors?: string[];
}

export interface Bundle {
  rules: Rule[];
  min_confidence?: number;
  hash?: string;
}

export interface Hit {
  rule_id: string;
  effect: string;
  outcome: "match" | "unknown";
  unknown_facts: string[];
}

export interface Decision {
  decision: "allow" | "hold" | "block";
  reason: string;
  rule_ids: string[];
  hits: Hit[];
  shadow: Hit[];
  bundle_hash: string | null;
}

const SEVERITY: Record<string, number> = { allow: 0, hold: 1, block: 2 };
const GLOB_SPECIAL = new Set("\\^$.|?+()[]{}".split(""));

export function globToRegex(pattern: string): string {
  let out = "";
  for (const ch of pattern) {
    if (ch === "*") out += ".*";
    else if (GLOB_SPECIAL.has(ch)) out += "\\" + ch;
    else out += ch;
  }
  return out;
}

const norm = (v: unknown) => (typeof v === "string" ? v.toLowerCase() : v);
const isNumber = (v: unknown): v is number => typeof v === "number";

function leaf(cond: { fact: string; op: string; value?: unknown }, facts: Facts, unknown: Set<string>): boolean | null {
  const { fact: name, op, value } = cond;
  const fact = facts[name];
  if (op === "exists") return fact !== undefined && fact !== null;
  if (fact === undefined || fact === null) {
    unknown.add(name);
    return null;
  }
  switch (op) {
    case "eq":
      return norm(fact) === norm(value);
    case "neq":
      return norm(fact) !== norm(value);
    case "in":
    case "not_in": {
      const hit = (value as unknown[]).map(norm).includes(norm(fact));
      return op === "in" ? hit : !hit;
    }
    case "gt":
    case "gte":
    case "lt":
    case "lte": {
      if (!(isNumber(fact) && isNumber(value))) {
        unknown.add(name);
        return null;
      }
      return op === "gt" ? fact > value : op === "gte" ? fact >= value : op === "lt" ? fact < value : fact <= value;
    }
    case "glob":
    case "not_glob":
    case "ends_with":
    case "not_ends_with": {
      if (typeof fact !== "string") {
        unknown.add(name);
        return null;
      }
      if (op === "glob" || op === "not_glob") {
        const hit = new RegExp("^(?:" + globToRegex(String(value)) + ")$", "i").test(fact);
        return op === "glob" ? hit : !hit;
      }
      const hit = fact.toLowerCase().endsWith(String(value).toLowerCase());
      return op === "ends_with" ? hit : !hit;
    }
  }
  throw new Error(`unknown op ${op}`);
}

export function evalCondition(cond: Condition, facts: Facts, unknown: Set<string>): boolean | null {
  if ("all" in cond) {
    const r = cond.all.map((c) => evalCondition(c, facts, unknown));
    if (r.some((x) => x === false)) return false;
    return r.some((x) => x === null) ? null : true;
  }
  if ("any" in cond) {
    const r = cond.any.map((c) => evalCondition(c, facts, unknown));
    if (r.some((x) => x === true)) return true;
    return r.some((x) => x === null) ? null : false;
  }
  if ("not" in cond) {
    const r = evalCondition(cond.not, facts, unknown);
    return r === null ? null : !r;
  }
  return leaf(cond, facts, unknown);
}

export function effectiveFacts(facts: Facts, confidence: Record<string, number> | null | undefined, minConfidence: number): Facts {
  const conf = confidence || {};
  const out: Facts = {};
  for (const [k, v] of Object.entries(facts)) {
    if (v !== null && v !== undefined && v !== "unknown" && (conf[k] ?? 1.0) >= minConfidence) out[k] = v;
  }
  return out;
}

export function evaluate(
  bundle: Bundle,
  facts: Facts,
  confidence?: Record<string, number> | null,
  grant = false,
): Decision {
  const eff = effectiveFacts(facts, confidence, bundle.min_confidence ?? 0.8);
  const sensor = eff.sensor as string | undefined;
  const hits: Hit[] = [];
  const shadow: Hit[] = [];
  for (const rule of bundle.rules) {
    if (rule.sensors && rule.sensors.length && !rule.sensors.includes(sensor as string)) continue;
    const unknown = new Set<string>();
    const result = evalCondition(rule.when, eff, unknown);
    if (result === false) continue;
    let effect: string;
    let outcome: "match" | "unknown";
    if (result === true) {
      effect = rule.effect;
      outcome = "match";
    } else {
      effect = rule.on_unknown ?? "hold";
      outcome = "unknown";
    }
    if (effect === "allow") continue;
    if (grant && effect === "hold") effect = "allow";
    const hit: Hit = {
      rule_id: rule.id,
      effect,
      outcome,
      unknown_facts: outcome === "unknown" ? [...unknown].sort() : [],
    };
    if ((rule.mode ?? "enforce") === "shadow") shadow.push(hit);
    else if (effect !== "allow") hits.push(hit);
  }

  let decision: Decision["decision"] = "allow";
  for (const h of hits) if (SEVERITY[h.effect] > SEVERITY[decision]) decision = h.effect as Decision["decision"];

  const byId = new Map(bundle.rules.map((r) => [r.id, r]));
  let reason = "no policy matched";
  if (decision === "allow" && grant) reason = "approved by a human";
  for (const h of hits) {
    if (h.effect === decision) {
      reason = `${h.rule_id}: ${byId.get(h.rule_id)!.source}`;
      if (h.outcome === "unknown") reason += " (could not determine: " + h.unknown_facts.join(", ") + ")";
      break;
    }
  }
  return {
    decision,
    reason,
    rule_ids: hits.filter((h) => h.effect === decision).map((h) => h.rule_id),
    hits,
    shadow,
    bundle_hash: bundle.hash ?? null,
  };
}
