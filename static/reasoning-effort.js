import {
  actions,
  append,
  button,
  el,
  input,
  notify,
  options,
  replace,
  request,
  ui,
} from "./core.js";
import { externalLink } from "./model-catalog.js";

export const legacyEffortMessage =
  "This Deck server predates model capability lookup. Open an updated Deck server to see the levels.";

// State polling can announce another model while a metadata lookup is in flight.
export function effortLookup(load, paint) {
  let lastKey,
    revision = 0;
  return async (model, target, retry = false) => {
    const key = JSON.stringify([model, target]);
    if (key === lastKey && !retry) return;
    lastKey = key;
    const current = ++revision;
    if (!model) {
      paint({
        model,
        target,
        status: "unknown",
        message: "Choose a main model to look up its levels.",
      });
      return;
    }
    paint({ model, target, status: "loading" });
    let result;
    try {
      result = await load(model, target);
      if (result.model !== model) result = { status: "stale" };
    } catch (error) {
      if (error.name === "AbortError") return;
      if (error.status === 409) result = { status: "stale" };
      else result = {
        status: "unknown",
        message: error.status === 404 ? legacyEffortMessage : "Could not look up this model’s levels.",
      };
    }
    if (current === revision) paint({ ...result, model, target });
  };
}

export function effortChoices(report, saved) {
  const value = report.target === "launch" ? String(saved ?? "") : "";
  if (["loading", "stale"].includes(report.status)) return { kind: report.status };
  if (report.status === "unsupported") return { kind: "unsupported", value };
  if (report.status === "known" && Array.isArray(report.levels)) {
    const levels = [...new Set(report.levels.filter((v) => typeof v === "string" && v.length))];
    if (levels.length)
      return { kind: "select", levels, value, unlisted: !!value && !levels.includes(value) };
  }
  return { kind: "manual", value };
}

export function workspaceEffort(row, root) {
  if (!root || !row.configured_path) return false;
  const slash = (s) => s.replaceAll("\\", "/").replace(/\/$/, "").toLowerCase();
  return slash(row.configured_path) === slash(root) + "/.aider.conf.yml";
}

export async function savedRegistrationEffort(model, rows, lookup) {
  const launch = rows.find((r) => r.key === "model")?.configured;
  const value = String(rows.find((r) => r.key === "reasoning-effort")?.configured ?? "");
  if (!model || model !== launch || !value.trim()) return null;
  const report = await lookup(model);
  const verified = report.model === model && report.status === "known" &&
    Array.isArray(report.levels) && report.levels.includes(value);
  return verified && report.launch_guard?.can_declare === true ? value : null;
}

export function launchGuardNotice(report) {
  const guard = report.launch_guard;
  if (
    report.target !== "launch" ||
    !report.model ||
    !["known", "unknown"].includes(report.status) ||
    !guard?.checked ||
    guard.declared === true
  ) return null;
  const message =
    guard.declared === false
      ? "Aider will ignore the saved reasoning effort at launch. Its model settings need reasoning_effort in accepts_settings."
      : "If Aider warns that it is ignoring reasoning_effort at launch, declare support in this model’s settings.";
  return { message: guard.message || message, canDeclare: guard.can_declare === true };
}

