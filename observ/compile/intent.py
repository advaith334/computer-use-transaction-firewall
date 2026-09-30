"""The structured intent a plain-language policy decomposes into.

Both the deterministic templates and the Claude decomposer produce this shape; grounding
turns it into an evaluator rule. Values are strings plus a declared type so the model's
structured output stays simple and unambiguous.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SemanticAction = Literal[
    "payment", "payee_change", "email_send", "delete", "login", "navigation", "unknown"
]


class Condition(BaseModel):
    fact: str = Field(description="A fact name from the fact schema.")
    op: str = Field(description="An operator from the op list.")
    value: str = Field(description="The comparison value, written as text.")
    value_type: Literal["string", "number", "list"] = Field(
        description="How to read value: number, a single string, or a comma-separated list."
    )


class VagueTerm(BaseModel):
    term: str = Field(description="The vague phrase exactly as written, e.g. 'large' or 'prod console'.")
    question: str = Field(description="One question whose answer makes the phrase precise.")
    fact: str = Field(description="The fact the answer constrains.")
    op: str = Field(description="The operator the answer is used with.")
    value_type: Literal["string", "number", "list"]


class Intent(BaseModel):
    effect: Literal["block", "hold", "allow"] = Field(
        description="block = never allowed; hold = needs human approval; allow = explicitly allowed."
    )
    semantic_actions: list[SemanticAction] = Field(
        description="Business actions the policy is about. Empty means any committing action."
    )
    conditions: list[Condition]
    vague_terms: list[VagueTerm] = Field(
        description="Phrases that cannot be enforced until the author makes them precise."
    )
    sensors: list[Literal["extension", "harness", "egress", "cdp", "desktop"]] = Field(
        description="Restrict to these sensors. Empty means every sensor."
    )
    untranslatable_reason: str | None = Field(
        description="Why this policy cannot be expressed over the fact schema, or null."
    )
    summary: str = Field(description="One-line restatement of what the rule enforces.")
