"""Claude decomposer for policies no template matches (authoring time only).

The runtime path never calls a model: the output here is grounded, linted, shown back to
the author, and frozen into the signed bundle.
"""

from __future__ import annotations

import json

import anthropic

from observ.compile.intent import Intent
from observ.schema import FACTS, OPS

MODEL = "claude-opus-5-5"

SYSTEM = """You compile one plain-language policy for a computer-use agent firewall into a structured intent.

The firewall sees every proposed on-screen action (click, keystroke, form submit) and every
committing network request, reduced to these typed facts:
{facts}

Operators: {ops}

Rules:
- effect: "block" for never/must-not policies, "hold" when the policy asks for approval,
  review or confirmation, "allow" only for explicit permissions.
- semantic_actions names the business actions the policy is about; leave it empty only if
  the policy is about every committing action.
- Only use facts and operators from the lists above. Never invent a fact.
- If the policy uses a vague phrase whose meaning you would have to guess ("large",
  "sensitive", "prod", "suspicious"), do NOT guess: add it to vague_terms with one precise
  question, the fact it constrains and the operator to use with the answer.
- If the policy cannot be expressed over these facts at all (e.g. it is about the agent's
  reasoning, or about data the firewall cannot see), set untranslatable_reason and leave
  the rest minimal.
- For email domain policies, "outside acme.com" means recipient_domain neq "acme.com" AND
  recipient_domain not_ends_with ".acme.com".
"""


def decompose(line: str, client: anthropic.Anthropic | None = None) -> Intent:
    client = client or anthropic.Anthropic()
    system = SYSTEM.format(
        facts=json.dumps(FACTS, indent=1),
        ops=json.dumps(OPS, indent=1),
    )
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": f"Policy: {line}"}],
        output_format=Intent,
        output_config={"effort": "high"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        return Intent(
            effect="hold", semantic_actions=[], conditions=[], vague_terms=[], sensors=[],
            untranslatable_reason="the model declined to compile this policy",
            summary="",
        )
    return response.parsed_output
