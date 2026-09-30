"""Fixture backend: fixed answers, for tests and offline demos of the decision tier."""

from __future__ import annotations

from observ.decide.backend import Answer, DecisionState, Question


class FixtureBackend:
    name = "fixture"

    def __init__(self, answers: dict[str, tuple[object, float]]):
        self.answers = answers
        self.calls: list[tuple[DecisionState, list[Question]]] = []

    def ask(self, state: DecisionState, questions: list[Question]) -> dict[str, Answer]:
        self.calls.append((state, questions))
        return {q.id: Answer(*self.answers[q.id]) for q in questions if q.id in self.answers}
