# Observ form factor: compile once, enforce anywhere

This note records why Observ is shaped the way it is: a policy compiler, a portable
deterministic evaluator, and a mesh of small sensors chosen by an installer. It comes
from research done on 2026-09-30 (six web research passes plus the team's Speedrun
Alpha notes and the "Deterministic Observability for Computer Agents" meeting). Vendor
facts below come from public docs and press at that date; the bottom section lists the
claims we could not verify.

## The requirement

- Work with as many computer-use agent (CUA) stacks as possible (horizontal).
- Anyone can install it in minutes.
- Configuring it is handing over a list of plain-language policies, and it then works.
- Target customer (decided in the meeting): companies running CUAs across fleets of
  VMs / sandboxes, not consumer machines. The layer is deterministic and the pitch is
  compliance, safety, traceability and trust.

## Finding 1: every model only *proposes* actions

Anthropic (`computer_toolset_20260801` tool_use), OpenAI (`computer_call` with batched
`actions[]`), Gemini 3.5 computer use (`function_call`, 0–999 coordinates) and open
models (UI-TARS/OpenCUA pyautogui text) all hand the action to client code to execute.
So a firewall can sit at the model boundary, the executor boundary, or the device.

## Finding 2: no single chokepoint covers the market

| Layer | Best at | Why it can't be the whole product |
|---|---|---|
| Model API gateway (base_url swap) | Zero-code install | Bypassable; pausing mid-loop is awkward; blind to OpenAI's code-execution mode; commoditized (Portkey went to Palo Alto, Helicone to maintenance mode) |
| CDP proxy | Exact element + form values | Blind to agents that click by pixels (xdotool/VNC) and to extension-based agents |
| Framework hooks / SDK | Agent intent and reasoning | Opt-in, one adapter per framework |
| Network egress proxy | The actual commit, however produced | Can't always tell what a request *means*; blind to SAP GUI, Citrix/RDP (Epic), 3270, local actions; holding sockets is fragile |
| Force-installed browser extension | Every browser action regardless of framework/model, incl. pixel clicks | Only browsers; some hosted browsers disallow custom extensions |
| eBPF | Process/network telemetry | No click/keystroke semantics |

## The design

1. **Policy compiler.** `policies.txt` (one rule per line) → structured intents →
   grounded in a typed fact schema → rule shown back with its source sentence → static
   lint (matches-everything / unreachable) → clarifying questions for vague terms →
   generated allow/block tests → signed bundle. Built-in templates cover the common policy
   pack deterministically; Claude handles free-form policies at authoring time only.
   (Research: single-shot NL→policy generation is ~15% correct; structured, schema-grounded,
   test-driven pipelines reach far higher. AWS AgentCore's NL→Cedar flow is the reference.)
2. **Portable deterministic evaluator** in Python and TypeScript, pinned together by a
   shared conformance suite. Every sensor decides locally in well under a millisecond.
   Uncertain → hold, never fail-open. Decisions replay exactly from the audit log.
3. **Fast decision tier** (Jev, OpenAI Decisions API, Claude Haiku, or a local
   classifier) answers *typed questions* for facts the sensor couldn't derive, only for
   committing actions. It produces facts with a confidence; it never decides.
4. **Sensor mesh**, ordered by coverage per unit of effort: force-installed browser
   extension → harness hooks → egress commit proxy (with UI-intent correlation) → CDP
   proxy → desktop input shim → platform connectors (Copilot Studio webhook, AgentCore
   Lambda interceptor, Claude Code managed hooks) → model gateway for shadow audit.
5. **Installer.** `observ init` detects the stack and picks sensors; fleet paths are base
   images/templates with the extension force-installed by Chrome policy, and (roadmap) a
   Kubernetes operator. Shadow mode by default, then one-command enforce.

## Competitive position (Sept 2026)

- The prompt/tool-call guardrail layer has consolidated into big security vendors:
  Invariant→Snyk, Lakera→Check Point, Protect AI and Portkey→Palo Alto Networks, Prompt
  Security→SentinelOne, Aim→Cato, Pangea→CrowdStrike.
- Nobody funded owns deterministic, customer-written policy enforcement at the
  screen-action level for CUA fleets. Closest: OpenLeash (open source, prosumer, click
  interception at "commitment points"), Decawork (YC S26, learned approval models at the
  tool-call layer), Straiker (agentic-browser firewall), Brex's open-source CrabTrap
  (plain-language policy per HTTP request; no human hold, no UI correlation), Palo Alto
  Prisma Browser (a whole browser that pauses agents for step-up MFA).
- Native model-vendor safety (Anthropic classifiers, OpenAI `pending_safety_checks`,
  Gemini `safety_decision`) is generic safety, not customer business rules.

## Distribution precedents

- One-line installs win developers: Browserbase `connectOverCDP(url)`, Sentry `init()`
  auto-instrumentation, PostHog's AI install wizard (wizard users converted to paid ~5x).
- Self-hosting removes data-residency objections (Langfuse). Screenshots are sensitive.
- Security buyers want deterministic, replayable decisions, tamper-evident logs, SIEM /
  OpenTelemetry export, and native platform hooks (Copilot Studio's external security
  provider: 1 s budget, allow/block).

## What this proof of concept verified

- One `policies.txt` enforced identically by three sensors against five agent styles
  (DOM, pixel, direct HTTP, Anthropic computer-use loop, and a browser with no extension),
  locally and inside a sandbox image where the extension is force-installed by policy.
- Playwright's default flags (`--disable-extensions`, `--disable-background-networking`)
  stop a policy force-install from happening. So an agent that launches its own browser
  with defaults gets no extension, and the egress proxy is what catches it. Sandbox images
  must control how the browser is launched, and the egress backstop is not optional.

## Not verified (treat as leads)

- Exact request/response schemas for OpenAI's Decisions API (limited preview) and Jev
  (early access); adapters in `observ/decide/` fail loudly until those are public.
- Some 2026 model names and acquisition figures came through summarizing tools.
- Whether hosted browsers' extension support behaves like a policy force-install.
