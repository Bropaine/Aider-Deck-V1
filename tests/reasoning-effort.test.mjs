import assert from "node:assert/strict";
import test from "node:test";
import { settingsFilterForRender } from "../static/settings.js";
import {
  effortChoices, effortLookup, launchGuardNotice, savedRegistrationEffort, workspaceEffort,
} from "../static/reasoning-effort.js";

test("warning review survives Settings redraws and restores the prior search on restart", () => {
  for (const previous of ["", "map-tokens"]) {
    const review = { value: "reasoning-effort", previous };
    assert.deepEqual(settingsFilterForRender({ value: previous, dataset: {} }, { reviewReasoning: true }), review);
    const field = { value: review.value, dataset: { beforeReview: previous } };
    assert.deepEqual(settingsFilterForRender(field), review);
    assert.deepEqual(settingsFilterForRender(field, { reviewReasoning: true }), review);
    assert.equal(settingsFilterForRender(field, { sessionChanged: true }).value, previous);
  }
});

test("restart preserves a manual filter even when it matches the warning shortcut", () => {
  for (const value of ["reasoning-effort", "map-tokens", ""]) {
    const field = { value, dataset: {} };
    assert.equal(settingsFilterForRender(field, { sessionChanged: true }).value, value);
  }
});

test("published effort levels are model-specific and preserve provider order", () => {
  const report = {
    target: "launch",
    status: "known",
    levels: ["minimal", "high", "high", "xhigh"],
  };
  assert.deepEqual(effortChoices(report, "high"), {
    kind: "select",
    levels: ["minimal", "high", "xhigh"],
    value: "high",
    unlisted: false,
  });
  const saved = effortChoices(report, "medium");
  assert.equal(saved.unlisted, true);
  assert.equal(saved.value, "medium");
  assert.ok(!saved.levels.includes("medium"));
});

test("manual entry preserves custom and zero settings when the provider omits its list", () => {
  for (const status of ["known", "unknown"]) {
    const report = { target: "launch", status, levels: [] };
    assert.deepEqual(effortChoices(report, 0), { kind: "manual", value: "0" });
    assert.deepEqual(effortChoices(report, "custom"), { kind: "manual", value: "custom" });
    assert.deepEqual(effortChoices(report, null), { kind: "manual", value: "" });
  }
});

test("an unsupported model has no manual control and an old launch value can be cleared", () => {
  assert.deepEqual(effortChoices({ status: "unsupported", target: "launch" }, "3"), {
    kind: "unsupported",
    value: "3",
  });
  assert.deepEqual(effortChoices({ status: "loading" }, "3"), { kind: "loading" });
});

test("clearing an unsupported override is only available for the actual workspace setting", () => {
  const root = "G:\\work";
  assert.equal(workspaceEffort({ configured_path: "g:/Work/.aider.conf.yml" }, root), true);
  assert.equal(workspaceEffort({ configured_path: "C:/home/.aider.conf.yml" }, root), false);
  assert.equal(workspaceEffort({ configured_path: "G:/work/.env" }, root), false);
  assert.equal(workspaceEffort({ configured_path: "Process environment" }, root), false);
  assert.equal(workspaceEffort({}, root), false);
});

test("live choices never claim that the saved launch effort is already active", () => {
  const report = { target: "live", status: "known", levels: ["low", "max"] };
  assert.deepEqual(effortChoices(report, "high"), {
    kind: "select",
    levels: ["low", "max"],
    value: "",
    unlisted: false,
  });
  assert.equal(effortChoices({ ...report, status: "unknown" }, "7").value, "");
});

test("missing model skips discovery and a failed lookup permits manual entry and retry", async () => {
  let calls = 0;
  const seen = [];
  const lookup = effortLookup(
    async (model) => {
      if (++calls === 1) throw Error("offline");
      return { model, status: "known", levels: ["high"] };
    },
    (report) => seen.push(report),
  );
  await lookup("", "launch");
  assert.equal(calls, 0);
  assert.equal(seen.at(-1).status, "unknown");
  await lookup("my-model", "launch");
  await lookup("my-model", "launch");
  assert.equal(calls, 1);
  assert.deepEqual(
    seen.map((r) => r.status),
    ["unknown", "loading", "unknown"],
  );
  await lookup("my-model", "launch", true);
  assert.equal(calls, 2);
  assert.deepEqual(seen.at(-1).levels, ["high"]);
});

test("a missing Deck endpoint explains the server version and preserves manual entry", async () => {
  let report;
  let status = 404;
  const lookup = effortLookup(
    async () => { throw Object.assign(Error("request failed"), { status }); },
    (result) => { report = result; },
  );
  await lookup("selected", "launch");
  assert.match(report.message, /This Deck server predates model capability lookup/);
  assert.deepEqual(effortChoices(report, "high"), { kind: "manual", value: "high" });
  status = 503;
  await lookup("selected", "launch", true);
  assert.equal(report.message, "Could not look up this model’s levels.");
  assert.deepEqual(effortChoices(report, "high"), { kind: "manual", value: "high" });
});

