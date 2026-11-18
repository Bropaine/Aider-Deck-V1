import assert from "node:assert/strict";
import test from "node:test";
import { keyboardSize, readLayout, saveLayout, sideBounds, terminalBounds } from "../static/layout.mjs";

const defaults = {
  explorer: true, inspector: true, terminalRatio: null, explorerWidth: null, inspectorWidth: null,
};

test("side panel visibility and divider preference survive reload independently", () => {
  const values = new Map();
  const storage = { getItem: (key) => values.get(key), setItem: (key, value) => values.set(key, value) };
  assert.deepEqual(readLayout(storage), defaults);
  for (const explorer of [false, true]) for (const inspector of [false, true]) {
    const layout = { explorer, inspector, terminalRatio: .375, explorerWidth: 156, inspectorWidth: 210 };
    saveLayout(storage, layout);
    assert.deepEqual(readLayout(storage), layout);
  }
  saveLayout(storage, { explorer: false, inspector: true, terminalRatio: null });
  assert.equal(readLayout(storage).terminalRatio, null);
});

test("invalid or blocked preference storage keeps valid independent choices", () => {
  const fallback = defaults;
  for (const value of ["no JSON", "[4]", "null", '{"terminalRatio":1.2}', '{"explorer":"false"}'])
    assert.deepEqual(readLayout({ getItem: () => value }), fallback);
  assert.deepEqual(readLayout({ getItem: () => '{"explorer":false,"inspector":17,"terminalRatio":"bad"}' }),
    { ...fallback, explorer: false });
  const denied = { getItem() { throw Error("denied"); }, setItem() { throw Error("quota"); } };
  assert.deepEqual(readLayout(denied), fallback);
  assert.doesNotThrow(() => saveLayout(denied, fallback));
});

test("existing layouts use automatic widths and each side can reset independently", () => {
  const stored = (value) => readLayout({ getItem: () => JSON.stringify(value) });
  assert.deepEqual(stored({ inspector: false, terminalRatio: .4 }),
    { ...defaults, inspector: false, terminalRatio: .4 });
  assert.deepEqual(stored({ explorerWidth: 187.5, inspectorWidth: null }),
    { ...defaults, explorerWidth: 187.5 });
  for (const bad of [0, -12, "200", {}, false]) {
    assert.deepEqual(stored({ explorerWidth: bad, inspectorWidth: 220 }),
      { ...defaults, inspectorWidth: 220 });
  }
});

test("horizontal keys move the edge physically and preserve space for the terminal", () => {
  const bounds = sideBounds(250, 410, 144, 340);
  assert.deepEqual(bounds, { min: 144, max: 320 });
  assert.equal(keyboardSize("ArrowLeft", 250, bounds, false, "explorer"), 234);
  assert.equal(keyboardSize("ArrowLeft", 250, bounds, false, "inspector"), 266);
  assert.equal(keyboardSize("ArrowRight", 280, bounds, true, "explorer"), 320);
  assert.equal(keyboardSize("ArrowRight", 165, bounds, true, "inspector"), 144);
  assert.equal(keyboardSize("Home", 260, bounds, false, "inspector"), 144);
  assert.equal(keyboardSize("End", 260, bounds, false, "inspector"), 320);
  assert.equal(keyboardSize("ArrowDown", 260, bounds, false, "inspector"), null);
  assert.equal(sideBounds(200, 340, 192, 340).max, 200);
});

test("keyboard arrows move the divider, with larger Shift steps and bounded endpoints", () => {
  const bounds = terminalBounds(700);
  assert.deepEqual(bounds, { min: 110, max: 526 });
  assert.equal(keyboardSize("ArrowUp", 330, bounds), 314);
  assert.equal(keyboardSize("ArrowDown", 330, bounds, true), 394);
  assert.equal(keyboardSize("ArrowUp", 125, bounds, true), bounds.min);
  assert.equal(keyboardSize("ArrowDown", 519, bounds), bounds.max);
  assert.equal(keyboardSize("Home", 400, bounds), bounds.min);
  assert.equal(keyboardSize("End", 400, bounds), bounds.max);
  assert.equal(keyboardSize("Tab", 400, bounds), null);
  assert.deepEqual(terminalBounds(260), { min: 110, max: 110 });
  assert.deepEqual(terminalBounds(60), { min: 60, max: 60 });
});
