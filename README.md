# Observ

**A transaction firewall for computer-use agents.** Give it a list of plain-language
policies. It checks every click, keystroke and submit an agent makes, and every
committing network request, *before* it executes: allow, block, or hold for a human.
Every decision is logged and can be replayed exactly.

```text
policies.txt ──observ compile──▶ signed bundle ──▶ sensors decide locally (<1 ms)
 "Never change a vendor's       (rules + tests)     ├─ browser extension  (any browser agent, even pixel clickers)
  bank details"                                     ├─ harness hook       (Anthropic/OpenAI/Gemini computer-use loops)
                                                    └─ egress proxy       (the actual commit, however it was produced)
                                                              │
                                              observd: holds · approvals inbox · audit · decision tier
```

This repository is a working proof of concept of the form factor recommended in
[docs/form-factor.md](docs/form-factor.md): **compile once, enforce anywhere.** Horizontal
coverage comes from a portable, deterministic policy and a mesh of small sensors, not
from betting on one chokepoint.

## 3-minute quickstart

```bash
uv sync --all-extras                     # Python 3.12+
(cd extension && npm install && npm run build)
uv run playwright install chromium

uv run observ init examples/             # detect the agent stack, pick sensors
uv run observ compile                    # policies.txt -> bundle.json (review + generated tests)
uv run observ enforce --all              # rules start in shadow mode
uv run observ serve                      # observd on :7777; approvals inbox at http://127.0.0.1:7777/
```

Or run everything end to end: portal, observd, egress proxy, five agents, a scripted
reviewer, then a ledger check and audit replay:

```bash
uv run python examples/demo.py           # --headed to watch
```

## Configure it by writing policies

```text
# policies.txt
Never submit a payment over $5,000 without human approval
Never change a vendor's bank details
Never email anyone outside acme.com
The admin console needs human approval
Block any payment or bank change that doesn't match an on-screen action
```

`observ compile` shows you what each line became, and refuses what it can't enforce:

```text
✓ p1  Never submit a payment over $5,000 without human approval
      HOLD when semantic_action = "payment" AND amount > 5000
        test  HOLD   submit via extension: semantic_action=payment, amount=5001
        test  ALLOW  submit via extension: semantic_action=payment, amount=5000
? p6  Don't let the agent pay large invoices without approval
      ? What payment amount counts as 'large'? (a number, e.g. 5000)
✗ p7  Be careful with anything sensitive
      ! no built-in template matches; set ANTHROPIC_API_KEY to compile it with Claude
```

How compilation works:

- **Templates first.** Common policies (payment thresholds, bank or payee changes, email
  domains, deletes, "X needs approval", UI-intent correlation) compile deterministically
  with no LLM.
- **Claude for the rest.** Anything else goes to Claude (`claude-opus-5-5`, structured
  output) *at authoring time only*. The result is grounded in the fact schema
  ([spec/fact_schema.json](spec/fact_schema.json)), so an invented fact or operator is
  rejected.
- **Clarifying questions.** Vague terms ("large", "prod console") get a question. Answers
  live in `policies.answers.yaml`, or the compiler asks you interactively.
- **Lint.** Rules that match everything or can never fire are rejected.
- **Generated tests.** Every rule gets allow/block tests (`observ test`).
- **Signed bundle.** A tampered bundle won't load.

## Deterministic by design

- The runtime evaluator is pure rule matching with three-valued logic. A fact the
  sensor can't determine (or determines with low confidence) makes the rule UNKNOWN,
  and UNKNOWN means **hold**, never fail-open.
- The Python reference ([observ/evaluator.py](observ/evaluator.py)) and the extension's
  TypeScript port are pinned together by [conformance/vectors.json](conformance/vectors.json).
- Every audit record holds exactly what the evaluator saw, chained by hash:

  ```bash
  uv run observ audit verify     # hash chain intact; N/N decisions replayed exactly
  ```

## The decision tier (Jev, OpenAI Decisions API, Claude, or local)

Some committing actions can't be classified from the DOM (a "Confirm" button over
obfuscated fields). For those, observd asks a fast model **typed questions**
(`semantic_action ∈ {payment, payee_change, …}`, `amount`), and only for facts some rule
needs. The answers become facts with a confidence. Deterministic rules still decide, and
low confidence still means hold. The answers are logged, so replay stays exact.

| Backend | `OBSERV_DECISION_BACKEND` | Status |
|---|---|---|
| Local keyword classifier (nothing leaves the box) | `local` (default) | working |
| Claude Haiku 4.5, text and screenshot crop | `anthropic` | working (needs a key) |
| OpenAI Decisions API | `openai` | adapter stub: preview API, schema not public yet |
| TypeSafe Jev | `jev` | adapter stub: early access, schema not public yet |

## Proof: bench and demo

`uv run observ bench` ([bench/report.md](bench/report.md)) runs 25 labelled actions,
including prompt-injection, mixed-recipient and obfuscated-markup cases:

| config | violations stopped | false stops | human holds | p50 ms |
|---|---|---|---|---|
| no firewall | 0% | 0% | 0 | – |
| rules only | 100% | 31% | 11 | 0.12 |
| rules + local decision tier | 100% | 0% | 7 | 0.13 |

