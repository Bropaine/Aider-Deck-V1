import {
  $,
  ui,
  el,
  append,
  replace,
  run,
  button,
  input,
  request,
  notify,
  modal,
  closeModal,
  actions,
  confirmAction,
  askText,
  diffView,
  command,
} from "./core.js";
import { createGitSafety, gitSafetyStatus } from "./git-safety.js";

let lastGit = "",
  gitSerial = 0,
  safety = null;
export function resetGit() {
  lastGit = "";
  safety?.section.remove();
  safety = null;
  gitSerial++;
}
async function showDiff(args, title) {
  const qs = new URLSearchParams(args),
    data = await request("/git/diff?" + qs);
  modal(
    title,
    data.numstat
      ? el(
          "pre",
          String(data.numstat)
            .split(/\r?\n/)
            .filter(Boolean)
            .map((line) => {
              const [add, del, ...path] = line.split("\t");
              return "+" + add + "  −" + del + "  " + path.join("\t");
            })
            .join("\n"),
          "preview",
        )
      : null,
    data.stat ? el("pre", data.stat, "preview") : null,
    diffView(data.diff),
  );
}
export async function renderGit(force = false) {
  const serial = ++gitSerial,
    [data, protection] = await Promise.all([request("/git"), gitSafetyStatus()]);
  if (ui.tab !== "Git" || serial !== gitSerial) return;
  const panel = $("inspector-content"),
    json = JSON.stringify([data, protection]);
  if (!force && json === lastGit && panel.querySelector("#commit-message")) return;
  lastGit = json;
  const previousWorkspace = panel.dataset.gitWorkspace === ui.wid;
  const old = previousWorkspace ? panel.querySelector("#commit-message") : null,
    draft = old?.value || "",
    focus = old === document.activeElement;
  const selection = focus ? [old.selectionStart, old.selectionEnd] : null;
  const previousOutput = previousWorkspace ? panel.querySelector("#git-result")?.textContent : "";
  panel.dataset.gitWorkspace = ui.wid;
  replace(panel);
  const head = el("section");
  append(
    head,
    el("h3", "Working tree"),
    el("strong", data.branch || "Detached HEAD", "mono"),
    data.ahead != null || data.behind != null
      ? el("p", "Ahead " + (data.ahead ?? "—") + " · Behind " + (data.behind ?? "—"), "hint")
      : null,
    el(
      "p",
      (data.staged || []).length +
        " staged · " +
        (data.unstaged || []).length +
        " unstaged · " +
        (data.untracked || []).length +
        " untracked",
      "hint",
    ),
    actions(
      button("Working diff", () => showDiff({}, "Working tree diff")),
      button("Staged diff", () => showDiff({ staged: "1" }, "Staged diff")),
    ),
  );
  panel.append(head);
  safety ??= createGitSafety(ui.wid);
  safety.update(protection);
  panel.append(safety.section);
  const after = async (action, payload) => {
    const r = await request("/git/" + action, "POST", payload);
    await renderGit(true);
    await ui.refresh();
    if ($("git-result")) $("git-result").textContent = r.output || "";
    if (r.output) notify(r.output);
  };
  for (const group of ["staged", "unstaged", "untracked"]) {
    const files = data[group] || [];
    panel.append(el("h3", group + " · " + files.length));
    if (!files.length) panel.append(el("p", "No " + group + " changes.", "hint"));
    for (const file of files) {
      const name = file.old_path ? file.old_path + " → " + file.path : file.path;
      const wrap = el("div", null, "change-row"),
        controls = actions(
          button("View diff", () =>
            showDiff({ path: file.path, staged: group === "staged" ? "1" : "0" }, name),
          ),
        );
      if (group === "staged")
        controls.append(button("Unstage", () => after("unstage", { path: file.path })));
      else {
        controls.append(
          button("Stage", () => after("stage", { path: file.path })),
          button(
            "Discard",
            () =>
              confirmAction(
                "Discard " + file.path + "?",
                group === "untracked"
                  ? "Permanently delete the untracked file " +
                      file.path +
                      "? Git cannot recover this file."
                  : "Permanently discard unstaged changes to " +
                      file.path +
                      " and restore its index version?",
                "Discard changes",
                () => after("discard", { path: file.path, confirmed: true }),
              ),
            "danger",
          ),
        );
      }
      append(wrap, el("span", file.code, "source"), el("span", " " + name, "mono"), controls);
      panel.append(wrap);
    }
  }
  const commit = el("textarea", null, "full");
  commit.id = "commit-message";
  commit.value = draft;
  commit.placeholder = "Commit message";
  commit.setAttribute("aria-label", "Commit message");
  commit.rows = 2;
  const commitBtn = button(
    "Commit staged",
    async () => {
      if (!commit.value.trim()) throw Error("Enter a commit message.");
      const result = await request("/git/commit", "POST", { message: commit.value });
      commit.value = "";
      await renderGit(true);
      await ui.refresh();
      if ($("git-result")) $("git-result").textContent = result.output;
      notify(result.output || "Committed.");
    },
    "primary",
  );
  commitBtn.disabled = !data.staged?.length;
  const result = el("p", previousOutput || "", "hint mono");
  result.id = "git-result";
  append(panel, el("h3", "Commit"), commit, actions(commitBtn), result, el("h3", "Recent commits"));
  for (const c of data.log || [])
    panel.append(
      append(
        el("div", null, "change-row"),
        button(
          c.hash.slice(0, 8) + "  " + c.subject,
          () => showDiff({ commit: c.hash }, c.subject),
          "item-link mono",
        ),
        el("p", c.author + " · " + c.time, "hint small"),
      ),
    );
  if (!data.log?.length) panel.append(el("p", "No commits yet.", "hint"));
  const slashDiff = button("/diff", () => command("/diff")),
    undo = button("Undo Aider’s last commit", () =>
      confirmAction("Aider /undo", "Undo the last commit created by Aider?", "Send /undo", () =>
        command("/undo"),
      ),
    );
  for (const b of [slashDiff, undo]) {
    b.disabled = ui.state.status !== "live";
    b.dataset.liveRequired = "";
  }
  append(panel, el("hr", null, "divider"), el("h3", "Aider session"), actions(slashDiff, undo));
  if (selection) {
    commit.focus({ preventScroll: true });
    commit.setSelectionRange(...selection);
  }
}
export async function renderPrompts() {
  const d = await request("/prompts");
  if (ui.tab !== "Prompts") return;
  const panel = replace($("inspector-content"), el("h3", "Prompt library"));
  const changeFolder = () =>
    askText("Library folder", "Folder path", d.folder, "Save folder", async (folder) => {
      await request("/prefs", "POST", { prompt_library: folder });
      await renderPrompts();
    });
  const folder = el("p", d.folder, "hint mono");
  folder.style.overflowWrap = "anywhere";
  const saveNew = () => {
    const text = $("prompt").value;
    if (!text.trim()) throw Error("Write a prompt in the composer first.");
    const name = input("Prompt filename");
    name.placeholder = "prompt.md";
    name.className = "full";
    const save = async (overwrite = false) => {
      const filename = name.value.trim();
      if (!filename) throw Error("Enter a filename.");
      if (!overwrite && d.prompts.some((p) => p.name.toLowerCase() === filename.toLowerCase())) {
        confirmAction(
          "Replace saved prompt?",
          "Replace the contents of " + filename + "?",
          "Replace",
          () => save(true),
        );
        return;
      }
      await request("/prompts/item", "POST", { name: filename, text, overwrite });
      closeModal();
      await renderPrompts();
      notify("Saved " + filename + ".");
    };
    modal(
      "Save composer as prompt",
      el("p", "Filename (.md or .txt)", "hint"),
      name,
      actions(
        button("Save", () => save(), "primary"),
        button("Cancel", closeModal),
      ),
    );
    name.focus();
  };
  append(
    panel,
    folder,
    actions(button("Change folder", changeFolder), button("Save composer as prompt", saveNew)),
    el("hr", null, "divider"),
  );
  if (!d.prompts.length) panel.append(el("p", "No .md or .txt prompts in this folder.", "empty"));
  for (const p of d.prompts) {
    const preview = async () => {
      const item = await request("/prompts/item?name=" + encodeURIComponent(p.name));
      modal(
        item.name,
        el("pre", item.text, "preview"),
        actions(
          button(
            "Load into composer",
            () => {
              const set = () => {
                ui.setPrompt(item.text);
                closeModal();
                $("prompt").focus();
                notify("Prompt loaded into the composer.");
              };
              if ($("prompt").value && $("prompt").value !== item.text)
                confirmAction(
                  "Replace composer text?",
                  "Load " + item.name + " over the current draft?",
                  "Load",
                  set,
                  false,
                );
              else set();
            },
            "primary",
          ),
          button(
            "Delete",
            () =>
              confirmAction(
                "Delete prompt?",
                "Permanently delete " + p.name + " from the library?",
                "Delete",
                async () => {
                  await request("/prompts/item", "DELETE", { name: p.name, confirmed: true });
                  await renderPrompts();
                },
              ),
            "danger",
          ),
        ),
      );
    };
    append(
      panel,
      append(
        el("div", null, "prompt-row"),
        button(p.name, preview, "item-link mono"),
        el("p", p.preview || "Empty prompt", "hint"),
        el("small", new Date(p.modified).toLocaleString(), "muted small"),
      ),
    );
  }
}
