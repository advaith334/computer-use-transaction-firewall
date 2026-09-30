// Observ content script (isolated world). Sees every click and form submission in the
// page, however it was produced: Playwright/CDP input, xdotool or VNC pixel clicks, a
// human, or page JS calling form.submit() (patched in main_world.ts). Page scripts can't
// reach these listeners.
//
// Fast path: the bundle is evaluated locally (<1ms). Non-committing actions the bundle
// allows pass untouched; anything the bundle blocks is stopped immediately. Committing
// actions (payments, bank changes, sends, deletes, unclassified POSTs) are paused and sent
// to observd, which records the UI intent the egress sensor correlates against, runs
// the decision tier if needed, and creates holds. Allowed actions are then replayed.

import { COMMIT_ACTIONS, factsFromSnapshot, type Snapshot } from "./facts.ts";
import { evaluate, type Bundle } from "./evaluator.ts";

type ObservGlobal = {
  snapshotFor: (target: Element, event: string, key?: string | null) => Snapshot;
};
const snapshots = (globalThis as unknown as { __observ: ObservGlobal }).__observ;

interface Verdict {
  decision: "allow" | "hold" | "block";
  reason: string;
  hold_id?: string | null;
  error?: string;
}

let bundle: Bundle | null = null;
function loadBundle(): void {
  chrome.runtime.sendMessage({ type: "bundle" }, (b) => {
    if (b && !b.error) bundle = b;
  });
}
loadBundle();

const replayForms = new WeakSet<HTMLFormElement>();
const replayClicks = new WeakSet<Element>();

function publish(v: Verdict): void {
  // Machine-readable outcome for agents and tests, plus a visible banner for humans.
  document.documentElement.setAttribute("data-observ", JSON.stringify(v));
  let bar = document.getElementById("__observ_bar");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "__observ_bar";
    bar.setAttribute("role", "alert");
    bar.style.cssText =
      "position:fixed;left:0;right:0;bottom:0;z-index:2147483647;padding:10px 14px;font:13px/1.4 system-ui;color:#fff;pointer-events:none";
    (document.body || document.documentElement).appendChild(bar);
  }
  const colors = { block: "#b42318", hold: "#b54708", allow: "#067647" };
  bar.style.background = colors[v.decision];
  const head = v.decision === "hold" ? `HELD for approval (${v.hold_id})` : v.decision.toUpperCase();
  bar.textContent = `Observ ${head}: ${v.reason}`;
}

function ask(msg: object): Promise<Verdict> {
  return new Promise((resolve) => chrome.runtime.sendMessage(msg, (r) => resolve(r || { error: "no response" })));
}

async function waitForHold(id: string): Promise<boolean> {
  for (;;) {
    await new Promise((r) => setTimeout(r, 1000));
    const h = (await ask({ type: "hold", id })) as unknown as { status?: string; error?: string };
    if (h.status === "approved") return true;
    if (h.status === "denied") return false;
  }
}

async function decideAndRun(snap: Snapshot, replay: () => void): Promise<void> {
  let v = await ask({ type: "decide", snapshot: snap });
  if (v.error) {
    // observd unreachable: enforce the local bundle; a hold can't be approved offline.
    const local = bundle ? evaluate(bundle, factsFromSnapshot(snap)) : null;
    v = local
      ? { decision: local.decision === "hold" ? "block" : local.decision, reason: local.reason + " [observd offline]" }
      : { decision: "block", reason: "observd unreachable and no policy bundle loaded" };
  }
  publish(v);
  if (v.decision === "allow") return replay();
  if (v.decision === "hold" && v.hold_id) {
    const approved = await waitForHold(v.hold_id);
    publish({ decision: approved ? "allow" : "block", reason: approved ? "approved by a human" : "denied by a human" });
    if (approved) {
      await ask({ type: "consume", id: v.hold_id }); // single-use: this approval is spent here
      replay();
    }
  }
}

function intercept(e: Event, snap: Snapshot, replay: () => void): void {
  if (!bundle) loadBundle();
  const facts = factsFromSnapshot(snap);
  const local = bundle ? evaluate(bundle, facts) : null;
  const committing = COMMIT_ACTIONS.includes(facts.semantic_action as string);
  if (local && local.decision === "allow" && !committing) return; // fast path
  e.preventDefault();
  e.stopImmediatePropagation();
  if (local && local.decision === "block") {
    publish({ decision: "block", reason: local.reason });
    void ask({ type: "decide", snapshot: snap }); // audit it; the local block already stands
    return;
  }
  void decideAndRun(snap, replay);
}

window.addEventListener(
  "submit",
  (e) => {
    const form = e.target as HTMLFormElement;
    if (replayForms.has(form)) {
      replayForms.delete(form);
      return;
    }
    const submitter = (e as SubmitEvent).submitter;
    const snap = snapshots.snapshotFor(submitter || form, "submit");
    intercept(e, snap, () => {
      replayForms.add(form);
      if (submitter && (submitter as HTMLButtonElement).form === form) form.requestSubmit(submitter as HTMLElement);
      else form.requestSubmit();
    });
  },
  true,
);

window.addEventListener(
  "click",
  (e) => {
    const target = e.target as Element;
    if (!(target instanceof Element)) return;
    const snap = snapshots.snapshotFor(target, "click");
    const facts = factsFromSnapshot(snap);
    // Submit buttons are handled by the submit listener; only committing non-form clicks here.
    if (facts.verb !== "click" || facts.semantic_action === "none") return;
    const el = (target.closest("button,a,[role=button],input") as Element) || target;
    if (replayClicks.has(el)) {
      replayClicks.delete(el);
      return;
    }
    intercept(e, snap, () => {
      replayClicks.add(el);
      (el as HTMLElement).click();
    });
  },
  true,
);
