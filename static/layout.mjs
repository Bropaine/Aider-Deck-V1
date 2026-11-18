const storageKey = "aider-deck.layout.v1";
const defaults = { explorer: true, inspector: true, terminalRatio: null };
const movementKeys = {
  composer: ["ArrowUp", "ArrowDown"],
  explorer: ["ArrowLeft", "ArrowRight"],
  inspector: ["ArrowRight", "ArrowLeft"],
};

function storedWidth(value) {
  return Number.isFinite(value) && value > 0 ? value : null;
}

export function readLayout(storage) {
  let value;
  try {
    value = JSON.parse(storage?.getItem(storageKey) || "null");
  } catch { /* Layout remains usable when storage is unavailable. */ }
  const ratio = value?.terminalRatio;
  return {
    explorer: typeof value?.explorer === "boolean" ? value.explorer : defaults.explorer,
    inspector: typeof value?.inspector === "boolean" ? value.inspector : defaults.inspector,
    terminalRatio: Number.isFinite(ratio) && ratio >= 0 && ratio <= 1 ? ratio : null,
    explorerWidth: storedWidth(value?.explorerWidth),
    inspectorWidth: storedWidth(value?.inspectorWidth),
  };
}

export function saveLayout(storage, layout) {
  try { storage?.setItem(storageKey, JSON.stringify(layout)); } catch { /* Use in-memory changes. */ }
}

export function terminalBounds(total) {
  const min = Math.min(110, Math.max(0, total));
  return { min, max: Math.max(min, total - 174) };
}
function clamp(value, { min, max }) {
  return Math.min(max, Math.max(min, value));
}
export function keyboardSize(key, current, bounds, shift = false, pane = "composer") {
  const increment = shift ? 64 : 16;
  const [decrease, increase] = movementKeys[pane];
  switch (key) {
    case decrease: return clamp(current - increment, bounds);
    case increase: return clamp(current + increment, bounds);
    case "Home": return bounds.min;
    case "End": return bounds.max;
    default: return null;
  }
}

export function sideBounds(width, centerWidth, min, centerMin) {
  return { min, max: Math.max(min, width + centerWidth - centerMin) };
}

function bindDivider(handle, pane, geometry, resize, reset, persist) {
  const coordinate = pane === "composer" ? "clientY" : "clientX";
  const sign = pane === "inspector" ? -1 : 1;
  const cursor = pane === "composer" ? "resizing-composer" : "resizing-side";
  let drag;
  handle.addEventListener("pointerdown", (event) => {
    if (!event.isPrimary || event.button !== 0) return;
    event.preventDefault();
    handle.focus({ preventScroll: true });
    handle.setPointerCapture(event.pointerId);
    drag = { id: event.pointerId, origin: event[coordinate], size: geometry().current, moved: false };
    document.documentElement.classList.add(cursor);
  });
  handle.addEventListener("pointermove", (event) => {
    if (drag?.id !== event.pointerId || !handle.offsetWidth) return;
    const delta = (event[coordinate] - drag.origin) * sign;
    if (!delta && !drag.moved) return;
    drag.moved = true;
    const box = geometry();
    resize(clamp(drag.size + delta, box.bounds), box);
  });
  handle.addEventListener("lostpointercapture", () => {
    if (drag?.moved) persist();
    drag = null;
    document.documentElement.classList.remove(cursor);
  });
  handle.addEventListener("dblclick", reset);
  handle.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      reset();
      return;
    }
    const box = geometry();
    const size = keyboardSize(event.key, box.current, box.bounds, event.shiftKey, pane);
    if (size === null) return;
    event.preventDefault();
    resize(size, box);
    persist();
  });
}

