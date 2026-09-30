"""The fast decision tier: typed questions -> answers with confidence.

Decision models *produce facts; they never decide*. The engine asks a backend only for
facts a rule needs that the sensor could not derive deterministically, and only for
committing actions. Answers land in the facts with a confidence; below the bundle's
min_confidence they count as unknown, which the evaluator turns into a hold.

Backends: local (keyword classifier, no data leaves the box), fixture (tests),
anthropic_haiku (Claude Haiku 4.5), openai_decisions and jev (preview APIs; adapters
wait on public docs).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal, Protocol

from observ.schema import SEMANTIC_ACTIONS


@dataclass(frozen=True)
class Question:
    id: str  # the fact this answers, e.g. "semantic_action"
    kind: Literal["choice", "number", "bool"]
    text: str
    options: tuple[str, ...] = ()


@dataclass
class Answer:
    value: object
    confidence: float


@dataclass
class DecisionState:
    """What the backend may look at: text context and, optionally, a screenshot crop."""

    text: str
    image_png: bytes | None = None
    extra: dict = field(default_factory=dict)


class DecisionBackend(Protocol):
    name: str

    def ask(self, state: DecisionState, questions: list[Question]) -> dict[str, Answer]: ...


QUESTIONS: dict[str, Question] = {
    "semantic_action": Question(
        id="semantic_action",
        kind="choice",
        text="What does this action do if it goes through?",
        options=tuple(a for a in SEMANTIC_ACTIONS if a != "unknown"),
    ),
    "amount": Question(
        id="amount",
        kind="number",
        text="How much money (a number, no currency symbol) does this action commit? Answer 0 if none.",
    ),
}


def backend_from_env() -> DecisionBackend | None:
    name = os.environ.get("OBSERV_DECISION_BACKEND", "local")
    if name in ("", "none", "off"):
        return None
    if name == "local":
        from observ.decide.local import LocalKeywordBackend

        return LocalKeywordBackend()
    if name == "anthropic":
        from observ.decide.anthropic_haiku import HaikuBackend

        return HaikuBackend()
    if name == "openai":
        from observ.decide.openai_decisions import OpenAIDecisionsBackend

        return OpenAIDecisionsBackend()
    if name == "jev":
        from observ.decide.jev import JevBackend

        return JevBackend()
    raise ValueError(f"unknown OBSERV_DECISION_BACKEND {name!r}")
