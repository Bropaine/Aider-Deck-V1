import { $, ui, el, button, replace, request, modal, closeModal } from "./core.js";

const endpoint = "/ollama/model-install";

function installationNotice(host, response, action) {
  replace(host);
  if (!response.message) return;
  host.append(el("p", response.message, response.blocked ? "warn-text" : "hint"));
  const waiting = response.job?.state === "running";
  if (waiting) host.append(el("p", response.job.message || "Downloading through Ollama…", "hint"));
  if (response.job?.state === "failed" && response.status === "missing") {
    host.append(el("p", response.job.error || "Ollama could not finish the download.", "bad-text"));
  }
  if (response.status === "missing" && !waiting)
    host.append(button("Download through Ollama", () => action("pull")));
  if (response.status === "offline")
    host.append(button("Start Ollama", () => action("start")));
  if (response.blocked && !waiting)
    host.append(button("Check again", () => action("check")));
}

function gateView(host, getModel, target, current) {
  let sequence = 0;
  let timer;
  let last;
  async function check(action = "check") {
    clearTimeout(timer);
    const id = ++sequence;
    const model = getModel();
    const session = ui.state.attachment_session;
    const here = () => id === sequence && current() && model === getModel() && session === ui.state.attachment_session;
    if (!here()) return null;
    const params = { target, action, session };
    if (model != null) params.model = model;
    if (action !== "check") {
      if (!last || last.model !== model && model != null) return null;
      params.route = last.route;
      for (const control of host.querySelectorAll("button")) control.disabled = true;
    }
    try {
      const result = await request(endpoint, "POST", params);
      if (!here()) return null;
      last = result;
      installationNotice(host, result, check);
      if (result.job?.state === "running") timer = setTimeout(() => check(), 1000);
      return result;
    } catch (error) {
      if (here()) {
        last = null;
        replace(host, el("p", error.message, "bad-text"), button("Check again", () => check()));
      }
      return null;
    }
  }
  function invalidate() {
    sequence++;
    clearTimeout(timer);
    last = null;
    replace(host);
  }
  return { check, invalidate };
}

export function installationGate(field, wrap) {
  const host = el("div");
  host.className = "model-install-notice";
  host.setAttribute("aria-live", "polite");
  wrap.append(host);
  const wid = ui.wid;
  const current = () => field.isConnected && wid === ui.wid;
  const value = () => field.value.trim();
  const launch = gateView(host, value, "launch", current);
  const live = gateView(host, value, "live", current);
  let timer, lastValue;
  function changed() {
    if (value() === lastValue) return;
    lastValue = value();
    clearTimeout(timer);
    launch.invalidate();
    live.invalidate();
    if (value() && ui.state.supports_model_install) timer = setTimeout(() => launch.check(), 300);
  }
  field.addEventListener("input", changed);
  field.addEventListener("change", changed);
  changed();
  return {
    liveToken: null,
    async allowLive() {
      if (!ui.state.supports_model_install) return true;
      clearTimeout(timer);
      launch.invalidate();
      const result = await live.check();
      this.liveToken = result?.route;
      return !!result && !result.blocked;
    },
  };
}

export async function launchInstallationCheck() {
  if (!ui.state.supports_model_install) return { allowed: true };
  const wid = ui.wid;
  const generation = ui.state.attachment_session;
  const host = el("div");
  const current = () => ui.wid === wid && ui.state.attachment_session === generation;
  const gate = gateView(host, () => null, "launch", current);
  const result = await gate.check();
  if (!current()) return { allowed: false };
  if (result && !result.blocked) return { allowed: true, route: result.route };
  modal("Local model availability", host, button("Open model settings", () => {
    closeModal();
    $("model-badge").click();
  }), button("Close", closeModal));
  // Keep job polling scoped to this dialog once the owner leaves it.
  $("dialog").addEventListener("close", () => gate.invalidate(), { once: true });
  return { allowed: false };
}