function bindSideWidths(desk, layout, storage) {
  const center = desk.querySelector(".session-pane");
  desk.classList.add("resizable-sides");
  for (const name of ["explorer", "inspector"]) {
    const side = document.getElementById(name + "-pane");
    const label = name === "explorer" ? "Explorer" : "Inspector";
    const edge = document.createElement("div");
    edge.id = name + "-divider";
    edge.className = "side-divider";
    edge.tabIndex = 0;
    edge.setAttribute("role", "separator");
    edge.setAttribute("aria-orientation", "vertical");
    edge.setAttribute("aria-controls", side.id);
    edge.setAttribute("aria-label", "Resize " + label);
    edge.title = "Resize " + label + " · Drag or use Left/Right · Double-click to reset";
    side.insertAdjacentElement(name === "explorer" ? "afterend" : "beforebegin", edge);

    const preference = name + "Width", property = "--" + name + "-width";
    function apply(width) {
      layout[preference] = width;
      if (width === null) desk.style.removeProperty(property);
      else desk.style.setProperty(property, width + "px");
    }
    function geometry() {
      const style = getComputedStyle(desk);
      const current = side.offsetWidth;
      const bounds = sideBounds(current, center.offsetWidth,
        parseFloat(style.getPropertyValue("--" + name + "-min")),
        parseFloat(style.getPropertyValue("--center-min")));
      return { current, bounds };
    }
    function updateAria() {
      if (!edge.offsetWidth) return;
      const { current, bounds: { min, max } } = geometry();
      edge.setAttribute("aria-valuemin", String(min));
      edge.setAttribute("aria-valuemax", String(max));
      edge.setAttribute("aria-valuenow", String(current));
      edge.setAttribute("aria-valuetext", label + " " + current + " pixels");
    }
    function reset() {
      apply(null);
      saveLayout(storage, layout);
    }
    apply(layout[preference]);
    const observer = new ResizeObserver(updateAria);
    observer.observe(side);
    observer.observe(center);

    bindDivider(edge, name, geometry, apply, reset, () => saveLayout(storage, layout));
  }
}

export function bindLayout(onAutoSize) {
  const find = (id) => document.getElementById(id);
  const divider = find("composer-divider"), composer = find("composer"), terminal = find("terminal-shell");
  // An older running server can supply a cached template without the controls.
  if (!divider || !composer || !terminal || !find("toggle-explorer") || !find("toggle-inspector")) return;
  const desk = composer.closest(".desk"), pane = composer.parentElement;
  let storage;
  try { storage = window.localStorage; } catch { /* Some browser policies block the getter. */ }
  const layout = readLayout(storage);
  bindSideWidths(desk, layout, storage);

  function show(name, visible, persist = true) {
    const button = find("toggle-" + name), side = find(name + "-pane");
    if (!visible && side.contains(document.activeElement)) button.focus();
    side.hidden = !visible;
    desk.classList.toggle(name + "-hidden", !visible);
    const label = (visible ? "Hide " : "Show ") + (name === "explorer" ? "Explorer" : "Inspector");
    button.title = label;
    button.setAttribute("aria-label", label);
    button.setAttribute("aria-expanded", String(visible));
    layout[name] = visible;
    if (persist) saveLayout(storage, layout);
  }
  for (const name of ["explorer", "inspector"]) {
    show(name, layout[name], false);
    find("toggle-" + name).addEventListener("click", () => show(name, !layout[name]));
  }

  function geometry() {
    const chrome = Array.from(pane.children).reduce((sum, child) =>
      sum + (child === terminal || child === composer ? 0 : child.offsetHeight), 0);
    const total = Math.max(1, pane.clientHeight - chrome);
    return { total, bounds: terminalBounds(total), current: terminal.offsetHeight };
  }
  function paint() {
    const { total, bounds, current } = geometry();
    const height = layout.terminalRatio === null ? current : clamp(Math.round(total * layout.terminalRatio), bounds);
    composer.classList.toggle("sized", layout.terminalRatio !== null);
    if (layout.terminalRatio !== null) composer.style.setProperty("--composer-height", (total - height) + "px");
    else composer.style.removeProperty("--composer-height");
    const percent = (n) => Math.round(100 * n / total);
    divider.setAttribute("aria-valuemin", String(percent(Math.min(bounds.min, height))));
    divider.setAttribute("aria-valuemax", String(percent(Math.max(bounds.max, height))));
    divider.setAttribute("aria-valuenow", String(percent(height)));
    divider.setAttribute("aria-valuetext", "Terminal " + height + " pixels, Composer " + Math.max(0, total - height) + " pixels");
  }
  function resize(pixels, { total }) {
    layout.terminalRatio = pixels / total;
    paint();
  }
  function reset() {
    layout.terminalRatio = null;
    paint();
    onAutoSize();
    saveLayout(storage, layout);
  }
  let frame = 0;
  const observer = new ResizeObserver(() => {
    if (frame) return;
    frame = requestAnimationFrame(() => { frame = 0; paint(); });
  });
  for (const item of [pane, ...pane.children]) observer.observe(item);

  bindDivider(divider, "composer", geometry, resize, reset, () => saveLayout(storage, layout));
  paint();
  return { showInspector: () => { if (!layout.inspector) show("inspector", true); } };
}
