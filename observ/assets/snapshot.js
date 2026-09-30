// Observ DOM snapshot builder. Single source of truth, shared by:
//   - the browser extension (copied into extension/dist by the build), and
//   - harness adapters that drive a browser (injected with page.evaluate).
// It only *describes* the target of an action; observ/facts.py and extension/src/facts.ts
// turn the description into typed facts.
(function (root) {
  function clean(s, limit) {
    return String(s || "").replace(/\s+/g, " ").trim().slice(0, limit || 120);
  }

  function labelFor(el) {
    if (!el) return "";
    var aria = el.getAttribute && el.getAttribute("aria-label");
    if (aria) return clean(aria);
    if (el.labels && el.labels.length) return clean(el.labels[0].textContent);
    if (el.id) {
      var byFor = el.ownerDocument.querySelector('label[for="' + el.id + '"]');
      if (byFor) return clean(byFor.textContent);
    }
    if (el.tagName === "INPUT" && (el.type === "submit" || el.type === "button")) return clean(el.value);
    if (el.placeholder) return clean(el.placeholder);
    return clean(el.textContent);
  }

  function roleFor(el) {
    var explicit = el.getAttribute && el.getAttribute("role");
    if (explicit) return explicit;
    var tag = el.tagName.toLowerCase();
    if (tag === "button") return "button";
    if (tag === "a") return "link";
    if (tag === "input") {
      var t = (el.type || "text").toLowerCase();
      if (t === "submit" || t === "button") return "button";
      if (t === "checkbox" || t === "radio") return t;
      return "textbox";
    }
    if (tag === "select") return "combobox";
    if (tag === "textarea") return "textbox";
    return tag;
  }

  function fieldValue(f) {
    if (f.tagName === "SELECT") {
      var opt = f.options[f.selectedIndex];
      return opt ? clean(opt.textContent) : "";
    }
    if (f.type === "password") return f.value ? "********" : "";
    return clean(f.value, 500);
  }

  function describeForm(form) {
    if (!form) return null;
    var fields = [];
    var submitLabel = "";
    Array.prototype.forEach.call(form.elements, function (f) {
      var type = (f.type || f.tagName).toLowerCase();
      if (type === "submit" || (f.tagName === "BUTTON" && (f.type || "submit") === "submit")) {
        if (!submitLabel) submitLabel = labelFor(f);
        return;
      }
      if (type === "hidden" || type === "button" || type === "fieldset") return;
      fields.push({ name: f.name || f.id || "", label: labelFor(f), type: type, value: fieldValue(f) });
    });
    return {
      action: form.getAttribute("action") || "",
      method: (form.getAttribute("method") || "get").toLowerCase(),
      submit_label: submitLabel,
      fields: fields,
    };
  }

  // The element the action lands on: nearest interactive ancestor of the raw target.
  function actionable(el) {
    var cur = el;
    while (cur && cur.nodeType === 1) {
      if (/^(BUTTON|A|INPUT|SELECT|TEXTAREA)$/.test(cur.tagName) || (cur.getAttribute && cur.getAttribute("role") === "button")) {
        return cur;
      }
      cur = cur.parentElement;
    }
    return el;
  }

  function snapshotFor(target, event, key) {
    var el = actionable(target);
    var form = el && (el.form || (el.closest && el.closest("form"))) || (target && target.tagName === "FORM" ? target : null);
    return {
      url: (el && el.ownerDocument && el.ownerDocument.location.href) || "",
      event: event,
      key: key || null,
      element: el
        ? {
            tag: el.tagName.toLowerCase(),
            type: (el.getAttribute && el.getAttribute("type")) || "",
            role: roleFor(el),
            label: labelFor(el),
            text: clean(el.textContent, 80),
            name: el.name || el.id || "",
          }
        : {},
      form: describeForm(form),
    };
  }

  function snapshotAt(x, y, event, key) {
    var el = document.elementFromPoint(x, y);
    return snapshotFor(el, event || "click", key);
  }

  function snapshotFocused(event, key) {
    return snapshotFor(document.activeElement || document.body, event || "key", key);
  }

  root.__observ = Object.assign(root.__observ || {}, {
    snapshotFor: snapshotFor,
    snapshotAt: snapshotAt,
    snapshotFocused: snapshotFocused,
  });
})(typeof globalThis !== "undefined" ? globalThis : window);
