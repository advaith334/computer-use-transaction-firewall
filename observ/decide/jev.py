"""TypeSafe Jev backend (hosted early access, mid-September 2026).

Public descriptions: a request is State + typed Questions (Choice, Score, Noul) and
each answer comes back with a probability and confidence. Text and JSON state only (no
images), so screenshot crops must be described in text first. The endpoint schema is
early-access only, so this adapter fails loudly instead of guessing it.
"""

from __future__ import annotations

import os

from observ.decide.backend import Answer, DecisionState, Question


class JevBackend:
    name = "jev"

    def __init__(self) -> None:
        self.api_key = os.environ.get("JEV_API_KEY")

    def ask(self, state: DecisionState, questions: list[Question]) -> dict[str, Answer]:
        # Mapping when access is granted:
        #   Question(kind="choice") -> Jev Choice over q.options
        #   Question(kind="number") -> Jev Score on an amount rubric
        #   Question(kind="bool")   -> Jev Noul (bounded probability)
        #   state.text              -> Jev State (image_png is not supported by Jev)
        raise NotImplementedError(
            "Jev is in early access and its API schema is not public; "
            "use OBSERV_DECISION_BACKEND=local or anthropic until it is"
        )
