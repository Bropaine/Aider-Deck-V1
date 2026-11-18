import assert from "node:assert/strict";
import test from "node:test";
import { ui } from "../static/core.js";
import { liveModelLabel, renderOverview } from "../static/settings.js";

test("main and weak labels use each live announcement after a model switch", () => {
  const old = "openai/gpt-4.1", current = "ollama_chat/local:latest";
  const state = { status: "live", model: current, weak_model: old,
    launch: { model: old, "weak-model": "different" } };
  assert.equal(liveModelLabel(state, "model"), "Live main model · " + current);
  assert.equal(liveModelLabel(state, "weak-model"), "Live weak model · " + old);
  state.weak_model = current;
  assert.equal(liveModelLabel(state, "weak-model"), "Live weak model · " + current);
});

test("missing role announcements and exited sessions cannot imply a live weak model", () => {
  const state = { status: "live", model: "main", weak_model: null, launch: { "weak-model": "saved" } };
  assert.equal(liveModelLabel(state, "weak-model"), "Live weak model · Not yet reported");
  delete state.weak_model;
  assert.match(liveModelLabel(state, "weak-model"), /Restart the Deck server/);
  state.status = "exited";
  state.weak_model = "previous";
  assert.equal(liveModelLabel(state, "weak-model"), "Live weak model · No live session");
});

test("polling refreshes the filtered weak readout while a replacement model is being typed", t => {
  const savedState = ui.state, savedDocument = globalThis.document;
  t.after(() => {
    ui.state = savedState;
    if (savedDocument === undefined) delete globalThis.document;
    else globalThis.document = savedDocument;
  });
  const readout = { textContent: "" }, field = { value: "typed-draft" };
  const elements = { "live-weak-model": readout, "field-weak-model": field };
  globalThis.document = { getElementById: id => elements[id] ?? null };
  ui.state = { status: "live", model: "ollama_chat/local", weak_model: "openai/old" };
  renderOverview();
  assert.equal(readout.textContent, "Live weak model · openai/old");
  ui.state.weak_model = "openai/new";
  renderOverview();
  assert.equal(readout.textContent, "Live weak model · openai/new");
  assert.equal(field.value, "typed-draft");
});
