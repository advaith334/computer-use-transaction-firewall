"""`observ init`: detect the agent stack in a project and pick the sensors.

Reads source and manifest files (never executes anything), maps what it finds to the
sensor mesh, writes observ.config.json and prints the wiring for each sensor. The
detections are curated signatures per framework, not free-form guessing.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SCAN_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".json", ".toml", ".txt", ".yaml", ".yml", ".cfg"}
SKIP_DIRS = {"node_modules", ".venv", "venv", ".git", "dist", "build", "__pycache__", ".observ"}
SCAN_NAMES = {"Dockerfile", "requirements.txt", "pyproject.toml", "package.json"}

# name -> (regex over file text, kind, sensors, note)
SIGNATURES: dict[str, tuple[str, str, list[str], str]] = {
    "anthropic-computer-use": (r"computer_(?:toolset_)?20\d{6}|\"computer\".{0,40}display_width|toolset_name.{0,10}computer",
                               "harness", ["harness"], "Anthropic computer-use loop"),
    "openai-computer-use": (r"computer_call|computer_use_preview|\"type\":\s*\"computer\"|ComputerTool\(", "harness", ["harness"],
                            "OpenAI computer-use (Responses API / Agents SDK)"),
    "gemini-computer-use": (r"computer_use\s*=|ComputerUse\(|types\.ComputerUse", "harness", ["harness"],
                            "Gemini computer use"),
    "claude-agent-sdk": (r"claude_agent_sdk|@anthropic-ai/claude-agent-sdk|PreToolUse", "harness", ["harness"],
                         "Claude Agent SDK (PreToolUse hook)"),
    "browser-use": (r"\bbrowser_use\b|from browser_use|\"browser-use\"", "browser", ["extension", "egress"],
                    "Browser Use"),
    "stagehand": (r"@browserbasehq/stagehand|\bStagehand\(", "browser", ["extension", "egress"], "Stagehand"),
    "playwright": (r"\bplaywright\b", "browser", ["extension", "egress"], "Playwright"),
    "puppeteer": (r"\bpuppeteer\b", "browser", ["extension", "egress"], "Puppeteer"),
    "skyvern": (r"\bskyvern\b", "browser", ["extension", "egress"], "Skyvern"),
    "pixel-desktop": (r"\bpyautogui\b|\bxdotool\b|\bpynput\b", "desktop", ["harness", "egress"],
                      "pixel/desktop input (pyautogui/xdotool)"),
    "browserbase": (r"browserbase", "provider", ["extension"], "Browserbase (upload extension via Extensions API)"),
    "steel": (r"steel-sdk|steel\.dev|from steel", "provider", ["extension"], "Steel (extensions API)"),
    "kernel": (r"onkernel|kernel\.sh|from kernel", "provider", ["extension"], "Kernel (auto force-installs webRequest extensions)"),
    "hyperbrowser": (r"hyperbrowser", "provider", ["extension"], "Hyperbrowser (extension upload)"),
    "browser-use-cloud": (r"cloud\.browser-use\.com|BrowserUseCloud|api\.browser-use\.com", "provider", ["cdp"],
                          "Browser Use Cloud (no extension support: use the CDP proxy)"),
    "e2b": (r"\be2b\b|e2b_desktop|@e2b/desktop", "sandbox", ["egress", "harness"], "E2B sandbox (egressProxy + template)"),
    "cua": (r"\bcua\b|trycua|cua-computer", "sandbox", ["harness", "egress"], "Cua (on_computer_call_start callback)"),
    "langgraph": (r"\blanggraph\b", "harness", ["harness"], "LangGraph (interrupt for holds)"),
    "direct-http": (r"requests\.post|httpx\.(?:post|Client\(|AsyncClient)|fetch\([^)]*method:\s*['\"]POST", "code", ["egress"],
                    "direct HTTP from agent code (egress catches code-execution commits)"),
}

WIRING = {
    "extension": "Load extension/dist (force-install via Chrome policy ExtensionSettings in sandbox images, "
                 "or upload to your cloud browser); set managed storage observd_url.",
    "harness": "Wrap your action executor: ComputerUseGuard(ObservClient(), page=page).run(block, execute)",
    "egress": "Run `observ egress` and point the sandbox HTTP(S) proxy at it (install its CA for HTTPS).",
    "cdp": "Connect your agent to the Observ CDP proxy URL instead of the provider's (roadmap: S4).",
}


@dataclass
class Detection:
    name: str
    kind: str
    sensors: list[str]
    note: str
    files: list[str] = field(default_factory=list)


def _files(root: Path):
    for p in root.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.is_file() and (p.suffix in SCAN_SUFFIXES or p.name in SCAN_NAMES) and p.stat().st_size < 1_000_000:
            yield p


def detect(root: str | Path) -> list[Detection]:
    root = Path(root)
    found: dict[str, Detection] = {}
    compiled = {k: re.compile(v[0], re.I | re.S) for k, v in SIGNATURES.items()}
    for p in _files(root):
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        for name, rx in compiled.items():
            if rx.search(text):
                _, kind, sensors, note = SIGNATURES[name]
                d = found.setdefault(name, Detection(name, kind, sensors, note))
                d.files.append(str(p.relative_to(root)))
    return sorted(found.values(), key=lambda d: d.name)


def plan(detections: list[Detection]) -> list[str]:
    sensors: list[str] = []
    for d in detections:
        for s in d.sensors:
            if s not in sensors:
                sensors.append(s)
    # The egress backstop is always recommended for anything that can commit.
    if detections and "egress" not in sensors:
        sensors.append("egress")
    return sensors


def write_config(root: str | Path, detections: list[Detection], sensors: list[str]) -> Path:
    path = Path(root) / "observ.config.json"
    path.write_text(json.dumps({
        "observd_url": "http://127.0.0.1:7777",
        "policies": "policies.txt",
        "sensors": sensors,
        "detected": [{"name": d.name, "kind": d.kind, "files": d.files[:5]} for d in detections],
    }, indent=2) + "\n")
    return path
