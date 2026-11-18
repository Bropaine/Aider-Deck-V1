import { el, request } from "./core.js";

export function externalLink(label, url) {
  const link = el("a", label);
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  return link;
}

export function modelCatalogLink(field) {
  const slot = el("div", null, "hint small");
  slot.id = "model-catalog-link";
  let timer, lastModel, revision = 0;
  function refresh() {
    const model = field.value.trim();
    if (lastModel === model) return;
    lastModel = model;
    clearTimeout(timer);
    const version = ++revision;
    slot.replaceChildren();
    if (!model) return;
    timer = setTimeout(async () => {
      if (!field.isConnected) return;
      try {
        const { link } = await request("/config/model-catalog", "POST", { model });
        if (field.isConnected && version === revision && link?.url.startsWith("https://"))
          slot.append(externalLink(link.label, link.url));
      } catch {
        // The rest of the picker remains usable on an older server.
      }
    }, 220);
  }
  field.addEventListener("input", refresh);
  field.addEventListener("change", refresh);
  refresh();
  return slot;
}
