import assert from "node:assert/strict";
import test from "node:test";
import { pickerGroups } from "../static/model-picker.js";

const saved = [
  { name: "openai/A", enabled: true },
  { name: "ollama_chat/b:latest", enabled: false },
];
const discovered = [
  { label: "OpenAI", models: ["openai/A", "openai/C"] },
  { label: "Installed Ollama", models: ["ollama_chat/b:latest", "ollama_chat/d"] },
  { label: "Saved Ollama tags", models: ["ollama_chat/d"] },
];
test("saved visibility takes precedence and discovery appears once and unchecked", () => {
  const groups = pickerGroups(saved, discovered);
  assert.deepEqual(groups.map((g) => g.label), ["Saved models", "OpenAI", "Installed Ollama"]);
  assert.deepEqual(groups[0].rows.map((item) => item.enabled), [true, false]);
  assert.deepEqual(groups.slice(1).flatMap((g) => g.rows).map((i) => [i.name, i.enabled]),
    [["openai/C", false], ["ollama_chat/d", false]]);
});
test("search by group or ID keeps key warnings and saved visibility", () => {
  const groups = pickerGroups(saved, discovered, "  OPENAI ", { "openai/C": "Configure an API key." });
  assert.deepEqual(groups.flatMap((g) => g.rows).map((item) => item.name), ["openai/A", "openai/C"]);
  assert.equal(groups[1].rows[0].key_warning, "Configure an API key.");
  assert.deepEqual(pickerGroups(saved, discovered, "Installed")[0].rows.map((item) => item.name), ["ollama_chat/d"]);
});
