import {
  $,
  ui,
  el,
  append,
  replace,
  str,
  run,
  button,
  input,
  options,
  details,
  notify,
  request,
  modal,
  closeModal,
  actions,
  rawBlock,
  diffView,
  command,
} from "./core.js";
import { legacyEffortMessage, reasoningEditor, savedRegistrationEffort } from "./reasoning-effort.js";
import { savedModelPicker } from "./model-picker.js";
import { externalLink, modelCatalogLink } from "./model-catalog.js";
import { providerPortal } from "./provider-portals.mjs";
import { installationGate } from "./model-install.js";

let activeReasoning;

const modelRoles = {
  model: { label: "Live main model", property: "model" },
  "weak-model": { label: "Live weak model", property: "weak_model" },
};

export function liveModelLabel(state, key) {
  const { label, property } = modelRoles[key];
  let value = state[property] || "Not yet reported";
  if (state.status !== "live") value = "No live session";
  else if (key === "weak-model" && !Object.hasOwn(state, property))
    value = "Restart the Deck server to see this value";
  return label + " · " + value;
}

export async function loadConfig() {
  const c = await request("/config");
  ui.config = c;
  const editor = c.rows.find((r) => r.key === "editor-edit-format");
  options($("composer-editor-format"), ["", ...Object.keys(c.formats)], editor?.configured ?? "", {
    "": "Inherit",
  });
  return c;
}
export async function editRaw(name, onSaved) {
  const raw = await request("/config/raw/" + encodeURIComponent(name));
  const area = el("textarea", null, "editor");
  area.value = raw.text;
  area.spellcheck = false;
  area.setAttribute("aria-label", name);
  const error = el("p", raw.error || "", "bad-text");
  const save = button(
    "Save file",
    async () => {
      try {
        if (
          raw.invalid &&
          !window.confirm("Replace the entire invalid " + name + " with the text in this editor?")
        )
          return;
        await request("/config/raw/" + encodeURIComponent(name), "PUT", {
          text: area.value,
          revision: raw.revision,
          replace_invalid: !!raw.invalid,
        });
        closeModal();
        await afterWrite();
        await onSaved?.();
        if (name === ".aiderignore" && ui.state.status === "live") {
          modal(
            "Saved .aiderignore",
            el("p", "Refresh Aider’s repository map using the new ignore rules?"),
            actions(
              button(
                "Refresh map",
                async () => {
                  await command("/map-refresh");
                  closeModal();
                },
                "primary",
              ),
              button("Close", closeModal),
            ),
          );
        } else notify("Saved " + name + ".");
      } catch (e) {
        error.textContent = e.message;
      }
    },
    "primary",
  );
  modal(name, area, error, actions(save, button("Cancel", closeModal)));
}
async function afterWrite() {
  await loadConfig();
  await ui.refresh();
  if (ui.tab === "Settings") renderSettings();
}
async function previewChange(payload, current = () => true) {
  const p = await request("/config/preview", "POST", payload);
  if (!current()) return;
  const declaring = payload.kind === "reasoning-support";
  const cancel = button("Cancel", closeModal);
  const confirm = button("Confirm write", async () => {
    const dialog = $("dialog");
    const close = dialog.querySelector(".dialog-heading button");
    const keepOpen = (event) => event.preventDefault();
    dialog.addEventListener("cancel", keepOpen);
    cancel.disabled = confirm.disabled = close.disabled = true;
    let saved = false;
    try {
      await request("/config/apply", "POST", { ...payload, token: p.token });
      saved = true;
      confirm.textContent = "Refreshing settings…";
      // Keep the underlying fields inert until the refresh replaces them.
      await afterWrite();
      const restart = declaring && ui.state.status === "live" ? " Restart Aider to load this change." : "";
      notify("Saved " + p.file + "." + restart);
    } finally {
      dialog.removeEventListener("cancel", keepOpen);
      cancel.disabled = confirm.disabled = close.disabled = false;
      if (saved && confirm.isConnected) closeModal();
    }
  }, "primary");
  modal(
    {
      "reasoning-support": "Enable reasoning effort for this model",
      register: "Register model",
      preset: "Apply preset",
    }[payload.kind],
    el("p", p.file, "hint mono"),
    declaring
      ? el("p", "Review the change for " + payload.model + ". Aider loads it at startup.", "hint")
      : null,
    diffView(p.diff),
    append(
      el("div", null, "dialog-actions"),
      cancel, confirm,
    ),
  );
}

