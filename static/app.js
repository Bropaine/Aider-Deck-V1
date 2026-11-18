import {
  $,
  ui,
  el,
  append,
  replace,
  run,
  button,
  input,
  options,
  notify,
  request,
  modal,
  closeModal,
  actions,
  askText,
  confirmAction,
  rawBlock,
  command,
} from "./core.js";
import {
  loadConfig,
  renderSettings,
  renderKeys,
  editRaw,
  registerModel,
  renderOverview,
} from "./settings.js";
import { renderGit, resetGit, renderPrompts } from "./panels.js";
import { renderOllama } from "./ollama.js";
import { launchInstallationCheck } from "./model-install.js";
import { FileSelection, legacyDropIssue } from "./file-selection.mjs";
import { attachmentEstimates, contextUsage } from "./context-usage.mjs";
import { attachmentSendIssue, bindAttachments, renderAttachments } from "./attachments.mjs";
import { bindLayout } from "./layout.mjs";

const modes = ["code", "ask", "architect", "context", "help"];
const cap = (s) => (s ? String(s)[0].toUpperCase() + String(s).slice(1) : "—");
const drafts = new Map(),
  workspaceSelections = new Map(),
  expanded = new Set();
const newFileSelections = () => ({ file: new FileSelection(), chat: new FileSelection() });
let selections = newFileSelections();
let historyPosition = -1,
  trackedTab = "Settings",
  treeVersion = 0,
  lastFileReadout = "",
  lastUsage = "",
  opening = 0;
let term = null,
  fit = null,
  socket = null,
  retry = null,
  reconnectAttempt = 0,
  outputSeen = false;
let statePending = null,
  gitTimer = null,
  argCommand = "",
  activeEpoch = 0,
  inspectorSeq = 0;
let replayWrite = null;
let inputEvent = null;
let modeRequest = null;
let sentGeometry = null;
let layout;

