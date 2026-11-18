import test from "node:test";
import assert from "node:assert/strict";
import { reportSummary } from "../static/git-safety.js";

test("empty outgoing range and empty staged content have distinct outcomes", () => {
  const empty = { findings: [], files_scanned: 0, commits_scanned: 0 };
  assert.match(reportSummary("outgoing", empty), /No outgoing commits/);
  assert.match(reportSummary("staged", empty), /No staged file content/);
});

test("a finding in metadata is still a finding even when no file content was scanned", () => {
  const result = { findings: [{ kind: "Private material" }], files_scanned: 0, commits_scanned: 1 };
  assert.equal(
    reportSummary("outgoing", result),
    "Potential secrets found · 1 commit · 0 files checked",
  );
});

test("a completed scan reports its actual scope and counts", () => {
  const result = { findings: [], files_scanned: 1, commits_scanned: 2 };
  assert.equal(
    reportSummary("outgoing", result),
    "No potential secrets found · 2 commits · 1 file checked",
  );
  assert.equal(reportSummary("staged", result), "No potential secrets found · 1 file checked");
});