Time from a plain `policies.txt` to the first enforced block is about 1–2 ms (compile,
load and decide, no LLM). Caveat: the scenarios and the local classifier were written
together for this PoC, so treat these numbers as a regression harness, not an
independent benchmark.

`examples/demo.py` (23 scenarios, all passing locally and in Docker):

| agent | sensor that catches it | e.g. |
|---|---|---|
| Playwright, DOM selectors | extension | injected "change Acme's bank details" is **blocked**; $6,000 is **held**, approved, then goes through; `form.submit()` from page JS is blocked |
| Pixel agent, coordinates only | extension | same outcomes with no selectors at all |
| Code-execution agent, direct HTTP | egress proxy | `POST /api/payments` with no on-screen action is **blocked** |
| Anthropic computer-use loop | harness hook | a blocked click returns an `is_error` tool_result with the policy reason |
| Browser with no extension | egress proxy | the bank change is still blocked |

The portal's ledger then confirms that only legitimate commits happened.

## Install paths (sensors)

| Sensor | Covers | How |
|---|---|---|
| **Browser extension** ([extension/](extension/)) | Every browser agent, whatever the framework or model: Browser Use, Stagehand, Playwright/MCP, Skyvern, pixel agents | Force-install via Chrome policy in your sandbox image ([docker/write_policy.py](docker/write_policy.py)), upload to Browserbase/Steel/Kernel/Hyperbrowser/Anchor, or `--load-extension` |
| **Harness hook** ([observ/sensors/harness_anthropic.py](observ/sensors/harness_anthropic.py)) | Computer-use loops, including desktop agents | `ComputerUseGuard(ObservClient(), page=page).run(tool_use_block, execute)` |
| **Egress commit proxy** ([observ/sensors/egress_addon.py](observ/sensors/egress_addon.py)) | The commit itself, however it was produced; correlates it with UI intent | `uv run observ egress`, then point the sandbox's HTTP(S) proxy at it (E2B `egressProxy`, Vercel `forwardURL`, `HTTP_PROXY`) |

Roadmap: a CDP proxy (for Browser Use Cloud and Airtop, which don't accept extensions),
a desktop input shim (Windows/macOS accessibility tree; Citrix, SAP GUI), platform
connectors (Copilot Studio's external security webhook, AgentCore Gateway interceptor,
Claude Code managed hooks), a Kubernetes operator, and a model-gateway shadow mode.

## Docker sandbox (the fleet install)

```bash
docker build -f docker/Dockerfile.sandbox -t observ-sandbox .
docker run --rm observ-sandbox           # runs the demo inside, extension force-installed by policy
```

The image bakes in Chromium with Observ **force-installed by Chrome enterprise policy**
(no flags an agent can drop), plus observd and the egress proxy.

What we verified: a browser launched with Playwright's default flags
(`--disable-extensions`, `--disable-background-networking`) never receives the policy
install. That is why the egress proxy is part of the image, not optional.

## Repository map

```
policies.txt, policies.answers.yaml   the demo policies (+ answers to clarifying questions)
observ/compile/     templates, Claude decomposer, grounding, lint, testgen, signed bundle
observ/evaluator.py deterministic evaluator (reference)   observ/facts.py  snapshot -> facts
observ/engine.py    decision tier, egress correlation, holds, single-use approvals, audit
observ/decide/      DecisionBackend: local, fixture, Claude Haiku, OpenAI Decisions, Jev
observ/observd/     HTTP API + approvals inbox        observ/sensors/  harness, egress
observ/assets/snapshot.js   DOM snapshot builder shared by the extension and harness
extension/          MV3 extension: TypeScript evaluator + facts port, content/page/SW scripts
conformance/        Python-generated vectors the TypeScript port must match
bench/, observ/bench.py     labelled scenarios + report     examples/  portal, agents, demo
spec/               fact schema, action envelope (POST /v1/decide), bundle JSON schemas
docker/             sandbox image with policy force-install       docs/  form-factor research
```

## Tests

```bash
uv run pytest                            # 57 tests: evaluator, compiler, engine, sensors, API, CLI
(cd extension && npm test && npm run typecheck)   # 26 conformance vectors + tsc
```

## Known limits

- The extension replays an approved submit with `requestSubmit()`, which suits standard
  forms. SPA handlers that require user activation may need deny-and-retry instead.
- A script can recover the native `form.submit` from a fresh iframe. The egress proxy is
  the backstop for that (a commit with no UI intent is blocked).
- Egress only gates recognizable commits (payment, bank change, email, delete). HTTPS
  needs the proxy CA installed in the sandbox. Pinned desktop apps, SAP GUI, Citrix and
  3270 terminals are invisible to it.
- The demo uses a single session and localhost HTTP. Holds live in observd's memory.

**macOS dev note:** if `import observ` fails under `uv run`, your system may have marked
`.venv/.../_editable_impl_observ.pth` hidden (Python 3.12 skips hidden `.pth` files).
Run with `PYTHONPATH=$PWD` or `chflags nohidden .venv/lib/python3.*/site-packages/*.pth`.
