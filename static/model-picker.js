import { actions, append, button, details, el, input, notify, replace, request, run } from "./core.js";

export function pickerGroups(saved, discovered, search = "", warnings = {}) {
  const groups = [], seen = new Set();
  const query = search.trim().toLowerCase();
  function group(label, items) {
    const rows = [];
    for (const item of items) {
      if (seen.has(item.name)) continue;
      seen.add(item.name);
      if (!query || (label + " " + item.name).toLowerCase().includes(query)) rows.push(item);
    }
    if (rows.length) groups.push({ label, rows });
  }
  group("Saved models", saved.map((row) => ({ ...row, saved: true })));
  for (const { label, models } of discovered)
    group(label, models.map((name) => ({ name, enabled: false, saved: false, key_warning: warnings[name] })));
  return groups;
}

export function savedModelPicker(field, config) {
  const picker = el("select");
  picker.id = "saved-model-picker";
  picker.setAttribute("aria-label", "Saved models");
  const status = el("span", "", "hint small");
  status.setAttribute("role", "status");
  const rows = el("div", null, "model-library-rows");
  const filter = input("Filter model list", "", "search");
  filter.id = "model-picker-filter";
  filter.placeholder = "Filter model list";
  let candidates = [], warnings = {}, loading = false, loaded = false, busy = false, revision = 0;
  const refresh = button("Refresh models", loadCandidates);
  refresh.id = "refresh-model-candidates";
  const manage = details("Manage saved models", actions(filter, refresh), status, rows);
  const save = button("Save model", () => update("save", field.value.trim()));
  save.id = "save-model-bookmark";

  function render() {
    replace(picker, el("option", "Choose a saved model…"));
    picker.options[0].value = "";
    for (const item of config.saved_models.filter((row) => row.enabled)) {
      const option = el("option", item.name);
      option.value = item.name;
      picker.append(option);
    }
    picker.value = config.saved_models.some((row) => row.enabled && row.name === field.value)
      ? field.value : "";
    picker.disabled = picker.options.length === 1;
    save.disabled = busy || !field.value.trim();
    renderRows();
  }
  function renderRows() {
    rows.replaceChildren();
    const groups = pickerGroups(config.saved_models, candidates, filter.value, warnings);
    if (!groups.length) rows.append(el("p", "No matching models.", "empty"));
    for (const group of groups) {
      rows.append(el("h4", group.label));
      for (const item of group.rows) {
        const toggle = input("Show " + item.name + " in picker", "", "checkbox");
        toggle.checked = item.enabled;
        toggle.disabled = busy || !!item.key_warning;
        toggle.onchange = run(() => update("visibility", item.name, toggle.checked));
        const line = append(el("div", null, "model-library-row"),
          append(el("label", null, "check"), toggle, el("span", item.name)));
        if (item.saved) {
          const remove = button("Remove", () => update("remove", item.name), "subtle");
          remove.setAttribute("aria-label", "Remove " + item.name);
          remove.disabled = busy;
          line.append(remove);
        }
        rows.append(line);
        if (item.key_warning) {
          const keys = button("Open Keys", () => {
            const tab = Array.from(document.querySelectorAll("#inspector-tabs button"))
              .find((entry) => entry.textContent === "Keys");
            tab?.click();
          }, "subtle");
          rows.append(append(el("div", null, "hint small warn-text"), el("span", item.key_warning + " "), keys));
        }
      }
    }
  }
  async function update(action, model, enabled) {
    if (busy || !model) return;
    const focused = rows.contains(document.activeElement) ? document.activeElement.getAttribute("aria-label") : "";
    revision++;
    busy = true;
    save.disabled = true;
    for (const control of rows.querySelectorAll("input,button")) control.disabled = true;
    try {
      const result = await request("/config/saved-models", "POST", { action, model, enabled });
      config.saved_models = result.saved_models;
      for (const suggestion of Array.from(field.list?.options || [])) {
        if (config.saved_models.some((row) => row.name === suggestion.value && !row.enabled))
          suggestion.remove();
      }
      if (action === "save") {
        const entry = result.saved_models.find((row) => row.name === model);
        notify(entry?.enabled ? "Model saved to this workspace’s picker." :
          "Model saved with visibility off. " + (entry?.key_warning || "Enable it in Manage saved models."));
      }
    } finally {
      busy = false;
      revision++;
      render();
      if (focused && document.activeElement === document.body && field.isConnected) {
        const controls = Array.from(rows.querySelectorAll("button,input"));
        (controls.find((control) => control.getAttribute("aria-label") === focused) || filter).focus();
      }
    }
  }
  async function loadCandidates() {
    if (loading) return;
    loading = true;
    refresh.disabled = true;
    status.textContent = "Loading model lists…";
    try {
      const version = revision;
      const [result, latest] = await Promise.all([
        request("/config/model-candidates"), request("/config/saved-models"),
      ]);
      if (!busy && version === revision) config.saved_models = latest.saved_models;
      candidates = result.groups;
      warnings = result.warnings || {};
      status.textContent = result.local_message || "";
      loaded = true;
      render();
    } catch (error) {
      if (error.name !== "AbortError") status.textContent = error.message;
    } finally {
      refresh.disabled = loading = false;
    }
  }
  manage.addEventListener("toggle", () => {
    if (manage.open && !loaded) loadCandidates();
  });
  filter.oninput = renderRows;
  picker.onchange = () => {
    if (!picker.value) return;
    field.value = picker.value;
    field.dispatchEvent(new Event("change", { bubbles: true }));
  };
  function syncField() {
    picker.value = Array.from(picker.options).some((option) => option.value === field.value)
      ? field.value : "";
    save.disabled = busy || !field.value.trim();
  }
  field.addEventListener("input", syncField);
  field.addEventListener("change", syncField);
  render();
  return append(el("div", null, "model-picker"), actions(picker, save), manage);
}
