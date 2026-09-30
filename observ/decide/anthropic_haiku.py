"""Claude Haiku 4.5 as a typed-decision backend (text + optional screenshot crop).

Confidence here is the model's self-report, constrained by structured output. Treat it
as a routing signal, not a calibrated probability; the bench measures how it behaves.
"""

from __future__ import annotations

import base64
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from observ.decide.backend import Answer, DecisionState, Question

MODEL = "claude-haiku-4-5"


class _Answer(BaseModel):
    question_id: str
    value: str = Field(description="The chosen option, the number, or true/false, as text.")
    confidence: float = Field(description="0 to 1: how sure you are, given only this evidence.")


class _Answers(BaseModel):
    answers: list[_Answer]


def _render(questions: list[Question]) -> str:
    lines = []
    for q in questions:
        opts = f" Options: {', '.join(q.options)}." if q.options else ""
        kind: Literal["choice", "number", "bool"] = q.kind
        lines.append(f"- id={q.id} ({kind}): {q.text}{opts}")
    return "\n".join(lines)


class HaikuBackend:
    name = "anthropic_haiku"

    def __init__(self, client: anthropic.Anthropic | None = None):
        self.client = client or anthropic.Anthropic()

    def ask(self, state: DecisionState, questions: list[Question]) -> dict[str, Answer]:
        content: list[dict] = []
        if state.image_png:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png",
                           "data": base64.standard_b64encode(state.image_png).decode()},
            })
        content.append({
            "type": "text",
            "text": (
                "A computer-use agent is about to perform this action. Answer each question about "
                "what the action does. If the evidence is ambiguous, give your best answer with a "
                f"low confidence.\n\nAction context:\n{state.text}\n\nQuestions:\n{_render(questions)}"
            ),
        })
        response = self.client.messages.parse(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": content}],
            output_format=_Answers,
        )
        if response.stop_reason == "refusal" or response.parsed_output is None:
            return {}
        by_id = {q.id: q for q in questions}
        out: dict[str, Answer] = {}
        for a in response.parsed_output.answers:
            q = by_id.get(a.question_id)
            if q is None:
                continue
            conf = max(0.0, min(1.0, a.confidence))
            if q.kind == "choice":
                if a.value in q.options:
                    out[q.id] = Answer(a.value, conf)
            elif q.kind == "number":
                try:
                    x = float(a.value.replace(",", "").replace("$", ""))
                except ValueError:
                    continue
                out[q.id] = Answer(int(x) if x == int(x) else x, conf)
            else:
                out[q.id] = Answer(a.value.strip().lower() == "true", conf)
        return out