export function reasoningEditor(row, launchModel, afterWrite, previewSupport) {
  const node = el("div");
  const target = el("select");
  target.id = "reasoning-effort-target";
  target.setAttribute("aria-label", "Reasoning effort target");
  const modelLabel = el("p", "", "hint mono");
  const fieldSlot = el("div");
  const guardNotice = el("p", "", "hint warn-text");
  guardNotice.id = "reasoning-effort-launch-guard";
  const status = el("p", "", "hint field-readout");
  status.id = "reasoning-effort-status";
  status.setAttribute("role", "status");
  let liveModel = "",
    scope = "launch",
    field,
    choices,
    report,
    retryDraft;
  const save = button("Save", () => submit(false));
  const live = button("Apply live", () => submit(true));
  const retry = button("Check levels again", () =>
    choices.kind === "stale" ? afterWrite() : sync(true),
  );
  const declare = button("Enable reasoning effort for this model…", () =>
    previewSupport(report.model, report.launch_guard.needs_format),
  );
  append(node, target, modelLabel, fieldSlot, status, guardNotice, actions(save, live, retry, declare));

  function buttons() {
    const waiting =
      ["loading", "stale"].includes(choices.kind) || field.selectedOptions?.[0]?.disabled;
    const unsupported = choices.kind === "unsupported";
    const canClear = choices.value && workspaceEffort(row, ui.state.path);
    save.textContent = unsupported && canClear ? "Clear saved effort" : "Save";
    save.disabled = scope !== "launch" || waiting || (unsupported && !canClear);
    live.disabled = scope !== "live" || waiting || unsupported || !field.value.trim();
    live.hidden = !liveModel;
    retry.textContent = choices.kind === "stale" ? "Refresh settings" : "Check levels again";
    retry.hidden = choices.kind !== "stale" && (choices.kind !== "manual" || !report.model);
  }

  async function submit(applyLive) {
    if (!node.isConnected || (applyLive ? live : save).disabled) return;
    const value = choices.kind === "unsupported" ? null : field.value.trim() || null;
    const out = await request("/config/setting", "POST", {
      key: row.key,
      model: report.model,
      value,
      live: applyLive,
    });
    if (applyLive) {
      notify("Sent " + row.live);
      await ui.refresh();
    } else {
      await afterWrite();
      notify(out.removed ? "Workspace override removed." : "Workspace setting saved.");
    }
  }

  function paint(result) {
    report = result;
    choices = effortChoices(report, row.configured);
    const { kind, value } = choices;
    const guard = launchGuardNotice(report);
    guardNotice.textContent = guard?.message || "";
    guardNotice.hidden = !guard;
    declare.hidden = !guard?.canDeclare;
    const notes = [];
    if (report.resolved_model && report.resolved_model !== report.model)
      notes.push("Model alias resolves to " + report.resolved_model + ".");
    if (kind === "manual") {
      field = input(row.label, retryDraft ?? value);
      field.placeholder = "Enter a provider value";
      notes.push(
        report.message || "The provider did not publish effort levels.",
        "Enter a value manually.",
      );
    } else {
      field = el("select");
      if (kind === "loading" || kind === "stale") {
        options(field, [""], "", { "": kind === "stale" ? "Model changed" : "Checking levels…" });
        field.disabled = true;
        notes.push(
          kind === "stale"
            ? "The selected model changed. Refresh settings to see its levels."
            : "Looking up model capabilities…",
        );
      } else if (kind === "unsupported") {
        options(field, [""], "", { "": "No effort levels" });
        field.disabled = true;
        notes.push(report.message || "This model has no selectable reasoning effort.");
        if (value) {
          notes.push(
            workspaceEffort(row, ui.state.path)
              ? "Workspace setting “" + value + "” can be cleared."
              : "Inherited value “" + value + "” comes from " + row.configured_source + ".",
          );
        }
      } else {
        const chosen = choices.levels.includes(retryDraft) ? retryDraft : value;
        options(field, ["", ...choices.levels], chosen, {
          "": scope === "live" ? "Select a level" : "Inherit",
        });
        if (choices.unlisted) {
          const old = el("option", "Choose a replacement");
          old.value = value;
          old.disabled = true;
          field.append(old);
          field.value = chosen;
          notes.push("Saved value “" + value + "” is outside the listed levels.");
        }
        if (report.message) notes.push(report.message);
      }
    }
    field.id = "field-reasoning-effort";
    field.setAttribute("aria-label", row.label);
    field.setAttribute("aria-describedby", guard ? status.id + " " + guardNotice.id : status.id);
    field.addEventListener("input", buttons);
    field.addEventListener("change", buttons);
    replace(fieldSlot, field);
    status.textContent = notes.join(" ");
    if (report.source && report.source !== "Manual entry") {
      if (notes.length) status.append(" ");
      if (typeof report.source_url === "string" && report.source_url.startsWith("https://")) {
        status.append(externalLink(report.source, report.source_url));
      } else status.append(report.source);
    }
    if (report.catalog?.url.startsWith("https://")) {
      status.append(" · ", externalLink(report.catalog.label, report.catalog.url));
    }
    if (kind !== "loading") retryDraft = undefined;
    buttons();
  }

  const lookup = effortLookup(
    (model, which) =>
      request("/config/reasoning-effort?" + new URLSearchParams({ model, target: which })),
    paint,
  );
  function sync(retryLookup = false) {
    const previous = liveModel;
    liveModel = ui.state.status === "live" ? ui.state.model || "" : "";
    if (!liveModel) scope = "launch";
    if (previous !== liveModel || !target.options.length) {
      options(target, ["launch", "live"], scope, {
        launch: "Next launch · " + (launchModel || "Model unset"),
        live: "Live session · " + liveModel,
      });
      target.hidden = !liveModel;
      modelLabel.hidden = !!liveModel;
      modelLabel.textContent = "Next launch · " + (launchModel || "Model unset");
      if (choices) buttons();
    }
    const model = scope === "live" ? liveModel : launchModel;
    if (retryLookup && choices?.kind === "manual") retryDraft = field.value;
    if (model !== report?.model || scope !== report?.target) retryDraft = undefined;
    return lookup(model, scope, retryLookup);
  }
  target.onchange = () => {
    scope = target.value;
    sync();
  };
  paint({ model: launchModel, target: scope, status: "loading" });
  return { node, sync };
}
