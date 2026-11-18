import test from "node:test";
import assert from "node:assert/strict";
import { attachmentEstimates, contextUsage } from "../static/context-usage.mjs";

const model = "ollama_chat/fixture:latest";
function session() {
  return {
    status: "live",
    model,
    max_input_tokens: 32000,
    tokens: { stale: false, data: { used: 2048, max: 16000, model } },
    local_context: { model, window: 8192 },
  };
}

test("local percent uses Ollama's active window and Aider's current estimate", () => {
  const ctx = contextUsage(session());
  assert.equal(ctx.local, true);
  assert.equal(ctx.max, 8192);
  assert.equal(ctx.percent, 25);
  assert.equal(ctx.remaining, 6144);
  assert.equal(ctx.configured, 32000);
});

test("the meter uses the same active-model budget as text attachment reviews", () => {
  const state = session();
  state.context_budget = {
    model, window: 4096, source: "Ollama loaded window",
    used: 3072, remaining: 1024, usage_source: "reply",
  };
  const context = contextUsage(state);
  assert.equal(context.percent, 75);
  assert.equal(context.remaining, 1024);
  assert.equal(context.replyEstimate, true);
  assert.deepEqual(context.data, {});
  Object.assign(state.context_budget, { used: null, remaining: null, usage_source: null });
  assert.equal(contextUsage(state).used, null);
  assert.equal(contextUsage(state).percent, null);
  state.context_budget.model = "earlier/model";
  assert.equal(contextUsage(state).used, 2048);
});

test("missing, invalid and different-model runner windows use labeled fallback limits", () => {
  for (const info of [
    null,
    { model, window: null },
    { model, window: 0 },
    { model: "other", window: 8192 },
  ]) {
    const ctx = contextUsage({ ...session(), local_context: info });
    assert.equal(ctx.used, 2048);
    assert.equal(ctx.source, "Configured input limit");
    assert.equal(ctx.max, 32000);
    assert.equal(ctx.percent, 6);
  }
});

test("local fallback uses the exact tag's default or its model capacity before Aider's estimate", () => {
  const s = session();
  const update = (runner) => contextUsage({ ...s, local_context: { model, ...runner } });
  assert.equal(update({ model_default: 4096, model_capacity: 65536 }).source, "Ollama model default");
  assert.equal(update({ window: 8192, model_default: 4096 }).max, 8192);
  s.max_input_tokens = null;
  const capacity = update({ model_capacity: 65536 });
  assert.equal(capacity.source, "Ollama model capacity");
  assert.equal(capacity.max, 65536);
  assert.equal(update({}).source, "Aider input limit");
  assert.equal(update({ model: "another", model_capacity: 123 }).max, 16000);
  delete s.tokens.data.max;
  assert.equal(update({}).percent, null);
});

test("a local OpenAI-compatible route meters passive reply counts and explicit reports", () => {
  const model = "openai/fixture:latest";
  const s = { status: "live", model, local_context: { model, window: 4096 }, reply_tokens: { model, used: 1024 } };
  const passive = contextUsage(s);
  assert.equal(passive.local, true);
  assert.equal(passive.replyEstimate, true);
  assert.equal(passive.percent, 25);
  assert.deepEqual(passive.data, {});
  s.tokens = { stale: false, data: { model, used: 1200, files: 200 } };
  assert.equal(contextUsage(s).replyEstimate, false);
  assert.equal(contextUsage(s).used, 1200);
  s.tokens.stale = true;
  assert.equal(contextUsage(s).used, 1024);
  s.reply_tokens.model = "previous";
  assert.equal(contextUsage(s).percent, null);
  assert.deepEqual(contextUsage(s).data, {});
  assert.equal(contextUsage({ ...s, status: "exited" }).used, null);
});

test("a fresh reply can reuse the same model's last reported maximum without the stale breakdown", () => {
  const s = { status: "live", model, tokens: { stale: true, data: { model, used: 1024, history: 24, max: 65536 } },
    reply_tokens: { model, used: 1600 } };
  const ctx = contextUsage(s);
  assert.equal(ctx.source, "Aider input limit");
  assert.equal(ctx.max, 65536);
  assert.equal(ctx.used, 1600);
  assert.equal(ctx.percent, 2);
  assert.deepEqual(ctx.data, {});
  assert.equal(contextUsage({ ...s, model: "ollama_chat/different" }).max, null);
  s.tokens.data.model = null;
  assert.equal(contextUsage(s).max, null);
});

test("unknown, stale and different-model token readouts cannot populate local estimates", () => {
  for (const tokens of [
    null,
    { stale: true, data: { model, used: 1024 } },
    { stale: false, data: { used: 1024 } },
    { stale: false, data: { model: "ollama/old", used: 1024 } },
  ]) {
    const ctx = contextUsage({ ...session(), tokens });
    assert.equal(ctx.max, 8192);
    assert.equal(ctx.used, null);
    assert.equal(ctx.percent, null);
    assert.equal(ctx.remaining, null);
    assert.deepEqual(ctx.data, {});
  }
  assert.equal(contextUsage({ ...session(), status: "exited" }).max, null);
});

test("zero usage is an observation and an overfull context shows its full percentage", () => {
  const s = session();
  s.tokens.data.used = 0;
  assert.equal(contextUsage(s).percent, 0);
  assert.equal(contextUsage(s).percentLabel, "0%");
  s.tokens.data.used = 1;
  assert.equal(contextUsage(s).percentLabel, "<1%");
  s.tokens.data.used = 2048;
  assert.equal(contextUsage(s).percentLabel, "25%");
  s.tokens.data.used = 12288;
  assert.equal(contextUsage(s).percent, 150);
  assert.equal(contextUsage(s).remaining, 0);
});

test("hosted models use their configured input limit or Aider's report", () => {
  const s = session();
  s.model = "openai/main";
  s.tokens.data.model = s.model;
  assert.equal(contextUsage(s).max, 32000);
  s.max_input_tokens = null;
  assert.equal(contextUsage(s).max, 16000);
});

test("file estimates match Windows path and /ls role and preserve zero counts", () => {
  const file = { path: "Src/My file [1].py", read_only: true };
  const empty = { path: "empty.txt", read_only: false };
  const missing = { path: "another.py", read_only: false };
  const list = { stale: false, data: [file, empty, missing] };
  const rows = [
    { path: "src\\my FILE [1].py", read_only: true, tokens: 321 },
    { ...file, read_only: false, tokens: 1 },
    { ...empty, tokens: 0 },
  ];
  const costs = attachmentEstimates(list, rows);
  assert.equal(costs.get(file), 321);
  assert.equal(costs.get(empty), 0);
  assert.equal(costs.has(missing), false);
  assert.equal(attachmentEstimates({ ...list, stale: true }, rows).size, 0);
  assert.equal(attachmentEstimates(list, undefined).size, 0);
});

test("unmatched roles, ambiguous paths, and invalid counts do not label attachments", () => {
  const file = { path: "name.txt", read_only: true };
  const list = { stale: false, data: [file] };
  for (const rows of [
    [{ ...file, read_only: false, tokens: 9 }],
    [
      { ...file, tokens: 12 },
      { ...file, path: "NAME.TXT", tokens: 62 },
    ],
    [{ ...file, tokens: -1 }],
  ])
    assert.equal(attachmentEstimates(list, rows).size, 0);
  assert.equal(
    attachmentEstimates({ ...list, data: [file, { ...file, path: "NAME.TXT" }] }, [
      { ...file, tokens: 12 },
    ]).size,
    0,
  );
});
