"""An Anthropic computer-use loop guarded by the harness sensor (no browser extension).

Replays tool_use blocks from examples/fixtures/anthropic_computer_use.json against a real
browser page, through ComputerUseGuard. Blocked or unapproved actions never execute; the
model would receive an is_error tool_result explaining why. Swap the fixture for live
`client.messages.create(..., tools=[{"type": "computer_toolset_20260801", ...}])` turns and
the guard code is unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from examples.common import PORTAL, Outcome, browser_proxy
from observ.sdk import ObservClient
from observ.sensors.harness_anthropic import ComputerUseGuard

AGENT = "anthropic computer-use (harness)"
FIXTURE = Path(__file__).parent / "fixtures" / "anthropic_computer_use.json"
KEYS = {"return": "Enter", "enter": "Enter", "tab": "Tab", "escape": "Escape"}


def executor(page):
    def execute(action: str, params: dict) -> str:
        if action in ("left_click", "double_click", "triple_click"):
            count = {"left_click": 1, "double_click": 2, "triple_click": 3}[action]
            page.mouse.click(*params["coordinate"], click_count=count)
        elif action == "type":
            page.keyboard.type(params["text"])
        elif action == "key":
            page.keyboard.press(KEYS.get(params["text"].lower(), params["text"]))
        page.wait_for_timeout(300)
        return f"done: {action}"
    return execute


def run(browser, client: ObservClient) -> list[Outcome]:
    episodes = json.loads(FIXTURE.read_text())["episodes"]
    kw = browser_proxy()
    context = browser.new_context(viewport={"width": 1280, "height": 800}, **({"proxy": kw["proxy"]} if kw else {}))
    page = context.new_page()
    guard = ComputerUseGuard(client, page=page, hold_timeout=15)
    results = []
    for ep in episodes:
        page.goto(PORTAL + ep["start"])
        tool_results = []
        held = False
        for turn in ep["turns"]:
            for block in turn["content"]:
                if block["type"] == "tool_use":
                    guard.last_verdict = None
                    tool_results.append(guard.run(block, executor(page)))
                    held = held or (guard.last_verdict or {}).get("decision") == "hold"
        last = tool_results[-1]
        committed = page.query_selector("#result") is not None
        if last.get("is_error"):
            actual = "held->denied" if last["content"].startswith("Held") else "blocked"
        else:
            actual = ("held->approved" if held else "committed") if committed else "no effect"
        results.append(Outcome(AGENT, ep["name"], ep["expected"], actual, last["content"]))
    context.close()
    return results


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=browser_proxy().get("extra_args", []))
        for o in run(browser, ObservClient()):
            print(f"{'✓' if o.ok else '✗'} {o.scenario:55} expected {o.expected:15} got {o.actual}\n    tool_result: {o.detail}")
        browser.close()


if __name__ == "__main__":
    main()
