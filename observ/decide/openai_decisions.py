"""OpenAI Decisions API backend (limited preview, announced at DevDay on 2026-09-29).

Public descriptions: questions with a fixed set of answers, text or image context,
~150 ms per decision on a specialised GPT-6 Luna. The request/response schema is not
public yet, so this adapter maps our typed questions onto it once it is. Until then it
fails loudly instead of guessing an API shape.
"""

from __future__ import annotations

import os

from observ.decide.backend import Answer, DecisionState, Question


class OpenAIDecisionsBackend:
    name = "openai_decisions"

    def __init__(self) -> None:
        self.api_key = os.environ.get("OPENAI_API_KEY")

    def ask(self, state: DecisionState, questions: list[Question]) -> dict[str, Answer]:
        # Mapping when the schema is published:
        #   Question(kind="choice")  -> a question with its options as the answer set
        #   Question(kind="bool")    -> a question with answers ["yes", "no"]
        #   state.text / image_png   -> the text and image context
        #   response confidence      -> Answer.confidence (hold below bundle min_confidence)
        raise NotImplementedError(
            "OpenAI Decisions API is in limited preview and its schema is not public; "
            "use OBSERV_DECISION_BACKEND=local or anthropic until it is"
        )
