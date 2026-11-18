import { $, append, button, el, notify, request, ui } from "./core.js";

const MiB = 1024 * 1024;
const labels = { attaching: "Attaching…", attached: "Read-only", removing: "Removing…", failed: "Failed" };
const local = new Map();
let pickerTarget, lastList, refreshAfterWrite, openReview;

// Use the reported size limits before reading bytes. The server validates the
// file name, actual bytes and quota again before it creates a session copy.
export async function encodeAttachments(files, existing = []) {
  if (!files.length) throw Error("Choose one or more files.");
  if (files.length + existing.length > 8) throw Error("Attach up to 8 files per session.");
  const total = [...files, ...existing].reduce((bytes, file) => bytes + file.size, 0);
  if (total > 4 * MiB) throw Error("Session attachments can use up to 4 MiB in total.");
  for (const file of files) {
    if (isImage(file) && file.size > 2 * MiB)
      throw Error(file.name + " exceeds the 2 MiB image limit.");
  }
  return Promise.all(files.map(async (file) => {
    let bytes;
    try {
      bytes = new Uint8Array(await file.arrayBuffer());
    } catch {
      throw Error("Could not read " + file.name + ". Choose an individual file you can open.");
    }
    let binary = "";
    for (let i = 0; i < bytes.length; i += 32768)
      binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
    return { name: file.name, data: btoa(binary) };
  }));
}

function isImage(file) {
  return /\.(?:jpe?g|png)$/i.test(file.name);
}

export function contextWarning(report) {
  const model = report.model || "the current model";
  if (report.reason === "legacy")
    return "This running Deck server cannot check the remaining context for " + model +
      ". Restart the Deck server to enable context-aware uploads.";
  const count = (value) => value.toLocaleString();
  const estimate = "The selected text is estimated at " + count(report.estimated_tokens) + " tokens.";
  const advertised = report.source === "Ollama model capacity";
  let remaining = "The remaining context is unknown for " + model + ".";
  if (report.remaining_tokens != null) {
    remaining = model + (advertised ? " has up to " : " has about ") +
      count(report.remaining_tokens) + " tokens remaining.";
  }
  const limit = report.window_tokens != null && report.source
    ? " " + report.source + " · " + count(report.window_tokens) + " tokens." : "";
  const allocation = advertised ? " Ollama’s actual loaded window may be smaller." : "";
  const images = report.has_images ? " Images in this selection use additional tokens beyond the text estimate." : "";
  return estimate + " " + remaining + limit + allocation + images + " Leave room for your message and the reply.";
}

// The server either attaches the batch or returns its assessment without adding a file.
export async function uploadSelection(files, { existing, target, supportsContext, post, review, isCurrent }) {
  const textFiles = files.filter(file => !isImage(file));
  if (!supportsContext) {
    const large = textFiles.find(file => file.size > MiB / 4);
    if (large) throw Error(large.name + " exceeds this older Deck server’s 256 KiB text limit. " +
      "Restart the Deck server to use context-aware uploads.");
  }
  const body = { session: target.id, model: target.model, files: await encodeAttachments(files, existing) };
  if (!isCurrent()) return null;
  if (!supportsContext && textFiles.length) {
    if (!await review({ model: target.model, reason: "legacy" }) || !isCurrent()) return null;
  }
  let result = await post(body);
  while (result.context_review) {
    if (!isCurrent() || !await review(result.context_review) || !isCurrent()) return null;
    result = await post({ ...body, context_confirmation: result.context_review.confirmation });
  }
  if (!Array.isArray(result.attachments)) throw Error("Deck could not confirm the attachment change.");
  return result;
}

export function sendIssue(attachments = [], activity) {
  if (activity?.error) return "Review the attachment error before sending.";
  if (activity?.pending) return "Wait for the attachment change to finish.";
  if (attachments.some((item) => item.status === "failed"))
    return "Remove the failed attachment before sending.";
  if (attachments.some((item) => item.status !== "attached"))
    return "Wait for Aider to confirm the attachment change.";
  return "";
}

function session() {
  return ui.state.status === "live" && ui.state.supports_attachments && ui.state.attachment_session;
}
function uploadTarget() {
  return { workspace: ui.wid, id: session(), model: ui.state.model ?? null };
}
function matchesTarget(target) {
  return target.workspace === ui.wid && target.id === session() && target.model === (ui.state.model ?? null);
}
export function attachmentSendIssue() {
  return sendIssue(ui.state.attachments, local.get(session()));
}
function unavailable() {
  return ui.state.status === "live"
    ? "Start an updated Deck server to attach files from your computer."
    : "Start an Aider session to attach files.";
}

