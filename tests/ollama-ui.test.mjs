import assert from "node:assert/strict";
import test from "node:test";
import {
  canonicalTag,
  modelActions,
  modelLibrary,
  formatBytes,
  progressText,
  requestUse,
} from "../static/ollama-models.mjs";

const running = { running: true, cli_available: false, job: null };
const stopped = { running: false, cli_available: true, job: null };
const model = { tag: "llama:8b", installed: true };

test("typed default tags match Ollama's installed and saved names", () => {
  assert.equal(canonicalTag("  llama  "), "llama:latest");
  assert.equal(canonicalTag("owner/model:v2"), "owner/model:v2");
  assert.equal(canonicalTag("ollama_chat/llama"), "llama:latest");
  assert.equal(canonicalTag("  "), "");
  assert.deepEqual(
    modelLibrary({
      saved: ["llama", "qwen:8b"],
      installed: [
        { name: "llama:latest", size: 1024 },
        { name: "small:v1", size: 50 },
      ],
      loaded: [{ name: "llama:latest", size: 9000 }],
    }),
    [
      { tag: "llama:latest", saved: true, installed: true, loaded: true, size: 1024 },
      { tag: "qwen:8b", saved: true, installed: false, loaded: false },
      { tag: "small:v1", saved: false, installed: true, loaded: false, size: 50 },
    ],
  );
});

test("Use requires an installed tag when Ollama's library is available", () => {
  assert.deepEqual(modelActions(running, model), { use: true, download: true, save: true });
  assert.deepEqual(modelActions(running, { tag: "missing", saved: true }), {
    use: false,
    download: true,
    save: false,
  });
  assert.equal(modelActions(running, { tag: "" }).use, false);
});

test("the owner can use a named model in one step when the daemon can be started", () => {
  assert.equal(modelActions(stopped, { tag: "named:latest" }).use, true);
  assert.deepEqual(modelActions({ ...stopped, cli_available: false }, model), {
    use: false,
    download: false,
    save: true,
  });
  assert.equal(modelActions(stopped, { tag: "" }).download, false);
});

test("running operations block duplicate loads and downloads", () => {
  assert.deepEqual(modelActions(running, model, true), {
    use: false,
    download: false,
    save: false,
  });
  assert.deepEqual(modelActions({ ...running, job: { state: "running" } }, model), {
    use: false,
    download: false,
    save: true,
  });
  assert.equal(modelActions({ ...running, job: { state: "failed" } }, model).use, true);
});

test("progress distinguishes unknown totals, byte counts and reported layer sizes", () => {
  assert.equal(progressText({ completed: 0, total: 0 }), "");
  assert.equal(progressText({ completed: 5, total: 0 }), "5 B");
  assert.equal(progressText({ completed: 0, total: 1024 }), "0 B / 1 KiB");
  assert.equal(progressText({ completed: 1572864, total: 2097152 }), "1.5 MiB / 2 MiB");
  assert.equal(formatBytes(null), "");
});

test("a live Aider session requires an affirmative click before requesting replacement", () => {
  const submits = [];
  const submit = (tag, confirmed) => submits.push({ tag, confirmed });
  let proceed;
  requestUse(
    " local:latest ",
    true,
    (tag, fn) => {
      assert.equal(tag, "local:latest");
      proceed = fn;
    },
    submit,
  );
  assert.deepEqual(submits, []);
  proceed();
  assert.deepEqual(submits, [{ tag: "local:latest", confirmed: true }]);
});

test("idle workspaces submit once without replacement permission; empty input does nothing", () => {
  const submits = [];
  const confirm = () => assert.fail("unexpected confirmation");
  const submit = (tag, confirmed) => submits.push({ tag, confirmed });
  requestUse(" ", false, confirm, submit);
  requestUse(" name:v1 ", false, confirm, submit);
  assert.deepEqual(submits, [{ tag: "name:v1", confirmed: false }]);
});
