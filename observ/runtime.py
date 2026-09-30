"""Small helpers to run observd / demo services in-process (background threads)."""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import uvicorn

from observ.audit import AuditLog
from observ.compile import bundle as bundle_mod
from observ.decide.backend import DecisionBackend
from observ.engine import Engine

ROOT = Path(__file__).resolve().parent.parent
EXTENSION_DIST = ROOT / "extension" / "dist"
SNAPSHOT_JS = Path(__file__).resolve().parent / "assets" / "snapshot.js"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """A uvicorn server on a background thread."""

    def __init__(self, app, port: int, host: str = "127.0.0.1"):
        self.port, self.host = port, host
        self.server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> "Server":
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                return self
            time.sleep(0.02)
        raise RuntimeError(f"server on :{self.port} did not start")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


def make_engine(bundle_path: str | Path, audit_dir: str | Path, backend: DecisionBackend | None = None) -> Engine:
    return Engine(bundle=bundle_mod.load(bundle_path), audit=AuditLog(audit_dir), backend=backend)


def launch_with_extension(
    playwright,
    user_data_dir: str,
    headless: bool = True,
    extension_dir: Path = EXTENSION_DIST,
    extra_args: list[str] | None = None,
    **kwargs,
):
    """Chromium with the Observ extension; returns once its service worker is up.

    OBSERV_EXTENSION_MODE=load (default) loads extension/dist as an unpacked extension.
    OBSERV_EXTENSION_MODE=policy relies on Chrome enterprise policy (ExtensionSettings
    force_installed), as in the Docker sandbox image: no flags, nothing the agent can drop.
    """
    policy = os.environ.get("OBSERV_EXTENSION_MODE", "load") == "policy"
    if policy:
        args = list(extra_args or [])
        # Playwright's defaults disable extensions and the background networking the
        # extension updater needs to download a force-installed extension.
        kwargs.setdefault("ignore_default_args",
                          ["--disable-extensions", "--disable-background-networking", "--disable-component-update"])
    else:
        if not (extension_dir / "manifest.json").exists():
            raise RuntimeError(f"{extension_dir} missing: run `npm install && npm run build` in extension/")
        args = [f"--disable-extensions-except={extension_dir}", f"--load-extension={extension_dir}", *(extra_args or [])]
    ctx = playwright.chromium.launch_persistent_context(
        user_data_dir,
        channel="chromium",
        headless=headless,
        viewport={"width": 1280, "height": 800},
        args=args,
        **kwargs,
    )
    if policy:
        _wait_for_policy_install(ctx, os.environ.get("PORTAL_URL", "http://127.0.0.1:8765"))
    elif not ctx.service_workers:
        ctx.wait_for_event("serviceworker", timeout=10_000)
    return ctx


def _wait_for_policy_install(ctx, probe_url: str, timeout: float = 60.0) -> None:
    """A force-installed extension arrives a moment after launch, and Playwright doesn't
    surface its service worker. Wait until its page-world patch is live on an http page."""
    page = ctx.new_page()
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            page.goto(probe_url)
            if page.evaluate("HTMLFormElement.prototype.submit.toString().includes('requestSubmit')"):
                return
            time.sleep(1)
        raise RuntimeError("Observ extension was not force-installed (check the Chrome policy)")
    finally:
        page.close()