function previewReasoningSupport(model, needsFormat, c) {
  if (needsFormat) return chooseRegistrationFormat(c, model, "reasoning-support");
  return previewChange({ kind: "reasoning-support", model });
}

export function registerModel() {
  const c = ui.config;
  if (!c) throw Error("Load workspace settings first.");
  const model =
    ui.state.model || c.registration?.model || c.rows.find((r) => r.key === "model")?.configured;
  if (!model) throw Error("Choose a main model first.");
  return chooseRegistrationFormat(c, model, "register");
}
async function chooseRegistrationFormat(c, model, kind) {
  const configuredFormat = c.rows.find((r) => r.key === "edit-format")?.configured;
  const select = options(
    el("select"),
    Object.keys(c.formats),
    Object.hasOwn(c.formats, configuredFormat) ? configuredFormat : "diff",
    c.formats,
  );
  for (const opt of select.options) opt.textContent = opt.value + " · " + c.formats[opt.value];
  select.setAttribute("aria-label", "Model default edit format");
  const declare = input("Declare reasoning effort support", "", "checkbox");
  const declaration = append(el("label", null, "check"), declare, "Declare reasoning effort support");
  declaration.hidden = true;
  const status = el("p", "", "hint");
  status.setAttribute("role", "status");
  status.hidden = true;
  const wid = ui.wid;
  const current = () => wid === ui.wid && select.isConnected && $("dialog").open;
  const preview = button(
    "Preview YAML diff",
    () => {
      if (!current()) return;
      const operation = declare.checked ? "reasoning-support" : kind;
      return previewChange({ kind: operation, model, edit_format: select.value }, current);
    },
    "primary",
  );
  modal(
    kind === "register" ? "Register model" : "Enable reasoning effort for this model",
    el("p", model, "mono"),
    el("p", "Choose the format Aider should return to in Code mode.", "hint"),
    select,
    declaration,
    status,
    actions(preview, button("Cancel", closeModal)),
  );
  if (kind !== "register") return;
  try {
    const level = await savedRegistrationEffort(model, c.rows, () => {
      preview.disabled = true;
      status.hidden = false;
      status.textContent = "Checking the saved reasoning level…";
      return request("/config/reasoning-effort?" + new URLSearchParams({ model, target: "launch" }));
    });
    if (!current()) return;
    declaration.hidden = !level;
    declare.checked = !!level;
    status.hidden = !level;
    if (level) status.textContent = "Saved launch effort · " + level;
  } catch (error) {
    if (!current() || error.name === "AbortError") return;
    status.hidden = false;
    if (error.status === 409) {
      status.textContent = "The launch model changed. Refresh Settings and reopen the registration.";
      return;
    }
    status.textContent = error.status === 404
      ? legacyEffortMessage
      : "Could not verify the saved reasoning level. You can still register the edit format.";
  }
  if (current()) preview.disabled = false;
}
function modelSuggestions(field, list, config) {
  let timer = null;
  field.addEventListener("input", () => {
    clearTimeout(timer);
    const q = field.value.trim();
    if (!q) {
      list.replaceChildren();
      return;
    }
    timer = setTimeout(
      run(async () => {
        if (!field.isConnected || field.value.trim() !== q) return;
        // Keep partial IDs (including an accidental paste) out of access logs.
        const data = Array.isArray(config.saved_models)
          ? await request("/config/models", "POST", { q })
          : await request("/config/models?q=" + encodeURIComponent(q));
        if (!field.isConnected || field.value.trim() !== q) return;
        list.replaceChildren(
          ...data.models.filter((name) => !config.saved_models?.some(
            (row) => row.name === name && !row.enabled
          )).map((v) => {
            const o = el("option");
            o.value = v;
            return o;
          }),
        );
      }),
      450,
    );
  });
}
function editorFor(r, c) {
  let field, get, extra;
  const val = r.configured;
  const choices = (r.choices || [])
    .map((v) => (typeof v === "object" ? v.value : v))
    .filter((v) => v !== null && v !== "");
  if (r.type === "toggle") {
    field = options(el("select"), ["", "true", "false"], val == null ? "" : String(val), {
      "": "Inherit",
      true: "On",
      false: "Off",
    });
    get = () => (field.value === "" ? null : field.value === "true");
  } else if (r.key === "model") {
    field = input(r.label, val);
    extra = el("datalist");
    extra.id = "list-" + r.key;
    field.setAttribute("list", extra.id);
    modelSuggestions(field, extra, c);
  } else if (r.type === "select") {
    const format = ["edit-format", "editor-edit-format"].includes(r.key);
    const vals = format ? Object.keys(c.formats) : choices;
    const current = val == null ? "" : String(val);
    const descriptions = Object.fromEntries(
      vals.map((v) => [v, format ? v + " · " + c.formats[v] : v]),
    );
    field = options(
      el("select"),
      Array.from(new Set(["", ...vals, ...(current ? [current] : [])])),
      current,
      { "": "Inherit", ...descriptions },
    );
  } else if (r.type === "list") {
    field = el("textarea");
    field.rows = 3;
    field.placeholder = "One entry per line";
    field.value = Array.isArray(val) ? val.join("\n") : (val ?? "");
    get = () => (field.value.trim() ? field.value.split(/\r?\n/).filter(Boolean) : null);
  } else {
    field = input(
      r.label,
      val,
      r.type === "number" ? "number" : r.key.endsWith("api-key") ? "password" : "text",
    );
    if (r.type === "number") {
      field.step = "any";
      get = () => (field.value === "" ? null : Number(field.value));
    }
    if (r.key.endsWith("api-key")) {
      field.value = "";
      field.autocomplete = "new-password";
      field.placeholder = "Replace stored value";
    }
    if (r.type === "color") {
      extra = input(
        r.label + " color picker",
        /^#[a-f\d]{6}$/i.test(val) ? val : "#ffffff",
        "color",
      );
      extra.oninput = () => (field.value = extra.value);
    }
  }
  field.id = "field-" + r.key;
  field.setAttribute("aria-label", r.label);
  return { field, extra, get: get || (() => field.value.trim() || null) };
}
function settingControls(wrap, r, c) {
  const { field, extra, get } = editorFor(r, c);
  const keyPortal = providerPortal(r.key.toUpperCase().replaceAll("-", "_"));
  const main = r.key === "model";
  const bookmarks = main && Array.isArray(c.saved_models) ? savedModelPicker(field, c) : null;
  const save = button(main ? "Save for next launch" : "Save", async () => {
    const value = get();
    if (r.key.endsWith("api-key") && !value) throw Error("Enter a replacement value first.");
    const out = await request("/config/setting", "POST", { key: r.key, value });
    await afterWrite();
    notify(
      out.message || (out.removed ? "Workspace override removed." : "Workspace setting saved."),
    );
  });
  const controls = actions(save);
  let gate;
  if (r.live) {
    const live = button("Apply live", async () => {
      if (gate && !await gate.allowLive()) return;
      await request("/config/setting", "POST", {
        key: r.key, value: get(), live: true, install_route: gate?.liveToken,
      });
      notify("Sent " + r.live);
      await ui.refresh();
    });
    live.disabled = ui.state.status !== "live";
    live.dataset.liveRequired = "";
    controls.append(live);
  }
  append(
    wrap,
    field,
    extra,
    bookmarks,
    controls,
    main ? modelCatalogLink(field) : null,
    keyPortal && externalLink(...keyPortal),
    modelRoles[r.key] ? el("p", "Next launch · " + (r.configured || "Aider default"), "hint small") : null,
    el(
      "p",
      r.live
        ? "Live command " + r.live + " · Save for the next launch"
        : "Applies on session restart",
      "hint small",
    ),
  );
  if (main) gate = installationGate(field, wrap);
}
function row(r, c) {
  const wrap = el("div", null, "setting-row");
  const title = el("label", r.label);
  title.htmlFor = "field-" + r.key;
  const role = modelRoles[r.key];
  const src = el("span", role ? "Config · " + r.configured_source : r.source, "source");
  src.title = role ? r.configured_path || r.configured_source : r.source_path || r.source;
  append(
    wrap,
    append(el("div", null, "field-heading"), title, src),
    el("p", r.description, "hint"),
  );
  if (role) {
    const readout = el("p", liveModelLabel(ui.state, r.key), "mono live-model-readout");
    readout.id = "live-" + r.key;
    readout.setAttribute("role", "status");
    wrap.append(readout);
  }
  if (r.key === "check-update") {
    append(
      wrap,
      el("p", str(r.value), "mono"),
      button("Check now", async () => {
        const data = await request("/config/update", "POST", {});
        modal("Aider update check", el("pre", data.output, "preview"));
      }),
    );
    return wrap;
  }
  if (str(r.value) !== str(r.configured)) {
    wrap.append(el("p", (role ? "Config-derived · " : "Effective: ") + str(r.value) + " · " + r.source, "hint warn-text"));
  }
  if (r.key === "reasoning-effort") {
    const launchModel = String(c.rows.find((item) => item.key === "model")?.configured || "");
    activeReasoning = reasoningEditor(r, launchModel, afterWrite, (model, needsFormat) =>
      previewReasoningSupport(model, needsFormat, c),
    );
    wrap.append(activeReasoning.node);
  } else settingControls(wrap, r, c);
  const meta = details(
    role ? "Configuration value and source" : "Effective value and source",
    el("p", (role ? "Config-derived · " : "Effective: ") + str(r.value) + " · " + r.source, "hint"),
    el("p", "Configured: " + str(r.configured) + " · " + r.configured_source, "hint"),
    el("p", "Default: " + str(r.default), "hint"),
    el("p", r.flag + " · " + r.env, "hint mono"),
  );
  if (r.source_path) meta.append(el("p", r.source_path, "hint mono"));
  if (r.key === "code-theme") {
    const a = el("a", "Pygments styles");
    a.href = "https://pygments.org/styles/";
    a.target = "_blank";
    a.rel = "noopener";
    meta.append(a);
  }
  wrap.append(meta);
  return wrap;
}
function themeRow(rows) {
  const wrap = el("div", null, "setting-row");
  const dark = rows.find((r) => r.key === "dark-mode"),
    light = rows.find((r) => r.key === "light-mode");
  const s = options(
    el("select"),
    ["default", "dark", "light"],
    dark?.configured ? "dark" : light?.configured ? "light" : "default",
    { default: "Terminal default", dark: "Dark", light: "Light" },
  );
  s.setAttribute("aria-label", "Terminal color mode");
  append(
    wrap,
    el("label", "Terminal color mode"),
    s,
    actions(
      button("Save", async () => {
        if (s.value === "default") {
          await request("/config/setting", "POST", { key: "dark-mode", value: null });
        } else await request("/config/setting", "POST", { key: s.value + "-mode", value: true });
        await afterWrite();
        notify("Saved terminal theme.");
      }),
    ),
    el(
      "p",
      rows.map((r) => r.key + " = " + str(r.value) + " · " + r.source).join(" / "),
      "hint small",
    ),
    el("p", "Applies on session restart", "hint small"),
  );
  return wrap;
}
export function showEffective() {
  const body = modal("Effective settings");
  let previous = "";
  const paint = () => {
    const s = ui.state,
      c = ui.config;
    if (!c || !body.isConnected || $("dialog-title").textContent !== "Effective settings") return;
    const signature = JSON.stringify([s.status, s.model, s.weak_model, s.active_format, s.launch_format, s.settings, c.rows]);
    if (signature === previous) return;
    previous = signature;
    const rawOpen = body.querySelector("details.raw")?.open;
    const table = el("table", null, "effective-table");
    const header = el("tr");
    for (const h of ["Setting", "Config-derived", "/settings launch view"])
      header.append(el("th", h));
    table.append(header);
    const observed = s.settings?.data || {};
    const rows = new Map(c.rows.map((r) => [r.key, r]));
    for (const k of new Set([...Object.keys(observed), ...rows.keys()])) {
      const item = rows.get(k),
        has = Object.hasOwn(observed, k);
      const tr = el("tr");
      if (has && item && str(item.value).toLowerCase() !== str(observed[k]).toLowerCase())
        tr.className = "disagrees";
      append(
        tr,
        el("td", k),
        el("td", item ? str(item.value) : "—"),
        el("td", has ? str(observed[k]) : "—"),
      );
      table.append(tr);
    }
    const live = s.active_format || "UNSET",
      launch = s.launch_format ?? s.launch?.["edit-format"] ?? "Unknown";
    replace(
      body,
      el("h3", "Active format · " + live),
      el("p", "Source: Aider’s latest coder announcement", "hint"),
      ...Object.keys(modelRoles).map(key => el("p", liveModelLabel(s, key), "mono live-model-readout")),
      el("p", "Launch setting: " + launch, live !== launch ? "warn-text" : "hint"),
      el(
        "p",
        s.settings?.stale ? "Last /settings readout is stale." : "Launch options from /settings",
        "hint",
      ),
      rawBlock(s.settings),
      table,
    );
    if (rawOpen && body.querySelector("details.raw")) body.querySelector("details.raw").open = true;
  };
  paint();
  const stop = () => window.removeEventListener("deck-state", paint);
  window.addEventListener("deck-state", paint);
  $("dialog").addEventListener("close", stop, { once: true });
  if (ui.state.status === "live") run(() => command("/settings"))();
}
export function settingsFilterForRender(field, { reviewReasoning, sessionChanged } = {}) {
  const value = field?.value ?? "";
  const previous = field?.dataset.beforeReview;
  if (reviewReasoning) return { value: "reasoning-effort", previous: previous ?? value };
  if (sessionChanged && previous !== undefined) return { value: previous };
  return { value, previous };
}