// ConPTY owns the terminal protocol. xterm emits automatic DA/DSR and color
// replies on the same callback as keyboard input. Passing those replies to
// Aider can insert escape sequences into the next prompt, even on startup.
function terminalReport(data) {
  return /^\x1b(?:\[[?=>]?[0-9;:]*[cRnt]|\][\s\S]*(?:\x07|\x1b\\)|P[\s\S]*\x1b\\)$/.test(data);
}
function sessionLive() {
  return ui.state.status === "live";
}
function onClick(id, fn) {
  $(id).addEventListener("click", run(fn));
}
function fitTerminal() {
  if (!term || !fit || !$("terminal").offsetHeight) return;
  try {
    fit.fit();
  } catch {
    return;
  }
  if (
    socket?.readyState === WebSocket.OPEN &&
    (socket !== sentGeometry?.socket ||
      term.cols !== sentGeometry.cols ||
      term.rows !== sentGeometry.rows)
  ) {
    socket.send(JSON.stringify({ type: "resize", cols: term.cols, rows: term.rows }));
    sentGeometry = { socket, cols: term.cols, rows: term.rows };
  }
}
function focusTerminal() {
  if (!term || socket?.readyState !== WebSocket.OPEN) {
    notify("Connecting the terminal…");
    return;
  }
  term.scrollToBottom();
  term.focus();
}
const integrity = {
  "xterm.js": "sha384-M169f14mRZOXm3hD/v2Ti0ThIT/RnAQagXA9nlE15yHAtrW19gdePJh/HaTzUOe/",
  "addon-fit.js": "sha384-iF+jqbuti4XlB64clWgFWYEscb+UnSRv3VgVikGYZu+otNFnSHr7y7NcKfBnGizn",
};
function script(url, ready, timeout = 3000) {
  return new Promise((resolve, reject) => {
    const node = document.createElement("script");
    node.src = url;
    node.async = true;
    if (url.startsWith("https:")) {
      node.crossOrigin = "anonymous";
      node.integrity = integrity[url.split("/").pop()];
    }
    let settled = false;
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      node.onload = node.onerror = null;
      if (ok) resolve();
      else {
        node.remove();
        reject(Error("Could not load " + url));
      }
    };
    const timer = setTimeout(() => finish(false), timeout);
    node.onload = () => finish(ready());
    node.onerror = () => finish(false);
    document.head.append(node);
  });
}
async function library(cdn, local, ready) {
  try {
    await script(cdn, ready);
  } catch {
    await script("/static/vendor/" + local, ready, 8000);
  }
}
async function initTerminal() {
  await Promise.all([
    library(
      "https://cdn.jsdelivr.net/npm/@xterm/xterm@5.5.0/lib/xterm.js",
      "xterm.js",
      () => !!window.Terminal,
    ),
    library(
      "https://cdn.jsdelivr.net/npm/@xterm/addon-fit@0.10.0/lib/addon-fit.js",
      "addon-fit.js",
      () => !!window.FitAddon,
    ),
  ]);
  term = new window.Terminal({
    cursorBlink: true,
    scrollback: 12000,
    convertEol: false,
    fontFamily: '"IBM Plex Mono", Consolas, monospace',
    fontSize: 13,
    allowProposedApi: false,
    theme: {
      background: "#0e1216",
      foreground: "#d4dfdc",
      cursor: "#8de6c1",
      selectionBackground: "#41574f",
      green: "#8de6c1",
      yellow: "#edc480",
    },
  });
  fit = new window.FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open($("terminal"));
  // Input events dispatch synchronously. Preserve genuine keystrokes and paste
  // even if the asynchronous parser is still draining a large replay.
  for (const name of [
    "keydown",
    "keypress",
    "input",
    "paste",
    "compositionend",
    "wheel",
    "mousedown",
    "mouseup",
    "mousemove",
    "pointerdown",
    "pointerup",
  ]) {
    $("terminal").addEventListener(
      name,
      (event) => {
        inputEvent = event;
      },
      { capture: true, passive: true },
    );
  }
  term.onData((data) => {
    if (!inputEvent?.eventPhase && (replayWrite || terminalReport(data))) return;
    if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "input", data }));
  });
  new ResizeObserver(() => fitTerminal()).observe($("terminal-shell"));
  fitTerminal();
  maybeConnect();
}
function disconnect() {
  clearTimeout(retry);
  retry = null;
  sentGeometry = null;
  modeRequest = null;
  reconnectAttempt = 0;
  if (socket) {
    socket.onclose = null;
    socket.onmessage = null;
    socket.close();
    socket = null;
  }
}
function maybeConnect() {
  if (
    !ui.wid ||
    !term ||
    socket ||
    retry ||
    ui.state.status === "idle" ||
    ui.state.status === "restarting"
  )
    return;
  if (ui.state.status === "exited" && outputSeen) return;
  const wid = ui.wid,
    ws = new WebSocket(
      (location.protocol === "https:" ? "wss://" : "ws://") +
        location.host +
        "/ws/" +
        encodeURIComponent(wid),
    );
  socket = ws;
  ws.onopen = () => {
    if (ws !== socket) return;
    reconnectAttempt = 0;
    fitTerminal();
  };
  ws.onmessage = (e) => {
    if (ws !== socket || wid !== ui.wid) return;
    let msg;
    try {
      msg = JSON.parse(e.data);
    } catch {
      return;
    }
    if (msg.type === "output") {
      if (msg.reset) {
        const replay = {};
        replayWrite = replay;
        term.reset();
        term.write(msg.data || "", () => {
          if (replayWrite === replay) replayWrite = null;
        });
      } else if (msg.data) term.write(msg.data);
      if (msg.data) {
        outputSeen = true;
        $("terminal-empty").hidden = true;
        scheduleGit();
      }
    }
    if (msg.type === "error")
      notify(msg.error || msg.message || "Terminal connection failed.", true);
  };
  ws.onclose = () => {
    if (ws !== socket) return;
    socket = null;
    if (sessionLive() && ui.wid === wid) {
      retry = setTimeout(
        () => {
          retry = null;
          maybeConnect();
        },
        Math.min(5000, 500 * (1 + reconnectAttempt++)),
      );
    }
  };
}
function scheduleGit() {
  if (ui.tab !== "Git") return;
  clearTimeout(gitTimer);
  gitTimer = setTimeout(
    run(() => renderGit()),
    700,
  );
}
async function workspaceList() {
  const result = await request("/api/workspaces", "GET", undefined, false);
  ui.workspaces = result.workspaces;
  workspaceTabs();
  return result;
}
function workspaceTabs() {
  const area = $("workspace-tabs"),
    live = ui.workspaces.filter((w) => w.status === "live" || w.status === "restarting");
  area.hidden = live.length < 2;
  replace(area);
  if (!area.hidden)
    for (const w of live) {
      const b = button(
        w.path.split(/[\\/]/).filter(Boolean).pop() || w.path,
        async () => activate(await request("/api/workspaces", "POST", { path: w.path }, false)),
        w.id === ui.wid ? "selected" : "",
        w.path,
      );
      area.append(b);
    }
}
async function browse(initial) {
  const seq = ++opening,
    recents = await workspaceList();
  const field = input("Workspace folder", initial || ui.state.path || "");
  field.placeholder = "Folder path";
  field.className = "full mono";
  const list = el("div", null, "picker-list"),
    message = el("p", null, "hint"),
    drives = el("div", null, "action-row");
  let current = "",
    parent = "",
    nav = 0;
  const load = async (path) => {
    const requestNo = ++nav;
    let data;
    try {
      data = await request(
        "/api/browse?path=" + encodeURIComponent(path || ""),
        "GET",
        undefined,
        false,
      );
    } catch (e) {
      if (requestNo === nav) message.textContent = e.message;
      return;
    }
    if (requestNo !== nav || seq !== opening) return;
    current = data.path;
    parent = data.parent;
    field.value = current || "";
    replace(drives, ...(data.drives || []).map((d) => button(d, () => load(d))));
    replace(list);
    message.textContent = data.dirs.length ? "Folders" : "No subfolders";
    for (const d of data.dirs) list.append(button("▸  " + d.name, () => load(d.path), "", d.path));
  };
  const open = async (path) => {
    const w = await request("/api/workspaces", "POST", { path }, false);
    closeModal();
    await activate(w);
    await workspaceList();
  };
  const recentList = el("div", null, "picker-list");
  for (const w of recents.workspaces) recentList.append(button(w.path, () => open(w.path)));
  const openBtn = button("Open workspace", () => open(field.value.trim()), "primary");
  modal(
    "Open workspace",
    el("p", "Workspace directory", "hint"),
    field,
    actions(
      button("Browse path", () => load(field.value)),
      button("Parent folder", () => load(parent || current)),
    ),
    drives,
    message,
    list,
    recents.workspaces.length ? el("h3", "Recent workspaces") : null,
    recentList,
    actions(openBtn, button("Cancel", closeModal)),
  );
  field.onkeydown = run(async (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      await load(field.value);
    }
  });
  await load(field.value);
}
async function activate(w) {
  if (ui.wid === w.id) {
    if (!ui.config) await refreshWorkspace();
    return;
  }
  if (ui.wid) drafts.set(ui.wid, $("prompt").value);
  closeModal();
  disconnect();
  if (term) term.reset();
  outputSeen = false;
  ui.wid = w.id;
  activeEpoch++;
  if (!workspaceSelections.has(w.id)) workspaceSelections.set(w.id, newFileSelections());
  selections = workspaceSelections.get(w.id);
  replace($("tree"));
  statePending = null;
  ui.config = null;
  ui.state = { path: w.path, status: w.status || "idle" };
  historyPosition = -1;
  expanded.clear();
  lastFileReadout = lastUsage = "";
  resetGit();
  ui.setPrompt(drafts.get(w.id) || "");
  $("argument-form").hidden = true;
  $("workspace-path").textContent = w.path;
  $("workspace-path").title = w.path;
  replace($("inspector-content"), el("p", "Loading workspace…", "empty"));
  renderState();
  await refreshWorkspace();
}
async function refreshWorkspace() {
  const outcomes = await Promise.allSettled([refreshState(), loadConfig(), loadTree()]);
  for (const x of outcomes)
    if (x.status === "rejected" && x.reason.name !== "AbortError") notify(x.reason.message, true);
  await showTab(trackedTab);
  renderState();
  workspaceTabs();
  maybeConnect();
}
async function refreshState() {
  if (!ui.wid) return;
  if (statePending) return statePending;
  const epoch = activeEpoch;
  const wait = (async () => {
    const s = await request("/state");
    if (epoch !== activeEpoch) return;
    const sessionChanged = ui.state.attachment_session && s.attachment_session &&
      ui.state.attachment_session !== s.attachment_session;
    ui.state = s;
    ui.history = s.history || [];
    if (sessionChanged && $("filter-settings")?.dataset.beforeReview !== undefined)
      renderSettings({ sessionChanged: true });
    renderState();
    maybeConnect();
    window.dispatchEvent(new Event("deck-state"));
  })();
  statePending = wait;
  try {
    await wait;
  } finally {
    if (statePending === wait) statePending = null;
  }
}
ui.refresh = refreshState;
function notices() {
  const s = ui.state,
    r = s.registration?.model ? s.registration : ui.config?.registration,
    box = $("notices");
  const signature = JSON.stringify([r, s.status, s.active_format, s.launch_format, s.pending, s.ignored_effort_model, s.model]);
  if (box.dataset.readout === signature) return;
  box.dataset.readout = signature;
  replace(box);
  if (r?.registered === null) {
    box.append(
      el("strong", "Registration unknown"),
      button("Inspect model settings", () => editRaw(".aider.model.settings.yml")),
    );
  } else if (r?.model && !r.registered) {
    append(
      box,
      el("strong", "UNREGISTERED MODEL"),
      el("span", r.model, "mono"),
      button("Register…", registerModel),
    );
  } else if (sessionLive() && r?.model && r.loaded_at_start === false) {
    box.append(
      el("strong", "UNREGISTERED MODEL · running session"),
      el("span", "Registration saved on disk"),
    );
  }
  if (s.active_format && s.launch_format && s.active_format !== s.launch_format) {
    box.append(
      el("span", "Format differs · launched " + s.launch_format + " → active " + s.active_format),
    );
  }
  if (sessionLive() && s.ignored_effort_model && s.ignored_effort_model === s.model) {
    box.append(
      el("span", "Aider skipped the saved reasoning effort for " + s.model + " at startup."),
      button("Review reasoning effort", async () => {
        layout?.showInspector();
        await showTab("Settings", { reviewReasoning: true });
      }),
    );
  }
  if (s.pending) box.append(el("span", "Restart to apply pending changes"));
  box.hidden = !box.children.length;
}
function chatFiles(estimates) {
  const s = ui.state,
    f = s.files,
    key = JSON.stringify([f, sessionLive(), f?.data?.map((file) => estimates.get(file))]);
  if (key === lastFileReadout) return;
  lastFileReadout = key;
  const wrap = replace($("chat-files")),
    files = f?.data || [];
  if (sessionLive() && f?.stale === false)
    selections.chat.retainOnly(new Set(files.map((file) => file.path)));
  renderFileSelection("chat");
  $("chat-count").textContent = f && !f.stale ? files.length : "—";
  if (!files.length)
    wrap.append(
      el("p", f && !f.stale ? "No files in chat." : "Run /ls to inspect attached files.", "empty"),
    );
  for (const item of files) {
    const row = el("div", null, "file-row");
    row.append(
      selectionCheckbox("chat", item.path, "Select " + item.path + " to drop"),
      el("span", item.path, "file-name"),
      el("span", item.read_only ? "Read-only" : "Editable", "pill"),
    );
    if (estimates.has(item)) {
      const count = el("small", "~" + estimates.get(item).toLocaleString() + " tokens", "file-token-estimate");
      count.title = "Aider's estimated token count for this attachment";
      row.append(count);
    }
    wrap.append(row);
    const drop = button("Drop", () => submitSelectedFiles("drop", item.path), "row-action");
    drop.dataset.dropFile = "";
    drop.disabled = !sessionLive() || selections.chat.busy;
    const controls = actions(
      button(
        item.read_only ? "Make editable" : "Make read-only",
        () => fileAction(item.read_only ? "add" : "read-only", item.path, true),
        "row-action",
      ),
      drop,
    );
    wrap.append(controls);
  }
  const raw = rawBlock(f);
  if (raw) wrap.append(raw);
}
function usage(context, estimates) {
  const s = ui.state,
    t = s.tokens,
    d = context.data,
    largest = [...estimates].sort((a, b) => b[1] - a[1]).slice(0, 3),
    key = JSON.stringify([context, t, s.usage, largest]);
  if (key === lastUsage) return;
  lastUsage = key;
  const wrap = replace($("usage"));
  function stat(label, value, missing = "—") {
    wrap.append(
      append(
        el("div", null, "stat-row"),
        el("span", label),
        el("span", value == null ? missing : value.toLocaleString()),
      ),
    );
  }
  stat(context.replyEstimate ? "Last reply · sent + received" : "Aider token estimate", context.used);
  if (context.source || context.local)
    stat(context.source || "Ollama loaded window", context.max, "Unknown");
  if (context.local && context.configured !== null && context.configured !== context.max)
    stat("Configured input limit", context.configured);
  stat("Estimated tokens remaining", context.remaining, "Unknown");
  for (const [label, value] of [["Files", d.files], ["History", d.history], ["Repo map", d.repo_map], ["System", d.system]])
    stat(label, value);
  if (largest.length) {
    wrap.append(el("strong", "Largest attachments", "small"));
    for (const [file, count] of largest) stat(file.path, "~" + count.toLocaleString());
  }
  if (s.usage) wrap.append(el("p", "Last observed · " + s.usage, "hint"));
  const raw = rawBlock(t);
  if (raw) wrap.append(raw);
}
function renderState() {
  const s = ui.state,
    live = sessionLive(),
    status = s.status || "idle",
    has = !!ui.wid;
  $("model").textContent = s.model || "—";
  $("model").title = s.model || "No live model announcement";
  $("mode").textContent = cap(s.mode);
  $("format").textContent = s.active_format || "UNSET";
  $("format").className =
    !s.active_format || s.active_format.includes("whole") ? "warn-text" : "good-text";
  const context = contextUsage(s);
  const estimates = attachmentEstimates(s.files, context.data.file_rows);
  const { max, used, percent } = context;
  const label = used == null ? "—" : (context.replyEstimate ? "~" : "") + used.toLocaleString();
  const reference = context.local && context.source && context.source !== "Ollama loaded window"
    ? " · " + context.source : "";
  $("context").textContent = label + (max ? " / " + max.toLocaleString() : "")
    + (context.percentLabel ? " · " + context.percentLabel : "") + reference;
  $("context-meter").hidden = percent == null;
  $("context-meter").value = percent == null ? 0 : Math.min(1, used / max);
  $("context-meter").setAttribute("aria-label", "Estimated context used");
  $("context").classList.toggle("warn-text", percent !== null && used / max > 0.7);
  const estimate = context.replyEstimate ? "Last reply · sent + received" : "Aider token estimate";
  $("context-badge").title = [estimate, context.source, "Click to run /tokens"].filter(Boolean).join(" · ");
  $("branch").textContent = s.git?.branch ? "⑂ " + s.git.branch + (s.git.dirty ? " ●" : "") : "";
  $("branch").title = s.git?.dirty ? "Working tree has changes" : "Git branch";
  $("status").textContent =
    cap(status) + (status === "exited" && s.exit_code != null ? " (" + s.exit_code + ")" : "");
  $("status").className = "status " + status;
  $("start").textContent = live ? "Restart session" : "Start session";
  $("start").disabled = !has || status === "restarting";
  $("start").classList.toggle("primary", !live);
  $("enter-sends").checked = s.prefs?.enter_sends !== false;
  const wanted = modeRequest?.mode || s.pending_mode;
  const modeNotice = $("mode-request");
  let modeMessage = "";
  if (live && wanted) {
    const reason =
      {
        waiting: "answer Aider’s terminal question to continue.",
        starting: "waiting for the first chat prompt.",
        typing: "finish your current terminal input to continue.",
        busy: "waiting for Aider’s next chat prompt.",
      }[s.input_state] || "waiting for Aider to confirm the switch.";
    modeMessage = cap(wanted) + " requested · " + reason;
  }
  if (modeNotice.textContent !== modeMessage) modeNotice.textContent = modeMessage;
  modeNotice.hidden = !modeMessage;
  const waiting = live && s.input_state === "waiting";
  $("terminal-question").hidden = !waiting;
  if (waiting) {
    const message = s.input_prompt || "Aider is waiting for a terminal answer.";
    if ($("terminal-question-text").textContent !== message)
      $("terminal-question-text").textContent = message;
  }
  for (const b of $("mode-buttons").children) {
    b.classList.toggle("selected", b.dataset.mode === s.mode);
    b.classList.toggle("requested", live && b.dataset.mode === wanted);
    b.setAttribute("aria-pressed", String(b.dataset.mode === s.mode));
  }
  for (const id of [
    "refresh-files",
    "refresh-tokens",
    "interrupt",
    "send",
    "once",
    "context-badge",
  ])
    $(id).disabled = !live;
  for (const b of document.querySelectorAll("[data-live-required]")) b.disabled = !live;
  renderAttachments();
  $("ignore-edit").disabled = !has;
  renderFileSelection("file");
  $("enter-sends").disabled = !has;
  $("composer-editor-format").disabled = !has;
  $("terminal-empty").hidden = live || outputSeen;
  if (!live && !outputSeen) {
    $("empty-heading").textContent = !has
      ? "Open a workspace"
      : status === "exited"
        ? "Session exited"
        : status === "restarting"
          ? "Starting Aider…"
          : "Start an Aider session";
    $("empty-body").textContent =
      s.error || (!has ? "Choose a project folder to start an Aider session." : s.path || "");
    $("empty-action").textContent = !has ? "Choose folder" : "Start session";
    $("empty-action").disabled = status === "restarting";
  }
  notices();
  chatFiles(estimates);
  usage(context, estimates);
  renderOverview();
}
async function sessionStarted() {
  disconnect();
  outputSeen = false;
  if (term) term.reset();
  if (statePending) await statePending;
  await refreshState();
  await loadConfig();
  if (ui.tab === "Settings") renderSettings({ sessionChanged: true });
  await workspaceList();
}
async function start() {
  if (!ui.wid) return browse();
  const checkedWorkspace = ui.wid;
  const checkedSession = ui.state.attachment_session;
  const launch = async (restart) => {
    if (checkedWorkspace !== ui.wid || checkedSession !== ui.state.attachment_session) return;
    $("start").disabled = true;
    try {
      const availability = await launchInstallationCheck();
      if (!availability.allowed || checkedWorkspace !== ui.wid) return;
      await request("/session/" + (restart ? "restart" : "start"), "POST", {
        install_route: availability.route,
      });
      await sessionStarted();
    } finally {
      $("start").disabled = false;
    }
  };
  if (sessionLive())
    modal("Restart session?",
      el("p", "Stop the current Aider process and start a new session using the workspace configuration?"),
      append(el("div", null, "dialog-actions"), button("Cancel", closeModal), button("Restart session", () => {
        closeModal();
        return launch(true);
      }, "danger")));
  else await launch(false);
}
function codeGuard(action, label) {
  const r = ui.state.registration || ui.config?.registration;
  if (!r || !r.registered || r.loaded_at_start === false) {
    confirmAction(
      r?.registered === null ? "Registration unknown" : "UNREGISTERED MODEL",
      "Aider may select whole-file editing for " +
        (ui.state.model || "this model") +
        ". The active format will be shown after Aider announces it.",
      label,
      action,
      false,
    );
  } else return action();
}
async function setMode(mode) {
  const change = async () => {
    const token = { mode };
    modeRequest = token;
    renderState();
    try {
      await request("/mode", "POST", { mode });
      // A poll that started before the click may still contain the previous mode.
      if (statePending) await statePending;
      await refreshState();
    } finally {
      if (modeRequest === token) {
        modeRequest = null;
        renderState();
      }
    }
  };
  if (mode === "code") await codeGuard(change, "Switch to Code");
  else await change();
}
function grow() {
  const field = $("prompt");
  field.style.height = "auto";
  field.style.height = Math.min(field.scrollHeight + 2, Math.max(70, innerHeight * 0.2)) + "px";
}
ui.setPrompt = (text) => {
  $("prompt").value = text;
  historyPosition = -1;
  grow();
};
async function send(once = null) {
  const text = $("prompt").value;
  if (!text.trim()) return;
  if (!sessionLive()) throw Error("Start a session first.");
  const submit = async () => {
    const issue = attachmentSendIssue();
    if (issue) throw Error(issue);
    const body = { text, once };
    if (ui.state.attachment_session) {
      body.attachment_session = ui.state.attachment_session;
      body.attachment_ids = (ui.state.attachments || []).map((item) => item.id);
    }
    await request("/send", "POST", body);
    if ($("prompt").value === text) ui.setPrompt("");
    await refreshState();
    $("prompt").focus();
  };
  if (once === "code") await codeGuard(submit, "Send once in Code");
  else await submit();
}
async function fileAction(action, path, fromChat = false) {
  if (!sessionLive()) throw Error("Start a session first.");
  await request("/files", "POST", { action, path, from_chat: fromChat });
  await refreshState();
}
async function checkLegacyDrop(paths, isCurrent) {
  // A poll issued before the click might describe a previous attachment list.
  if (statePending) await statePending;
  for (let attempt = 0; attempt < 12 && isCurrent(); attempt++) {
    await refreshState();
    if (!isCurrent()) return;
    const { files, input_state, path } = ui.state;
    if (files?.stale === false && (!input_state || input_state === "ready")) {
      const issue = legacyDropIssue(paths, files, path);
      if (issue) throw Error(issue);
      return;
    }
    // A previous /drop may still be completing its native /ls. A human
    // question or draft needs the owner's input and cannot make progress here.
    if (input_state === "waiting" || input_state === "typing") break;
    await new Promise((resolve) => setTimeout(resolve, 125));
  }
  if (isCurrent())
    throw Error(
      "Wait for Aider’s chat prompt, then choose Refresh files and tree and retry the drop.",
    );
}
function selectionCheckbox(scope, path, label) {
  const selection = selections[scope],
    pick = input(label, path, "checkbox");
  pick.checked = selection.paths.has(path);
  pick.onchange = () => {
    if (selection !== selections[scope]) return;
    selection.choose(path, pick.checked);
    renderFileSelection(scope);
  };
  return pick;
}
function renderFileSelection(scope, syncCheckboxes = false) {
  const selection = selections[scope],
    count = selection.paths.size,
    chat = scope === "chat";
  const badge = $(scope + "-selection-count");
  badge.hidden = !ui.wid;
  if (badge.textContent !== count + " selected") badge.textContent = count + " selected";
  $(scope + "-selection-actions").hidden = !count;
  for (const id of chat ? ["drop-selected"] : ["add-selected", "add-selected-readonly"]) {
    const b = $(id);
    b.disabled = !sessionLive() || selection.busy;
    b.title = sessionLive() ? "" : "Start a session to manage selected files.";
  }
  if (chat)
    for (const b of $("chat-files").querySelectorAll("[data-drop-file]"))
      b.disabled = !sessionLive() || selection.busy;
  const message = $(scope + "-selection-message");
  message.hidden = !selection.message;
  if (message.textContent !== selection.message) message.textContent = selection.message;
  if (syncCheckboxes)
    for (const box of $(chat ? "chat-files" : "tree").querySelectorAll("input[type=checkbox]"))
      box.checked = selection.paths.has(box.value);
}
async function submitSelectedFiles(action, singlePath) {
  if (!sessionLive()) throw Error("Start a session first.");
  const dropping = action === "drop",
    scope = dropping ? "chat" : "file",
    selection = selections[scope],
    epoch = activeEpoch,
    wid = ui.wid;
  const isCurrent = () => wid === ui.wid && epoch === activeEpoch;
  const supportsGroups =
    (dropping ? ui.state.supports_group_drop : ui.state.supports_file_groups) === true;
  const accepted = await selection.submit(
    action,
    singlePath == null && supportsGroups,
    (body) => request("/api/" + encodeURIComponent(wid) + "/files", "POST", body, false),
    isCurrent,
    () => {
      if (selection === selections[scope]) renderFileSelection(scope, true);
    },
    {
      singlePath,
      checkLegacy:
        dropping && !supportsGroups ? (paths) => checkLegacyDrop(paths, isCurrent) : undefined,
    },
  );
  if (accepted && isCurrent()) await refreshState();
}
function bindFileSelection(scope) {
  const chat = scope === "chat",
    count = el("span", "0 selected", "hint small"),
    bar = el("div", null, "file-selection-actions"),
    message = el("p", null, "hint warn-text");
  count.id = scope + "-selection-count";
  count.setAttribute("role", "status");
  bar.id = scope + "-selection-actions";
  bar.hidden = true;
  message.id = scope + "-selection-message";
  message.setAttribute("role", "alert");
  message.hidden = true;
  const clear = button(
    chat ? "Clear drop selection" : "Clear selection",
    () => {
      selections[scope].clear();
      renderFileSelection(scope, true);
    },
    "subtle",
  );
  if (chat) {
    const drop = button("Drop selected", () => submitSelectedFiles("drop"));
    drop.id = "drop-selected";
    append(bar, drop, clear);
    $("chat-files").before(count, bar, message);
  } else {
    const filters = el("div", null, "tree-options"),
      ignored = $("show-ignored").closest("label");
    const add = button("Add selected", () => submitSelectedFiles("add")),
      read = button("Add selected read-only", () => submitSelectedFiles("read-only"));
    add.id = "add-selected";
    read.id = "add-selected-readonly";
    ignored.before(filters);
    append(filters, ignored, count);
    append(bar, add, read, clear);
    $("tree").before(bar, message);
  }
}
async function loadTree() {
  const version = ++treeVersion,
    root = $("tree");
  if (!ui.wid) {
    replace(root, el("p", "Open a workspace to browse files.", "empty"));
    return;
  }
  const sub = async (path, parent) => {
    const d = await request(
      "/tree?path=" +
        encodeURIComponent(path) +
        "&ignored=" +
        ($("show-ignored").checked ? "1" : "0"),
    );
    if (version !== treeVersion) return;
    replace(parent);
    if (!d.entries.length) parent.append(el("p", "No visible entries.", "hint small"));
    for (const item of d.entries) {
      const row = el("div", null, "tree-row" + (item.ignored ? " ignored" : "")),
        kids = el("div", null, "tree-node");
      const open = async () => {
        if (!item.directory) {
          await fileAction("add", item.path);
          return;
        }
        if (expanded.has(item.path)) {
          expanded.delete(item.path);
          kids.hidden = true;
        } else {
          expanded.add(item.path);
          kids.hidden = false;
          await sub(item.path, kids);
        }
        label.textContent = (expanded.has(item.path) ? "▾ " : "▸ ") + item.name;
      };
      const label = button(
        (item.directory ? (expanded.has(item.path) ? "▾ " : "▸ ") : "") + item.name,
        open,
        "name mono",
        item.path,
      );
      const plus = item.directory
        ? button(
            "＋",
            () =>
              askText(
                "Add matching files",
                "Glob pattern inside " + item.path,
                "*",
                "Add matching",
                (pattern) => fileAction("add", item.path + "/" + pattern),
              ),
            "row-action",
            "Add all matching…",
          )
        : button("R", () => fileAction("read-only", item.path), "row-action", "Add read-only");
      if (!item.directory) {
        row.append(selectionCheckbox("file", item.path, "Select " + item.path));
      }
      append(row, label, plus);
      parent.append(row);
      if (item.directory) {
        parent.append(kids);
        kids.hidden = !expanded.has(item.path);
        if (!kids.hidden) await sub(item.path, kids);
      }
    }
  };
  await sub("", root);
}
async function showTab(tab, filterAction) {
  const version = ++inspectorSeq;
  const switched = ui.tab !== tab;
  if (switched) {
    replace($("inspector-content"));
    resetGit();
  }
  ui.tab = trackedTab = tab;
  for (const b of $("inspector-tabs").children) {
    b.classList.toggle("selected", b.textContent === tab);
    b.setAttribute("aria-current", b.textContent === tab ? "page" : "false");
  }
  if (!ui.wid) {
    replace(
      $("inspector-content"),
      el("p", "Open a workspace to inspect " + tab.toLowerCase() + ".", "empty"),
    );
    return;
  }
  try {
    if (tab === "Settings") {
      if (!ui.config || switched) await loadConfig();
      if (version === inspectorSeq) renderSettings(filterAction);
    } else if (tab === "Keys") await renderKeys();
    else if (tab === "Ollama") await renderOllama(sessionStarted);
    else if (tab === "Git") await renderGit(true);
    else await renderPrompts();
  } catch (e) {
    if (version === inspectorSeq && e.name !== "AbortError")
      replace(
        $("inspector-content"),
        el("p", e.message, "notice"),
        button("Retry", () => showTab(tab)),
      );
  }
}
function quickButtons() {
  const quick = ["tokens", "map", "settings", "diff"],
    more = [
      "undo",
      "lint",
      "test",
      "run",
      "git",
      "web",
      "copy-context",
      "save",
      "load",
      "commit",
      "map-refresh",
      "models",
      "ls",
      "multiline-mode",
    ];
  const arg = {
    run: "Command",
    git: "Git arguments",
    web: "URL",
    save: "Filename",
    load: "Filename",
    models: "Model name or fragment",
    commit: "Message (optional)",
  };
  for (const name of [...quick, ...more]) {
    const b = button("/" + name + (name in arg ? " …" : ""), async () => {
      document.querySelectorAll("details.menu[open]").forEach((d) => (d.open = false));
      if (name in arg) {
        argCommand = "/" + name;
        $("argument-label").textContent = argCommand;
        $("argument-input").placeholder = arg[name];
        $("argument-input").required = name !== "commit";
        $("argument-input").value = "";
        $("argument-form").hidden = false;
        $("argument-input").focus();
      } else if (name === "undo")
        confirmAction("Aider /undo", "Undo the last commit created by Aider?", "Send /undo", () =>
          command("/undo"),
        );
      else await command("/" + name);
    });
    b.dataset.liveRequired = "";
    $(quick.includes(name) ? "quick-actions" : "more-actions").append(b);
  }
  const destructive = {
    clear: "Clear this session’s conversation history?",
    reset: "Drop every file in chat and clear this session’s entire conversation history?",
    exit: "Exit the running Aider process and end this live session?",
  };
  for (const [name, desc] of Object.entries(destructive)) {
    const b = button(
      "/" + name,
      () => {
        document.querySelectorAll("details.menu[open]").forEach((d) => (d.open = false));
        confirmAction("Send /" + name + "?", desc, "Send /" + name, () =>
          command("/" + name, true),
        );
      },
      "danger",
    );
    b.dataset.liveRequired = "";
    $("session-menu").append(b);
  }
  $("session-menu").prepend(button("Restart session…", start));
}
function bind() {
  layout = bindLayout(grow);
  // Older running Flask instances may still serve the previous template.
  if (!$("focus-answer")) {
    const question = el("div", null, "terminal-question"),
      text = el("span"),
      focus = el("button", "Answer in terminal");
    question.id = "terminal-question";
    question.hidden = true;
    text.id = "terminal-question-text";
    text.setAttribute("role", "status");
    focus.id = "focus-answer";
    focus.type = "button";
    $("terminal-shell").after(append(question, text, focus));
  }
  if (!$("mode-request")) {
    const message = el("p", null, "mode-request");
    message.id = "mode-request";
    message.setAttribute("role", "status");
    message.hidden = true;
    $("mode-buttons").after(message);
  }
  bindFileSelection("file");
  bindFileSelection("chat");
  bindAttachments(async () => {
    // A poll issued before a mutation can still contain the old attachment list.
    if (statePending) await statePending;
    await refreshState();
  });
  $("server-address").textContent = location.host;
  for (const m of modes) {
    const b = button(cap(m), () => setMode(m));
    b.dataset.mode = m;
    b.dataset.liveRequired = "";
    $("mode-buttons").append(b);
    const o = el("option", cap(m));
    o.value = m;
    $("once").append(o);
  }
  quickButtons();
  for (const tab of ["Settings", "Keys", "Ollama", "Git", "Prompts"])
    $("inspector-tabs").append(button(tab, () => showTab(tab)));
  onClick("workspace-picker", () => browse(ui.state.path));
  onClick("empty-action", start);
  onClick("start", start);
  onClick("model-badge", async () => {
    layout?.showInspector();
    await showTab("Settings");
    const model = $("field-model");
    if (model) {
      model.closest("details").open = true;
      model.focus();
      model.scrollIntoView({ block: "nearest" });
    }
  });
  onClick("context-badge", () => command("/tokens"));
  onClick("refresh-files", async () => {
    await command("/ls");
    await loadTree();
  });
  onClick("refresh-tokens", () => command("/tokens"));
  onClick("ignore-edit", () => editRaw(".aiderignore", loadTree));
  onClick("interrupt", () => {
    // Keep Ctrl+C behind keystrokes that are already en route to this terminal.
    if (socket?.readyState === WebSocket.OPEN)
      socket.send(JSON.stringify({ type: "input", data: "\x03" }));
    else return request("/interrupt", "POST", {});
  });
  onClick("focus-answer", focusTerminal);
  onClick("send", () => send());
  $("dialog").addEventListener("click", (e) => {
    if (e.target === $("dialog") && e.offsetX < 0) closeModal();
  });
  document.addEventListener("click", (e) => {
    for (const d of document.querySelectorAll("details.menu[open]"))
      if (!d.contains(e.target)) d.open = false;
  });
  $("show-ignored").onchange = run(loadTree);
  $("once").onchange = run(async () => {
    const mode = $("once").value;
    $("once").value = "";
    if (mode) await send(mode);
  });
  $("composer-editor-format").onchange = run(async () => {
    await request("/config/setting", "POST", {
      key: "editor-edit-format",
      value: $("composer-editor-format").value || null,
    });
    await loadConfig();
    await refreshState();
    if (ui.tab === "Settings") renderSettings();
    notify("Editor format saved for the next session start.");
  });
  $("enter-sends").onchange = run(async () => {
    await request("/prefs", "POST", { enter_sends: $("enter-sends").checked });
    await refreshState();
  });
  $("add-form").addEventListener("submit", (e) => {
    e.preventDefault();
    run(async () => {
      const v = $("add-path").value.trim();
      if (!v) return;
      await fileAction("add", v);
      $("add-path").value = "";
    })();
  });
  $("argument-form").addEventListener("submit", (e) => {
    e.preventDefault();
    run(async () => {
      const arg = $("argument-input").value.trim();
      await command(argCommand + (arg ? " " + arg : ""));
      $("argument-form").hidden = true;
    })();
  });
  onClick("argument-cancel", () => {
    $("argument-form").hidden = true;
  });
  $("prompt").oninput = () => {
    historyPosition = -1;
    grow();
  };
  $("prompt").addEventListener("keydown", (e) => {
    if (e.isComposing) return;
    if (
      e.key === "Enter" &&
      !e.ctrlKey &&
      !e.metaKey &&
      !e.altKey &&
      e.shiftKey !== $("enter-sends").checked
    ) {
      e.preventDefault();
      run(() => send())();
      return;
    }
    if (
      !["ArrowUp", "ArrowDown"].includes(e.key) ||
      ($("prompt").value && historyPosition < 0) ||
      !ui.history.length
    )
      return;
    e.preventDefault();
    const count = ui.history.length;
    if (e.key === "ArrowUp")
      historyPosition = historyPosition < 0 ? count - 1 : Math.max(0, historyPosition - 1);
    else if (historyPosition >= 0)
      historyPosition = historyPosition + 1 >= count ? -1 : historyPosition + 1;
    else return;
    $("prompt").value = historyPosition < 0 ? "" : ui.history[historyPosition];
    grow();
  });
  window.addEventListener("resize", grow);
}
async function boot() {
  bind();
  renderState();
  await loadTree();
  await showTab("Settings");
  grow();
  run(initTerminal)();
  const list = await workspaceList(),
    w = list.workspaces.find((x) => x.id === list.active) || list.workspaces[0];
  if (w) await activate(w);
  setInterval(() => {
    if (!document.hidden && ui.wid) run(refreshState)();
  }, 1200);
  setInterval(() => {
    if (!document.hidden && ui.wid && ui.tab === "Git") run(renderGit)();
  }, 5000);
  setInterval(() => {
    if (!document.hidden) run(workspaceList)();
  }, 10000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && ui.wid) run(refreshState)();
  });
}
run(boot)();
