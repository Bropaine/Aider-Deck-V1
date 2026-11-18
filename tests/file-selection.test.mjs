import assert from "node:assert/strict";
import test from "node:test";
import { FileSelection, legacyDropIssue } from "../static/file-selection.mjs";

const active = () => true;
const redraw = () => {};

function select(...paths) {
  const selection = new FileSelection();
  for (const path of paths) selection.choose(path, true);
  return selection;
}

test("sends unique literal workspace paths in one grouped file action", async () => {
  const paths = ["one/same.txt", "two/same.txt", "docs/space [v2].txt"];
  for (const action of ["add", "read-only"]) {
    const selection = select(...paths, paths[0]);
    const requests = [];
    const count = await selection.submit(
      action,
      true,
      (body) => requests.push(body),
      active,
      redraw,
    );
    assert.deepEqual(requests, [{ action, paths }]);
    assert.equal(count, paths.length);
    assert.equal(selection.paths.size, 0);
  }
});

test("an empty or inactive workspace never submits a batch", async () => {
  const send = () => assert.fail("unexpected request");
  assert.equal(await select().submit("add", true, send, active, redraw), 0);
  assert.equal(await select("a").submit("add", false, send, () => false, redraw), 0);
});

test("a pending batch blocks duplicate clicks and preserves later choices", async () => {
  const selection = select("a", "b");
  let accept;
  const promise = new Promise((resolve) => {
    accept = resolve;
  });
  const pending = selection.submit("add", true, () => promise, active, redraw);
  assert.equal(selection.busy, true);
  const second = await selection.submit(
    "read-only",
    true,
    () => assert.fail("duplicate"),
    active,
    redraw,
  );
  assert.equal(second, 0);
  selection.choose("a", false);
  selection.choose("a", true);
  selection.choose("c", true);
  accept();
  assert.equal(await pending, 2);
  assert.equal(selection.busy, false);
  assert.deepEqual([...selection.paths], ["a", "c"]);
});

test("group rejection keeps each file selected for retry", async () => {
  const selection = select("a", "b");
  const reject = () => {
    throw Error("Missing file.");
  };
  assert.equal(await selection.submit("add", true, reject, active, redraw), 0);
  assert.deepEqual([...selection.paths], ["a", "b"]);
  assert.match(selection.message, /Accepted 0 of 2 files\. Missing file\./);
});

test("older servers stop at the failed path and retry only outstanding files", async () => {
  const selection = select("a", "b", "c");
  const requests = [];
  const send = (body) => {
    requests.push(body);
    if (body.path === "b") throw Error("Busy");
  };
  assert.equal(await selection.submit("read-only", false, send, active, redraw), 1);
  assert.deepEqual(requests, [
    { action: "read-only", path: "a" },
    { action: "read-only", path: "b" },
  ]);
  assert.deepEqual([...selection.paths], ["b", "c"]);
  assert.match(selection.message, /Accepted 1 of 3 files\. Busy/);
  const retry = [];
  assert.equal(
    await selection.submit("read-only", false, (body) => retry.push(body.path), active, redraw),
    2,
  );
  assert.deepEqual(retry, ["b", "c"]);
  assert.equal(selection.message, "");
});

test("a workspace change stops legacy requests and keeps unsent choices", async () => {
  const selection = select("a", "b");
  let current = true;
  const sent = [];
  const send = (body) => {
    sent.push(body.path);
    current = false;
  };
  assert.equal(await selection.submit("add", false, send, () => current, redraw), 1);
  assert.deepEqual(sent, ["a"]);
  assert.deepEqual([...selection.paths], ["b"]);
  assert.match(selection.message, /workspace changed/);
});

test("clearing then making a new choice during a legacy request preserves the new selection", async () => {
  const selection = select("a", "b");
  const sent = [];
  const send = (body) => {
    sent.push(body.path);
    selection.clear();
    selection.choose("b", true);
  };
  assert.equal(await selection.submit("add", false, send, active, redraw), 1);
  assert.deepEqual(sent, ["a"]);
  assert.deepEqual([...selection.paths], ["b"]);
});

const report = (...paths) => ({
  stale: false,
  data: paths.map((path, index) => ({ path, read_only: index % 2 === 1 })),
});

test("grouped drop submits literal reported paths in one action without a legacy preflight", async () => {
  const paths = ["nested/target.py", "nested/target.py.bak", "nested/read only [v1].txt"];
  const selection = select(...paths);
  const sent = [];
  const count = await selection.submit("drop", true, (body) => sent.push(body), active, redraw, {
    checkLegacy: () => assert.fail("modern servers handle exact paths"),
  });
  assert.deepEqual(sent, [{ action: "drop", paths }]);
  assert.equal(count, paths.length);
  assert.equal(selection.paths.size, 0);
});