export function renderAttachments() {
  if (openReview && !matchesTarget(openReview.target)) {
    if (openReview.target.id === session())
      local.get(session()).error = "The live model changed. Choose the files again to check their context.";
    openReview.finish(false);
  }
  const add = $("attach-files");
  if (!add) return; // A running older server can still have a cached template.
  const id = session(), activity = local.get(id), items = ui.state.attachments || [];
  add.disabled = !id || !!activity?.pending;
  add.title = id ? "Attach read-only text files or PNG/JPEG images from your computer" : unavailable();

  const key = JSON.stringify([id, items, activity?.pending]);
  if (lastList !== key) {
    lastList = key;
    const list = $("prompt-attachments");
    list.replaceChildren();
    list.hidden = items.length === 0;
    for (const item of items) {
      const remove = button("×", () => removeAttachment(id, item));
      remove.setAttribute("aria-label", "Remove " + item.name);
      remove.title = "Remove " + item.name + " from chat";
      remove.disabled = !id || !!activity?.pending || !["attached", "failed"].includes(item.status);
      const status = item.error ? "Failed · " + item.error : labels[item.status] || item.status;
      list.append(append(el("li", null, "attachment-chip"), el("span", item.name), el("small", status), remove));
    }
  }
  const message = openReview ? "" : activity?.error || activity?.pending || "";
  const feedback = $("attachment-feedback");
  feedback.hidden = !message;
  feedback.classList.toggle("warn-text", !!activity?.error);
  const label = $("attachment-message");
  if (label.textContent !== message) label.textContent = message;
  $("attachment-dismiss").hidden = !activity?.error;
  for (const control of [$("send"), $("once")])
    control.disabled = ui.state.status !== "live" || !!attachmentSendIssue();
}

async function mutate(id, message, update) {
  if (!id || session() !== id) {
    notify(id ? "The session changed. Choose the files again." : unavailable(), true);
    return;
  }
  if (local.get(id)?.pending) return;
  const activity = { pending: message, error: "" };
  local.set(id, activity);
  renderAttachments();
  try {
    const result = await update();
    if (!result || session() !== id) return;
    ui.state.attachments = result.attachments;
    await refreshAfterWrite();
  } catch (error) {
    if (error.name !== "AbortError") activity.error = error.message;
  } finally {
    activity.pending = "";
    if (!activity.error) local.delete(id);
    renderAttachments();
  }
}
function reviewContext(report, target, files) {
  const panel = el("section", null, "attachment-review");
  panel.setAttribute("role", "region");
  panel.setAttribute("aria-label", "Attachment context warning");
  const title = report.reason === "over" || report.reason === "near"
    ? "The selected text may exceed the remaining context" : "Check attachment context";
  const explanation = el("p", contextWarning(report));
  explanation.id = "attachment-context-explanation";
  append(panel, el("strong", title), el("p", files.map(file => file.name).join(", "), "mono"),
    explanation);
  return new Promise(resolve => {
    function finish(approved) {
      if (openReview?.finish !== finish) return;
      openReview = null;
      const hadFocus = panel.contains(document.activeElement);
      panel.remove();
      if (hadFocus && matchesTarget(target)) $("prompt").focus({ preventScroll: true });
      resolve(approved);
    }
    const cancel = button("Cancel", () => finish(false));
    cancel.setAttribute("aria-describedby", explanation.id);
    panel.append(append(el("div", null, "action-row"), cancel, button("Attach anyway", () => finish(true))));
    panel.addEventListener("keydown", event => {
      if (event.key === "Escape") {
        event.preventDefault();
        finish(false);
      }
    });
    openReview = { target, finish };
    $("attachment-feedback").after(panel);
    renderAttachments();
    cancel.focus();
  });
}
function addAttachments(target, files, folder = false) {
  if (!folder && !files.length) return;
  return mutate(target.id, "Checking attachments…", async () => {
    if (!matchesTarget(target)) throw Error("The live model changed. Choose the files again.");
    if (folder) throw Error("Drop individual files onto the Composer. Folders cannot be attached.");
    return uploadSelection(files, {
      existing: ui.state.attachments, target, supportsContext: ui.state.supports_attachment_context,
      post: body => request("/attachments", "POST", body),
      review: report => reviewContext(report, target, files),
      isCurrent: () => matchesTarget(target),
    });
  });
}
function removeAttachment(id, item) {
  return mutate(id, "Removing " + item.name + "…", () =>
    request("/attachments/" + encodeURIComponent(item.id) + "/remove", "POST", { session: id }));
}
function isFileDrag(transfer) {
  return transfer && Array.from(transfer.types).includes("Files");
}
export function bindAttachments(refresh) {
  const add = $("attach-files"), picker = $("attachment-picker");
  if (!add || !picker) return;
  refreshAfterWrite = refresh;
  add.addEventListener("click", () => {
    pickerTarget = uploadTarget();
    picker.click();
  });
  picker.addEventListener("change", () => {
    const files = Array.from(picker.files);
    picker.value = "";
    addAttachments(pickerTarget ?? uploadTarget(), files);
    pickerTarget = null;
  });
  $("attachment-dismiss").addEventListener("click", () => {
    local.delete(session());
    renderAttachments();
  });
  const composer = add.closest(".composer");
  let depth = 0;
  composer.addEventListener("dragenter", (event) => {
    if (!isFileDrag(event.dataTransfer)) return;
    event.preventDefault();
    depth++;
    composer.classList.add("file-hover");
  });
  composer.addEventListener("dragleave", () => {
    depth = Math.max(0, depth - 1);
    if (!depth) composer.classList.remove("file-hover");
  });
  document.addEventListener("dragover", (event) => {
    if (!isFileDrag(event.dataTransfer)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = composer.contains(event.target) && !add.disabled ? "copy" : "none";
  });
  document.addEventListener("drop", (event) => {
    if (!isFileDrag(event.dataTransfer)) return;
    event.preventDefault();
    depth = 0;
    composer.classList.remove("file-hover");
    if (!composer.contains(event.target)) return;
    const transfer = event.dataTransfer;
    const folder = Array.from(transfer.items).some((item) => item.webkitGetAsEntry?.()?.isDirectory);
    addAttachments(uploadTarget(), Array.from(transfer.files), folder);
  });
}
