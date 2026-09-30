"""Harness sensor for Anthropic computer-use loops.

Wraps the function that executes Claude's computer tool_use blocks. Before a click or an
Enter key runs, the guard asks Observ; blocked actions come back to the model as an
is_error tool_result carrying the policy reason, so the agent can adapt instead of
retrying blindly.

    guard = ComputerUseGuard(ObservClient(), page=page)     # page: optional Playwright page
    result = guard.run(tool_use_block, execute)              # execute(action, params) -> content

Handles both tool shapes: the per-action tools of the computer toolset (name="left_click",
input={"coordinate": [x, y]}) and the single-tool form (name="computer",
input={"action": "left_click", "coordinate": [x, y]}).

With a Playwright page, the target under the coordinate is described from the DOM (the
same snapshot the extension uses). Without one (a desktop VM), the guard sends a
screenshot crop and lets observd's decision tier classify the action.
"""

from __future__ import annotations

from typing import Any, Callable

from observ.runtime import SNAPSHOT_JS
from observ.sdk import ObservClient

CLICKS = {"left_click", "double_click", "triple_click", "right_click", "middle_click"}
ENTER_KEYS = {"return", "enter", "kp_enter"}
PASSIVE = {"screenshot", "zoom", "mouse_move", "scroll", "wait", "cursor_position", "left_mouse_down",
           "left_mouse_up", "hold_key"}


def normalize(block: Any) -> tuple[str, dict]:
    name = block["name"] if isinstance(block, dict) else block.name
    params = dict((block["input"] if isinstance(block, dict) else block.input) or {})
    if name == "computer":
        return params.pop("action", ""), params
    return name, params


class ComputerUseGuard:
    def __init__(
        self,
        client: ObservClient,
        page: Any = None,
        screenshot: Callable[[tuple[int, int] | None], bytes] | None = None,
        session: str = "default",
        hold_timeout: float = 60.0,
    ):
        self.client, self.page, self.screenshot = client, page, screenshot
        self.session, self.hold_timeout = session, hold_timeout
        self._js = SNAPSHOT_JS.read_text()
        self.last_verdict: dict | None = None  # most recent non-trivial verdict, for callers/tests

    def _snapshot(self, action: str, params: dict) -> dict | None:
        if self.page is None:
            return None
        self.page.evaluate(self._js)
        if action in CLICKS and params.get("coordinate"):
            x, y = params["coordinate"]
            return self.page.evaluate("([x, y]) => __observ.snapshotAt(x, y, 'click')", [x, y])
        return self.page.evaluate("(k) => __observ.snapshotFocused('key', k)", "Enter")

    def check(self, action: str, params: dict) -> dict | None:
        """The verdict for an action, or None for actions that can't commit anything."""
        if action in PASSIVE or action == "type":
            return None
        if action == "key" and str(params.get("text", "")).lower().split("+")[-1] not in ENTER_KEYS:
            return None
        if action not in CLICKS and action != "key":
            return None
        snap = self._snapshot(action, params)
        if snap is not None:
            return self.client.decide(sensor="harness", snapshot=snap, session=self.session)
        coord = tuple(params["coordinate"]) if params.get("coordinate") else None
        image = self.screenshot(coord) if self.screenshot else None
        facts = {"sensor": "harness", "verb": "click" if action in CLICKS else "key", "semantic_action": "unknown"}
        return self.client.decide(sensor="harness", facts=facts, session=self.session, image=image)

    def run(self, block: Any, execute: Callable[[str, dict], Any]) -> dict:
        """Guard and execute one tool_use block; returns the tool_result to send back."""
        tool_use_id = block["id"] if isinstance(block, dict) else block.id
        action, params = normalize(block)
        verdict = self.check(action, params)
        if verdict is not None:
            self.last_verdict = verdict
        if verdict is not None and verdict["decision"] != "allow":
            if verdict["decision"] == "hold":
                status = self.client.wait_for_hold(verdict["hold_id"], self.hold_timeout)
                if status == "approved":
                    self.client.consume(verdict["hold_id"])
                else:
                    return {
                        "type": "tool_result", "tool_use_id": tool_use_id, "is_error": True,
                        "content": f"Held for human approval ({verdict['hold_id']}, {status}): {verdict['reason']}. "
                                   "Do not work around this; wait and retry the same action after approval.",
                    }
            else:
                return {
                    "type": "tool_result", "tool_use_id": tool_use_id, "is_error": True,
                    "content": f"Blocked by Observ policy {verdict['reason']}. This action is not allowed; "
                               "do not retry it or try to achieve it another way.",
                }
        content = execute(action, params)
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
