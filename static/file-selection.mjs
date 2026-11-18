// Legacy Aider /drop can interpret an absolute path as a prefix or a glob.
// Newer Deck servers accept exact chat paths; earlier servers need a fresh /ls.
export function legacyDropIssue(paths, files, workspace = "") {
  if (files?.stale !== false || !Array.isArray(files.data))
    return "Choose Refresh files and tree to check the current attachments, then try again.";
  const normalized = (path) => path.replaceAll("\\", "/").toLowerCase();
  for (const path of paths) {
    const key = normalized(path);
    if (!files.data.some((item) => normalized(item.path) === key))
      return "Choose Refresh files and tree to check the current attachments, then try again.";
    const collision = files.data.find((item) => {
      const other = normalized(item.path);
      return other !== key && other.startsWith(key);
    });
    if (collision)
      return `Dropping ${path} could also drop ${collision.path}. Use an updated Deck server for an exact match.`;
    if (/[?*\[\]]/.test(path) || /[?*\[\]]/.test(workspace))
      return `This server treats characters in the path to ${path} as a pattern. Use an updated Deck server for an exact match.`;
  }
  return "";
}

/** Files selected in one workspace, including choices made while Aider is accepting a batch. */
export class FileSelection {
  paths = new Set();
  message = "";
  #submitted = null;

  get busy() {
    return this.#submitted !== null;
  }

  choose(path, checked) {
    if (this.paths.has(path) === checked) return;
    if (checked) this.paths.add(path);
    else this.paths.delete(path);
    // A later re-selection of the same path is a new choice.
    this.#submitted?.delete(path);
    this.message = "";
  }

  clear() {
    this.paths.clear();
    this.#submitted?.clear();
    this.message = "";
  }

  retainOnly(available) {
    for (const path of this.paths) {
      if (available.has(path)) continue;
      this.paths.delete(path);
      this.#submitted?.delete(path);
    }
  }

  async submit(action, grouped, send, isCurrent, changed, { checkLegacy, singlePath } = {}) {
    const paths = singlePath == null ? [...this.paths] : [singlePath];
    if (this.busy || !paths.length || !isCurrent()) return 0;
    this.#submitted = new Set(paths);
    this.message = "";
    let accepted = 0;
    const acknowledge = (path) => {
      accepted++;
      if (this.#submitted.delete(path)) this.paths.delete(path);
    };
    try {
      changed();
      if (grouped) {
        await send({ action, paths });
        paths.forEach(acknowledge);
      } else {
        // Older servers accept one path per request. Stop before sending any
        // more from an abandoned workspace or from a cleared selection.
        for (const path of paths) {
          if (isCurrent() && this.#submitted.has(path) && checkLegacy)
            await checkLegacy(this.#submitted);
          if (!isCurrent()) {
            this.message = `Accepted ${accepted} of ${paths.length} files before the workspace changed.`;
            break;
          }
          if (!this.#submitted.has(path)) continue;
          await send(action === "drop" ? { action, path, from_chat: true } : { action, path });
          acknowledge(path);
          changed();
        }
      }
    } catch (error) {
      this.message = `Accepted ${accepted} of ${paths.length} files. ${error.message}`;
    } finally {
      this.#submitted = null;
      changed();
    }
    return accepted;
  }
}
