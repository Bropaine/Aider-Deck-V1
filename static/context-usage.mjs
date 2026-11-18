// Aider estimates prompt size; Ollama reports the currently allocated window.
const tokenCount = (n) => (Number.isSafeInteger(n) && n >= 0 ? n : null);
const windowSize = (n) => (tokenCount(n) > 0 ? n : null);

// Running servers from before upload context checks only expose raw observations.
function legacyBudget(state, model, runner, configured, report, data) {
  const [source, max] = [
    ["Ollama loaded window", windowSize(runner?.window)],
    ["Ollama model default", windowSize(runner?.model_default)],
    ["Configured input limit", configured],
    ["Ollama model capacity", windowSize(runner?.model_capacity)],
    // A new turn invalidates usage and file rows, but the explicit model's
    // previously reported limit still applies until a different model is live.
    ["Aider input limit", windowSize(model && report.model === model ? report.max : data.max)],
  ].find(([, limit]) => limit !== null) || [null, null];
  const reported = tokenCount(data.used);
  const reply = model && state.reply_tokens?.model === model ? tokenCount(state.reply_tokens.used) : null;
  const used = reported ?? reply;
  return {
    window: max, source, used,
    remaining: max !== null && used !== null ? Math.max(0, max - used) : null,
    usage_source: reported !== null ? "report" : reply !== null ? "reply" : null,
  };
}

export function contextUsage(state) {
  const model = state.status === "live" ? state.model : null;
  const runner = model && state.local_context?.model === model ? state.local_context : null;
  const local = !!model && (!!runner || /^ollama(?:_chat)?\//i.test(model));
  const report = state.tokens?.data || {};
  const current =
    !!model && state.tokens?.stale === false && (report.model ? report.model === model : !local);
  const configured = model ? windowSize(state.max_input_tokens) : null;
  const budget = model && state.context_budget?.model === model
    ? state.context_budget
    : legacyBudget(state, model, runner, configured, report, current ? report : {});
  const { window: max, source, used, remaining } = budget;
  const replyEstimate = budget.usage_source === "reply";
  const data = current && budget.usage_source === "report" ? report : {};
  const percent = used !== null && max !== null ? Math.round((used / max) * 100) : null;
  let percentLabel = "";
  if (percent !== null) percentLabel = used > 0 && used < max / 100 ? "<1%" : percent + "%";
  return { data, used, max, source, percent, percentLabel, remaining, configured, local, replyEstimate };
}

// Windows path separators and case can differ between /ls and /tokens.
// Keep the role in the key so an old editable row can't label a read-only file.
export function attachmentEstimates(readout, fileRows) {
  const counts = new Map();
  if (readout?.stale !== false || !Array.isArray(fileRows)) return counts;
  const rows = new Map();
  const key = (row) => (row.read_only ? "r:" : "e:") + row.path.replaceAll("\\", "/").toLowerCase();
  for (const row of fileRows) {
    const count = tokenCount(row.tokens);
    if (typeof row.path !== "string" || count === null) continue;
    const name = key(row);
    rows.set(name, rows.has(name) ? null : count);
  }
  const seen = new Map();
  for (const file of readout.data || []) {
    const name = key(file);
    const count = rows.get(name);
    if (count == null) continue;
    if (seen.has(name)) {
      // A case-sensitive directory can contain two names that normalize alike.
      counts.delete(seen.get(name));
      continue;
    }
    seen.set(name, file);
    counts.set(file, count);
  }
  return counts;
}
