// Runs in the page's own JS world at document_start. HTMLFormElement.prototype.submit()
// submits without firing a "submit" event, which would let agent-injected JS (e.g. CDP
// Runtime.evaluate("form.submit()")) skip the content script. Route it through
// requestSubmit() so the event fires and Observ sees it.
//
// A determined script can recover the original from a fresh iframe; the egress sensor is
// the backstop for that (a commit with no matching UI intent is blocked).
(() => {
  const proto = HTMLFormElement.prototype;
  Object.defineProperty(proto, "submit", {
    configurable: false,
    writable: false,
    value: function (this: HTMLFormElement) {
      this.requestSubmit();
    },
  });
})();