export function renderSettings(filterAction = {}) {
  const c = ui.config;
  if (!c) return;
  const panel = $("inspector-content"),
    opened = new Set(
      [...panel.querySelectorAll("details.group[open]")].map((d) => d.dataset.group),
    );
  const filter = settingsFilterForRender(panel.querySelector("#filter-settings"), filterAction);
  replace(panel);
  for (const issue of c.issues || []) panel.append(el("p", issue, "notice"));
  const profile = el("section", null, "card");
  if (c.first_run)
    append(
      profile,
      el("h3", "First-run profile"),
      el("p", "Preview the Harbor working profile for this workspace.", "hint"),
    );
  else profile.append(el("h3", "Workspace profile"));
  const presets = options(el("select"), c.presets, c.presets[0]);
  presets.setAttribute("aria-label", "Workspace preset");
  append(
    profile,
    presets,
    actions(button("Preview preset", () => previewChange({ kind: "preset", name: presets.value }))),
  );
  panel.append(profile);
  const overview = el("div");
  overview.id = "config-overview";
  panel.append(overview);
  renderOverview();
  const search = input("Filter settings", filter.value, "search");
  if (filter.previous !== undefined) search.dataset.beforeReview = filter.previous;
  search.id = "filter-settings";
  search.className = "full search";
  search.placeholder = "Filter settings…";
  panel.append(search);
  const groups = el("div");
  panel.append(groups);
  function draw() {
    groups.replaceChildren();
    activeReasoning = null;
    const q = search.value.toLowerCase(),
      grouped = new Map();
    for (const r of c.rows) {
      if (![r.key, r.label, r.group].join(" ").toLowerCase().includes(q)) continue;
      const k = r.type === "color" ? "Advanced colors" : r.group;
      if (!grouped.has(k)) grouped.set(k, []);
      grouped.get(k).push(r);
    }
    for (const [name, items] of grouped) {
      const group = el("details", null, "group");
      group.dataset.group = name;
      group.open =
        !!q || opened.has(name) || (!opened.size && items.some((r) => r.key === "model"));
      group.append(el("summary", name));
      // The theme selector represents both flags even when the filter matches one.
      const colors = c.rows.filter((r) => r.key === "dark-mode" || r.key === "light-mode");
      const firstThemeRow = items.find((r) => colors.includes(r));
      for (const r of items) {
        if (colors.includes(r)) {
          if (r === firstThemeRow) group.append(themeRow(colors));
        } else group.append(row(r, c));
      }
      if (items.some((item) => item.key === "reasoning-effort"))
        group.addEventListener("toggle", refreshReasoning);
      groups.append(group);
    }
    if (!grouped.size) groups.append(el("p", "No matching settings.", "empty"));
    refreshReasoning();
  }
  search.oninput = () => {
    delete search.dataset.beforeReview;
    draw();
  };
  draw();
  if (filterAction.reviewReasoning) search.focus();
  const files = el("section");
  files.append(el("h3", "Configuration files"));
  for (const f of [".aider.conf.yml", ".aider.model.settings.yml", ".aider.model.metadata.json"])
    files.append(button(f, () => editRaw(f), "item-link mono"));
  files.querySelectorAll("button").forEach((b) => b.classList.add("full"));
  panel.append(files);
}
function refreshReasoning() {
  const node = activeReasoning?.node;
  if (node?.isConnected && node.closest("details.group")?.open) activeReasoning.sync();
}
export function renderOverview() {
  refreshReasoning();
  for (const key of Object.keys(modelRoles)) {
    const readout = $("live-" + key);
    if (readout) {
      const label = liveModelLabel(ui.state, key);
      if (readout.textContent !== label) readout.textContent = label;
    }
  }
  const box = $("config-overview");
  if (!box) return;
  const s = ui.state,
    r = s.registration?.model ? s.registration : ui.config?.registration;
  const signature = JSON.stringify([
    r,
    s.status,
    s.active_format,
    s.launch_format,
    s.editor_format,
    s.launch,
  ]);
  if (box.dataset.readout === signature) return;
  box.dataset.readout = signature;
  replace(
    box,
    el("h3", "Session configuration"),
    el(
      "p",
      "Active format · " + (s.active_format || "UNSET"),
      s.active_format ? "mono" : "mono warn-text",
    ),
    el(
      "p",
      "Launch setting · " + (s.launch_format || s.launch?.["edit-format"] || "No launch recorded"),
      "hint",
    ),
    s.editor_format ? el("p", "Observed editor format · " + s.editor_format, "hint") : null,
    r?.model
      ? el(
          "p",
          r.registered
            ? "Registered on disk · " + r.model + " · " + (r.edit_format || "format unset")
            : (r.registered === null ? "Registration unknown · " : "UNREGISTERED MODEL · ") +
                r.model,
          r.registered ? "hint" : "warn-text",
        )
      : null,
    s.status === "live" && r?.registered && r.loaded_at_start === false
      ? el(
          "p",
          "Running session still uses the unregistered model. Restart to load its registration.",
          "warn-text",
        )
      : null,
    actions(
      button("Effective settings", showEffective),
      r?.model && r.registered === false ? button("Register model", registerModel) : null,
    ),
  );
}
export async function renderKeys() {
  const d = await request("/config/keys");
  if (ui.tab !== "Keys") return;
  const panel = replace(
    $("inspector-content"),
    el("h3", "Workspace environment"),
    el(
      "p",
      "Stored in .env. Blank fields keep the saved value. Changes apply on session restart.",
      "hint",
    ),
  );
  for (const issue of d.issues || []) panel.append(el("p", issue, "notice"));
  for (const k of d.keys) {
    const wrap = el("div", null, "key-row"),
      field = input("Replace " + k.name, "", "password");
    field.placeholder = k.masked || "Enter value";
    field.autocomplete = "new-password";
    field.spellcheck = false;
    const portal = providerPortal(k.name);
    append(
      wrap,
      append(
        el("div", null, "field-heading"),
        el("strong", k.name, "small mono"),
        el("span", k.present ? "Set" : "Not set", "pill"),
      ),
      el("p", k.source || "No source", "hint small"),
      k.aider_override
        ? el(
            "p",
            "Aider’s configuration overrides this .env value. See the source above.",
            "hint warn-text",
          )
        : null,
      k.home_present || k.shadows_home
        ? el(
            "p",
            k.shadows_home
              ? "A workspace value shadows the home .env entry."
              : "Saving a workspace value will shadow the home .env entry.",
            "hint warn-text",
          )
        : null,
      field,
      portal && externalLink(...portal),
      actions(
        button("Save", async () => {
          await request("/config/keys", "POST", { values: { [k.name]: field.value } });
          field.value = "";
          await renderKeys();
          await ui.refresh();
          notify("Environment entry saved.");
        }),
        button("Presence + non-empty check", async () => {
          const v = await request("/config/keys/verify", "POST", { name: k.name });
          notify(k.name + " · Presence + non-empty check: " + (v.present ? "set" : "not set"));
        }),
      ),
    );
    panel.append(wrap);
  }
  const name = input("Environment variable name"),
    value = input("Environment variable value", "", "password");
  name.placeholder = "NAME";
  value.placeholder = "Value";
  value.autocomplete = "new-password";
  append(
    panel,
    el("h3", "Add variable"),
    append(
      el("div", null, "key-row"),
      name,
      value,
      actions(
        button("Save variable", async () => {
          if (!name.value.trim() || !value.value) throw Error("Enter a variable name and value.");
          await request("/config/keys", "POST", { values: { [name.value.trim()]: value.value } });
          value.value = "";
          await renderKeys();
          await ui.refresh();
        }),
      ),
    ),
  );
}
