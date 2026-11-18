import { actions, append, button, el, replace, request, ui } from "./core.js";

export function reportSummary(scope, report) {
  const files = report.files_scanned;
  const totals = [`${files} file${files === 1 ? "" : "s"}`];
  if (scope === "outgoing" && Number.isInteger(report.commits_scanned)) {
    const n = report.commits_scanned;
    totals.unshift(`${n} commit${n === 1 ? "" : "s"}`);
  }
  const count = report.findings.length;
  if (count) return `Potential secrets found · ${totals.join(" · ")} checked`;
  if (scope === "outgoing" && report.commits_scanned === 0)
    return "No outgoing commits in the checked range.";
  if (scope === "staged" && files === 0) return "No staged file content to scan.";
  return `No potential secrets found · ${totals.join(" · ")} checked`;
}

function reportView(scope, report) {
  const box = el("div", null, "git-scan-report");
  append(
    box,
    el("strong", reportSummary(scope, report), report.findings.length ? "warn-text" : ""),
    el(
      "p",
      report.coverage || (scope === "staged" ? "Staged changes" : "Current branch history"),
      "hint",
    ),
  );
  if (!report.findings.length) return box;
  const list = el("ul");
  for (const finding of report.findings) {
    const path = finding.path || "Repository";
    const location = finding.line == null ? path : path + ":" + finding.line;
    const row = append(el("li"), el("code", location), el("span", " · " + finding.kind));
    if (finding.commit) row.append(el("small", " · " + finding.commit.slice(0, 12), "mono"));
    list.append(row);
  }
  box.append(list);
  if (report.truncated)
    box.append(
      el("p", "Additional findings were omitted. Fix these and run the check again.", "hint"),
    );
  box.append(
    el(
      "p",
      scope === "staged"
        ? "Edit and stage the file again, or unstage it below."
        : "Remove flagged material from the outgoing commits before pushing.",
      "hint",
    ),
  );
  return box;
}

export async function gitSafetyStatus() {
  try {
    return await request("/git/safety");
  } catch (error) {
    if (error.name === "AbortError") throw error;
    return {
      error:
        error.status === 404
          ? "Open an updated Deck server to use the pre-push check."
          : "Could not check pre-push protection. Refresh the Git panel and try again.",
    };
  }
}

export function createGitSafety(wid) {
  const section = el("section");
  section.id = "git-safety";
  const status = el("p", "", "hint");
  const output = el("div");
  output.id = "git-safety-result";
  output.setAttribute("role", "status");
  const outgoing = button("Check before push", () => perform("outgoing"), "primary");
  const staged = button("Check staged files", () => perform("staged"));
  const enable = button("Enable pre-push check", () => perform("enable"));
  append(section, el("h3", "Secret checks"), status, actions(outgoing, staged, enable), output);
  let info = {},
    pending = "",
    currentReport = null,
    message = "";

  function paint() {
    const hook = info.hook;
    status.textContent =
      info.error ||
      (hook?.enabled
        ? "Automatic pre-push check enabled. Git will stop the push if it finds a potential secret."
        : hook?.available === false
          ? hook.reason
          : "Automatic pre-push check is off for this repository.");
    enable.hidden = !hook || hook.enabled;
    enable.disabled = !!pending || hook?.available === false;
    outgoing.disabled = staged.disabled = !hook || !!pending;
    if (pending) {
      output.textContent = pending === "enable" ? "Enabling pre-push check…" : "Checking locally…";
    } else if (currentReport) {
      replace(output, reportView(currentReport.scope, currentReport.data));
    } else {
      output.textContent = message;
    }
  }

  async function perform(scope) {
    if (pending || wid !== ui.wid || !section.isConnected) return;
    pending = scope;
    currentReport = null;
    message = "";
    paint();
    try {
      if (scope === "enable") {
        const result = await request("/git/safety/enable", "POST", {});
        info = { ...info, hook: result.hook };
      } else {
        const result = await request("/git/safety/check", "POST", { scope });
        if (!Array.isArray(result.findings) || !Number.isInteger(result.files_scanned))
          throw Error("Deck could not read the scan result. Run the check again.");
        const latest = await gitSafetyStatus();
        info = latest;
        if (!latest.revision)
          message = "Could not verify the current Git state. Run the check again.";
        else if (latest.revision !== result.revision)
          message = "Git changed during this check. Run it again for the current state.";
        else currentReport = { scope, data: result };
      }
    } catch (error) {
      if (error.name !== "AbortError") message = error.message;
    } finally {
      pending = "";
      paint();
    }
  }

  return {
    section,
    update(next) {
      if (currentReport && next.revision !== info.revision) {
        currentReport = null;
        message = "Git changed since the last check. Run it again for the current state.";
      }
      info = next;
      paint();
    },
  };
}