test("legacy drop uses the repository-relative path reported by Aider for each remaining file", async () => {
  const selection = select("sub/editable.txt", "sub/read only.txt", "last.txt");
  const sent = [],
    checked = [];
  const count = await selection.submit(
    "drop",
    false,
    (body) => {
      sent.push(body);
      if (sent.length === 2) throw Error("Busy");
    },
    active,
    redraw,
    { checkLegacy: (remaining) => checked.push([...remaining]) },
  );
  assert.equal(count, 1);
  assert.deepEqual(sent, [
    { action: "drop", path: "sub/editable.txt", from_chat: true },
    { action: "drop", path: "sub/read only.txt", from_chat: true },
  ]);
  assert.deepEqual(checked, [
    ["sub/editable.txt", "sub/read only.txt", "last.txt"],
    ["sub/read only.txt", "last.txt"],
  ]);
  assert.deepEqual([...selection.paths], ["sub/read only.txt", "last.txt"]);
  assert.match(selection.message, /Accepted 1 of 3/);
});

test("legacy drop refuses prefix collisions, pattern paths and unconfirmed file lists", () => {
  const files = report(
    "sub/Target.py",
    "sub/target.py.bak",
    "other/target.py",
    "sub/literal[v1].md",
  );
  assert.match(
    legacyDropIssue(["sub\\TARGET.py"], files),
    /sub\/target.py.bak.+updated Deck server/,
  );
  assert.match(
    legacyDropIssue(["sub/Target.py", "sub/target.py.bak"], files),
    /updated Deck server/,
  );
  assert.equal(legacyDropIssue(["sub/target.py.bak", "other/target.py"], files), "");
  assert.match(legacyDropIssue(["sub/literal[v1].md"], files), /pattern/);
  assert.match(legacyDropIssue(["other/target.py"], files, "C:\\[projects]\\demo"), /pattern/);
  assert.match(legacyDropIssue(["not-in-chat.txt"], files), /Refresh files and tree/);
  assert.match(
    legacyDropIssue(["other/target.py"], { ...files, stale: true }),
    /Refresh files and tree/,
  );
  assert.match(legacyDropIssue(["other/target.py"], null), /Refresh files and tree/);
});

test("legacy collision preflight refuses the entire selection before dropping a safe first file", async () => {
  const files = report("safe", "target", "target.bak");
  const selection = select("safe", "target");
  const count = await selection.submit(
    "drop",
    false,
    () => assert.fail("unsafe drop"),
    active,
    redraw,
    {
      checkLegacy: (paths) => {
        const issue = legacyDropIssue(paths, files);
        if (issue) throw Error(issue);
      },
    },
  );
  assert.equal(count, 0);
  assert.deepEqual([...selection.paths], ["safe", "target"]);
  assert.match(selection.message, /target.bak/);
});

test("switching workspaces during a drop preflight sends no request", async () => {
  const selection = select("a", "b");
  let current = true;
  await selection.submit(
    "drop",
    false,
    () => assert.fail("abandoned request"),
    () => current,
    redraw,
    {
      checkLegacy: async () => {
        current = false;
      },
    },
  );
  assert.deepEqual([...selection.paths], ["a", "b"]);
  assert.match(selection.message, /workspace changed/);
});

test("a refreshed attachment list prunes dropped files and preserves unaffected choices", async () => {
  const selection = select("a", "b");
  const sent = [];
  const count = await selection.submit(
    "drop",
    false,
    (body) => sent.push(body.path),
    active,
    redraw,
    {
      checkLegacy: () => {
        selection.retainOnly(new Set(["b", "c"]));
        selection.choose("c", true);
      },
    },
  );
  assert.equal(count, 1);
  assert.deepEqual(sent, ["b"]);
  assert.deepEqual([...selection.paths], ["c"]);
});

test("one-click Drop keeps other checked files and works without a selection", async () => {
  for (const other of [[], ["keep.txt"]]) {
    const selection = select(...other),
      sent = [];
    await selection.submit("drop", false, (body) => sent.push(body), active, redraw, {
      singlePath: "one.txt",
    });
    assert.deepEqual(sent, [{ action: "drop", path: "one.txt", from_chat: true }]);
    assert.deepEqual([...selection.paths], other);
  }
});
