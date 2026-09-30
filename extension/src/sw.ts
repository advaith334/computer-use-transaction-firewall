// Observ service worker: the only part of the extension that talks to observd.
// The observd URL comes from enterprise managed storage (set by the same Chrome policy
// that force-installs the extension), falling back to localhost.

const DEFAULT_OBSERVD = "http://127.0.0.1:7777";
let bundleCache: { at: number; bundle: unknown } | null = null;

async function observd(): Promise<string> {
  try {
    const managed = await chrome.storage.managed.get("observd_url");
    if (typeof managed.observd_url === "string" && managed.observd_url) return managed.observd_url;
  } catch {
    // managed storage is unavailable outside enterprise policy
  }
  return DEFAULT_OBSERVD;
}

async function call(path: string, init?: RequestInit): Promise<unknown> {
  const res = await fetch((await observd()) + path, init);
  if (!res.ok) throw new Error(`observd ${res.status}`);
  return res.json();
}

async function getBundle(): Promise<unknown> {
  if (bundleCache && Date.now() - bundleCache.at < 30_000) return bundleCache.bundle;
  const bundle = await call("/v1/bundle");
  bundleCache = { at: Date.now(), bundle };
  return bundle;
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  const done = (p: Promise<unknown>) =>
    p.then(sendResponse, (err: Error) => sendResponse({ error: String(err.message || err) }));
  if (msg.type === "bundle") done(getBundle());
  else if (msg.type === "decide")
    done(
      call("/v1/decide", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ sensor: "extension", session: "default", snapshot: msg.snapshot }),
      }),
    );
  else if (msg.type === "hold") done(call(`/v1/holds/${encodeURIComponent(msg.id)}`));
  else if (msg.type === "consume") done(call(`/v1/holds/${encodeURIComponent(msg.id)}/consume`, { method: "POST" }));
  else return false;
  return true; // async response
});
