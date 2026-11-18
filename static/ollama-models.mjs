// Ollama returns fully qualified tags from its installed and loaded model lists.
export function canonicalTag(value) {
  const tag = value.trim().replace(/^(?:ollama_chat|ollama)\//, "");
  return tag && !tag.includes(":") ? tag + ":latest" : tag;
}

export function modelLibrary(status) {
  const models = new Map();
  function get(name) {
    const tag = canonicalTag(name);
    if (!models.has(tag)) models.set(tag, { tag, saved: false, installed: false, loaded: false });
    return models.get(tag);
  }
  for (const tag of status.saved) get(tag).saved = true;
  for (const item of status.installed)
    Object.assign(get(item.name), { installed: true, size: item.size });
  for (const item of status.loaded) get(item.name).loaded = true;
  return [...models.values()].sort((a, b) => a.tag.localeCompare(b.tag));
}

export function modelActions(status, model, busy = false) {
  const available = status.running || status.cli_available;
  const idle = !busy && status.job?.state !== "running";
  return {
    save: !busy && !!model.tag && !model.saved,
    download: idle && !!model.tag && available,
    // A stopped daemon cannot list its models until Use starts it. The
    // backend checks the installed tag before loading or replacing Aider.
    use: idle && !!model.tag && available && (!status.running || !!model.installed),
  };
}

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let size = bytes,
    index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index++;
  }
  return Number(size.toFixed(1)) + " " + units[index];
}

export function progressText(job) {
  if (job.total > 0 && job.completed != null) {
    return formatBytes(job.completed) + " / " + formatBytes(job.total);
  }
  return job.completed > 0 ? formatBytes(job.completed) : "";
}

export function requestUse(value, live, confirm, submit) {
  const tag = value.trim();
  if (!tag) return;
  if (live) return confirm(tag, () => submit(tag, true));
  return submit(tag, false);
}
