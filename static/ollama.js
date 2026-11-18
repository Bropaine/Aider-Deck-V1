import {
  $,
  ui,
  el,
  append,
  replace,
  button,
  input,
  actions,
  request,
  notify,
  confirmAction,
} from "./core.js";
import {
  canonicalTag,
  modelLibrary,
  modelActions,
  formatBytes,
  progressText,
  requestUse,
} from "./ollama-models.mjs";

const operations = new Map();

export async function renderOllama(sessionStarted) {
  const wid = ui.wid,
    panel = el("div", null, "ollama-panel");
  panel.id = "ollama-panel";
  replace($("inspector-content"), panel);
  const visible = () => panel.isConnected && ui.wid === wid && ui.tab === "Ollama";
  let status = null,
    models = [],
    error = "",
    readError = "",
    unavailable = false;
  let timer,
    fetching = false,
    revision = 0,
    librarySignature,
    jobSignature;
  const pending = () => operations.get(wid) || "";

  const service = el("strong", "Checking Ollama…"),
    endpoint = el("p", "", "mono hint");
  service.id = "ollama-service";
  service.setAttribute("role", "status");
  const issue = el("p", null, "notice");
  issue.id = "ollama-error";
  issue.setAttribute("role", "alert");
  const start = button("Start Ollama", () => submit("start"));
  const refreshButton = button("Refresh Ollama", () => {
    error = "";
    return refresh();
  });
  const connection = append(
    el("section", null, "card"),
    service,
    endpoint,
    actions(start, refreshButton),
    issue,
  );
  const tagField = input("Ollama model tag");
  tagField.id = "ollama-tag";
  tagField.placeholder = "e.g. qwen3:8b";
  tagField.spellcheck = false;
  const tagLabel = el("label", "Ollama model tag");
  tagLabel.htmlFor = tagField.id;
  const save = button("Save tag", () => submit("save", tagField.value));
  const pull = button("Download model", () => submit("pull", tagField.value));
  const use = button("Use in Aider", () => useTag(tagField.value), "primary");
  const controls = append(
    el("section", null, "card"),
    tagLabel,
    tagField,
    actions(save, pull, use),
    el(
      "p",
      "Use in Aider sets this workspace’s main, weak and editor models and saves the local Ollama endpoint.",
      "hint",
    ),
  );
  const jobBox = el("div", null, "card ollama-job");
  jobBox.id = "ollama-job";
  jobBox.setAttribute("role", "status");
  const library = el("div", null, "ollama-library");
  append(panel, connection, controls, jobBox, library);
  tagField.addEventListener("input", updateButtons);

  function updateButtons() {
    const tag = canonicalTag(tagField.value);
    const model = models.find((m) => m.tag === tag) || { tag };
    const allowed = status ? modelActions(status, model, !!pending()) : {};
    save.disabled = !allowed.save;
    pull.disabled = !allowed.download;
    use.disabled = !allowed.use;
    use.title =
      tag && status?.running && !model.installed
        ? "Download this tag before using it in Aider."
        : "";
  }

  function drawLibrary() {
    const signature = JSON.stringify([
      models,
      !!pending(),
      status.running,
      status.cli_available,
      status.job?.state,
    ]);
    if (signature === librarySignature) return;
    librarySignature = signature;
    replace(library);
    for (const saved of [true, false]) {
      const entries = models.filter((m) => m.saved === saved);
      const section = append(
        el("section"),
        el("h3", saved ? "Saved in this workspace" : "Other local models"),
      );
      library.append(section);
      if (!entries.length) {
        section.append(
          el(
            "p",
            saved
              ? "No saved tags."
              : status.running
                ? "No other installed models."
                : "Start Ollama to see installed models.",
            "hint",
          ),
        );
        continue;
      }
      for (const model of entries) {
        const row = el("div", null, "ollama-model");
        row.dataset.ollamaTag = model.tag;
        const state = [
          model.installed ? "Installed" : null,
          model.loaded ? "Loaded" : null,
          formatBytes(model.size),
        ].filter(Boolean);
        if (status.running && !model.installed && !model.loaded) state.push("Download required");
        const allowed = modelActions(status, model, !!pending());
        const choose = button("Use in Aider", () => useTag(model.tag));
        choose.setAttribute("aria-label", "Use " + model.tag + " in Aider");
        choose.disabled = !allowed.use;
        const bookmark = button(
          saved ? "Remove from saved" : "Save tag",
          () => submit(saved ? "remove" : "save", model.tag),
          "subtle",
        );
        bookmark.setAttribute(
          "aria-label",
          saved ? "Remove " + model.tag + " from saved" : "Save " + model.tag,
        );
        bookmark.disabled = !!pending();
        const download = button("Download", () => submit("pull", model.tag));
        download.setAttribute("aria-label", "Download " + model.tag);
        download.disabled = !allowed.download;
        append(
          row,
          el("strong", model.tag, "mono"),
          el("span", state.join(" · "), "hint"),
          actions(choose, model.installed ? null : download, bookmark),
        );
        section.append(row);
      }
    }
  }

  function drawJob() {
    const job = status?.job;
    const signature = JSON.stringify([job, pending()]);
    if (signature === jobSignature) return;
    jobSignature = signature;
    replace(jobBox);
    jobBox.hidden = !job && !pending();
    if (pending()) jobBox.append(el("p", pending(), "hint"));
    if (!job) return;
    const action = job.action === "pull" ? "Download" : "Load";
    const label =
      { running: "in progress", succeeded: "complete", failed: "failed" }[job.state] || job.state;
    jobBox.append(el("strong", action + " " + label + " · " + job.model));
    if (job.message) jobBox.append(el("p", job.message, "hint"));
    if (job.state === "running") {
      const progress = el("progress");
      progress.setAttribute("aria-label", "Ollama job progress");
      if (job.total > 0 && job.completed != null) {
        progress.max = job.total;
        progress.value = job.completed;
      }
      jobBox.append(progress);
    }
    const bytes = progressText(job);
    if (bytes) jobBox.append(el("span", bytes, "mono hint"));
  }

  function paint() {
    if (!visible()) return;
    controls.hidden = !status;
    library.hidden = !status;
    const text = unavailable
      ? "Ollama controls unavailable"
      : status
        ? status.running
          ? "Ollama running"
          : "Ollama stopped"
        : readError
          ? "Ollama status unavailable"
          : "Checking Ollama…";
    if (service.textContent !== text) service.textContent = text;
    endpoint.textContent = status?.endpoint || "";
    start.hidden = !status || status.running;
    start.disabled = !!pending() || !status?.cli_available || status.job?.state === "running";
    const serviceMessages = [status?.error];
    if (status && !status.running && !status.cli_available)
      serviceMessages.push("Install Ollama for Windows to start the local service.");
    const message =
      error ||
      readError ||
      (status?.job?.state === "failed" && (status.job.error || status.job.message)) ||
      serviceMessages.filter(Boolean).join(" ");
    if (issue.textContent !== message) issue.textContent = message;
    issue.hidden = !message;
    models = status ? modelLibrary(status) : [];
    updateButtons();
    drawJob();
    if (status) drawLibrary();
  }

  function schedule() {
    clearTimeout(timer);
    if (visible() && !unavailable)
      timer = setTimeout(
        () => {
          if (!visible()) return;
          if (document.hidden) schedule();
          else refresh();
        },
        pending() || status?.job?.state === "running" ? 650 : 5000,
      );
  }

  async function refresh() {
    if (fetching || !visible()) return;
    const at = revision;
    fetching = true;
    refreshButton.disabled = true;
    try {
      const next = await request("/ollama");
      if (at === revision && visible()) {
        status = next;
        unavailable = false;
        readError = "";
      }
    } catch (e) {
      if (at === revision && visible() && e.name !== "AbortError") {
        unavailable = e.status === 404;
        status = null;
        readError = unavailable
          ? "This Deck server predates the Ollama tab. Open an updated Deck instance to manage local models."
          : e.message;
      }
    } finally {
      fetching = false;
      refreshButton.disabled = false;
      paint();
      schedule();
    }
  }

  async function submit(action, name = "", confirmed = false) {
    if (pending() || !visible()) return;
    const tag = name.trim();
    if (action !== "start" && !tag) return;
    revision++;
    error = "";
    operations.set(
      wid,
      {
        start: "Starting Ollama…",
        pull: "Requesting download…",
        use: "Preparing " + tag + " for Aider…",
        save: "Saving tag…",
        remove: "Removing saved tag…",
      }[action],
    );
    paint();
    schedule();
    try {
      if (action === "save" || action === "remove") {
        const result = await request("/ollama/tags", action === "save" ? "POST" : "DELETE", {
          tag,
        });
        if (status) status = { ...status, saved: result.saved };
      } else if (action === "start") {
        const result = await request("/ollama/start", "POST", {});
        status = { ...status, ...result };
      } else if (action === "pull") {
        const job = await request("/ollama/pull", "POST", { tag });
        if (status) status = { ...status, job };
      } else {
        const result = await request("/ollama/use", "POST", { tag, restart_confirmed: confirmed });
        await sessionStarted();
        if (ui.wid === wid) notify("Aider started with " + result.model + ".");
      }
    } catch (e) {
      if (e.name !== "AbortError") {
        error = e.message;
        if (!visible() && ui.wid === wid) notify(error, true);
        if (e.status === 409 && ui.wid === wid) await ui.refresh();
      }
    } finally {
      revision++;
      operations.delete(wid);
      paint();
      if (visible()) await refresh();
    }
  }

  function useTag(value) {
    if (pending()) return;
    return requestUse(
      value,
      ui.state.status === "live",
      (tag, proceed) =>
        confirmAction(
          "Replace this Aider session?",
          "Load " + tag + " and replace the running Aider process in this workspace?",
          "Replace session",
          proceed,
        ),
      (tag, confirmed) => submit("use", tag, confirmed),
    );
  }

  paint();
  await refresh();
}