test("delayed responses and workspace aborts cannot publish choices for another target", async () => {
  const pending = [];
  const seen = [];
  const lookup = effortLookup(
    (model) =>
      new Promise((resolve, reject) => {
        pending.push({ finish: (levels) => resolve({ model, levels, status: "known" }), reject });
      }),
    (report) => seen.push(report),
  );
  const first = lookup("old", "launch");
  const second = lookup("new", "launch");
  const third = lookup("new", "live");
  pending[2].finish(["high"]);
  await third;
  pending[1].finish(["medium"]);
  pending[0].finish(["low"]);
  await Promise.all([first, second]);
  assert.deepEqual(
    seen.filter((r) => r.status === "known"),
    [{ model: "new", target: "live", status: "known", levels: ["high"] }],
  );
  const gone = lookup("other", "live");
  pending[3].reject(new DOMException("Workspace changed", "AbortError"));
  await gone;
  assert.equal(seen.at(-1).status, "loading");
});

test("misidentified metadata never populates the dropdown", async () => {
  const seen = [];
  const lookup = effortLookup(
    async () => ({ model: "other", status: "known", levels: ["low"] }),
    (report) => seen.push(report),
  );
  await lookup("selected", "launch");
  assert.equal(seen.at(-1).status, "stale");
  assert.equal(seen.at(-1).model, "selected");
});

test("a changed workspace model requires refreshing before editing its effort", async () => {
  const seen = [];
  const lookup = effortLookup(
    async () => {
      const error = Error("Model changed");
      error.status = 409;
      throw error;
    },
    (report) => seen.push(report),
  );
  await lookup("old", "launch");
  assert.deepEqual(effortChoices(seen.at(-1), "high"), { kind: "stale" });
});

test("launch guard offers a model-specific declaration for confirmed and uncertain support", () => {
  const base = { model: "openai/example", target: "launch", status: "known" };
  const launch_guard = { checked: true, declared: false, can_declare: true };
  const missing = launchGuardNotice({ ...base, launch_guard });
  assert.equal(missing.canDeclare, true);
  assert.match(missing.message, /will ignore/);
  const uncertain = launchGuardNotice({
    ...base, status: "unknown", launch_guard: { ...launch_guard, declared: null },
  });
  assert.equal(uncertain.canDeclare, true);
  assert.match(uncertain.message, /If Aider warns/);
  const inherited = { ...launch_guard, can_declare: false, message: "Edit the selected settings file." };
  assert.deepEqual(launchGuardNotice({ ...base, launch_guard: inherited }), {
    canDeclare: false, message: inherited.message,
  });
});

test("live, declared, unchecked, unsupported and stale targets never offer a launch repair", () => {
  const launch_guard = { checked: true, declared: false, can_declare: true };
  const report = { model: "selected", target: "launch", status: "known", launch_guard };
  for (const changes of [
    { target: "live" }, { status: "unsupported" }, { status: "loading" }, { status: "stale" },
    { model: "" }, { launch_guard: null }, { launch_guard: { ...launch_guard, checked: false } },
    { launch_guard: { ...launch_guard, declared: true } },
  ]) assert.equal(launchGuardNotice({ ...report, ...changes }), null);
});

test("registration only looks up effort for the saved launch model with a configured level", async () => {
  const model = { key: "model", configured: "configured" };
  const effort = { key: "reasoning-effort", configured: "high" };
  const lookup = () => assert.fail("Registration should skip this metadata lookup");
  for (const rows of [[model], [model, { ...effort, configured: "" }], [effort]]) {
    assert.equal(await savedRegistrationEffort("configured", rows, lookup), null);
  }
  assert.equal(await savedRegistrationEffort("live-only", [model, effort], lookup), null);
});

test("registration offers the saved effort only for an exact level with an available declaration", async () => {
  const model = "openai/example";
  const rows = [{ key: "model", configured: model }, { key: "reasoning-effort", configured: "high" }];
  const report = { model, status: "known", levels: ["low", "high"], launch_guard: { can_declare: true } };
  assert.equal(await savedRegistrationEffort(model, rows, async () => report), "high");
  for (const changes of [
    { model: "other" }, { status: "unknown" }, { status: "unsupported" },
    { levels: ["low"] }, { levels: null }, { launch_guard: {} }, { launch_guard: { can_declare: false } },
  ]) {
    assert.equal(await savedRegistrationEffort(model, rows, async () => ({ ...report, ...changes })), null);
  }
});
