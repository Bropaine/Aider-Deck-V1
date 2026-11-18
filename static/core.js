export const $ = (id) => document.getElementById(id);
export const ui = {
  wid: null,
  workspaces: [],
  state: {},
  config: null,
  tab: "Settings",
  history: [],
  revision: 0,
};
export function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
}
export function replace(node, ...children) {
  node.replaceChildren(...children.filter(Boolean));
  return node;
}
export function append(node, ...children) {
  node.append(...children.filter(Boolean));
  return node;
}
export const str = (v) =>
  v === undefined || v === null
    ? "Unset"
    : Array.isArray(v)
      ? v.join(", ")
      : typeof v === "object"
        ? JSON.stringify(v)
        : String(v);
export function run(fn) {
  return (...args) =>
    Promise.resolve()
      .then(() => fn(...args))
      .catch((e) => {
        if (e.name !== "AbortError") notify(e.message || "The operation failed.", true);
      });
}
export function button(label, fn, cls = "", title = "") {
  const b = el("button", label, cls);
  b.type = "button";
  b.title = title;
  b.addEventListener("click", run(fn));
  return b;
}
export function input(label, value = "", type = "text") {
  const n = el("input");
  n.type = type;
  n.setAttribute("aria-label", label);
  n.value = value ?? "";
  return n;
}
export function options(node, values, chosen, names = {}) {
  node.replaceChildren();
  for (const v of values) {
    const o = el("option", names[v] || v);
    o.value = v;
    node.append(o);
  }
  if (chosen != null) node.value = chosen;
  return node;
}
export function details(label, ...children) {
  return append(el("details", null, "raw"), el("summary", label), ...children);
}
let timer;
export function notify(message, error = false) {
  if (error && $("dialog").open) {
    const body = $("dialog-body");
    let issue = body.querySelector(".dialog-error");
    if (!issue) {
      issue = el("p", null, "notice dialog-error");
      issue.setAttribute("role", "alert");
      body.prepend(issue);
    }
    issue.textContent = message;
    return;
  }
  const n = $("toast");
  n.hidden = false;
  n.textContent = message;
  n.classList.toggle("error", error);
  clearTimeout(timer);
  timer = setTimeout(() => (n.hidden = true), error ? 12000 : 4000);
}
export async function request(path, method = "GET", body, workspace = true) {
  const wid = ui.wid;
  if (workspace && !wid) throw Error("Open a workspace first.");
  const res = await fetch(workspace ? "/api/" + encodeURIComponent(wid) + path : path, {
    method,
    cache: "no-store",
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const result = await res.json().catch(() => ({}));
  if (workspace && wid !== ui.wid) throw new DOMException("Workspace changed", "AbortError");
  if (!res.ok) {
    const error = Error(result.error || res.statusText || "Request failed");
    error.status = res.status;
    throw error;
  }
  return result;
}
export function modal(title, ...children) {
  const d = $("dialog");
  if (d.open) d.close();
  $("dialog-title").textContent = title;
  const body = replace($("dialog-body"), ...children);
  d.showModal();
  return body;
}
export function closeModal() {
  if ($("dialog").open) $("dialog").close();
}
export function actions(...items) {
  return append(el("div", null, "action-row"), ...items);
}
export function confirmAction(title, message, label, fn, danger = true) {
  modal(
    title,
    el("p", message),
    append(
      el("div", null, "dialog-actions"),
      button("Cancel", closeModal),
      button(
        label,
        async () => {
          await fn();
          closeModal();
        },
        danger ? "danger" : "primary",
      ),
    ),
  );
}
export function askText(title, label, value, submit, fn) {
  const field = input(label, value);
  const go = async () => {
    if (!field.value.trim()) throw Error(label + " is required.");
    await fn(field.value.trim());
    closeModal();
  };
  modal(
    title,
    el("p", label, "hint"),
    field,
    actions(button(submit, go, "primary"), button("Cancel", closeModal)),
  );
  field.className = "full";
  field.onkeydown = run(async (e) => {
    if (e.key === "Enter") await go();
  });
  field.focus();
}
export function rawBlock(report) {
  if (!report) return null;
  if (!report.raw && !report.stale) return null;
  return details(
    report.stale ? "Stale · Show raw Aider output" : "Show raw Aider output",
    el("pre", report.raw || "No output captured.", "preview"),
  );
}
export function diffView(diff) {
  const wrap = el("div");
  if (!diff) return el("p", "No diff to display.", "empty");
  let body = null;
  for (const line of String(diff).split(/\r?\n/)) {
    if (line.startsWith("diff --git ") || !body) {
      const d = el("details", null, "diff-block");
      d.open = true;
      body = el("div");
      append(d, el("summary", line.startsWith("diff --git ") ? line.slice(11) : "Changes"), body);
      wrap.append(d);
    }
    const color =
      line.startsWith("+++") || line.startsWith("---") || line.startsWith("@@")
        ? "head"
        : line.startsWith("+")
          ? "plus"
          : line.startsWith("-")
            ? "minus"
            : "";
    body.append(el("span", line || " ", "diff-line diff-" + color));
  }
  return wrap;
}
export async function command(line, confirmed = false) {
  await request("/command", "POST", { line, confirmed });
  await ui.refresh();
}
