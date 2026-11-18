import assert from "node:assert/strict";
import test from "node:test";
import { contextWarning, encodeAttachments, sendIssue, uploadSelection } from "../static/attachments.mjs";

const MiB = 1024 * 1024;
const file = (name, bytes) => new File([bytes], name);

test("picker and drag batches preserve file names, bytes and order", async () => {
  const image = new Uint8Array(2 * MiB).fill(0xff);
  const payload = await encodeAttachments([
    file("Notes ☀.md", "a\r\nb\nΔ"),
    file("Photo.JPEG", image),
    file("empty.py", ""),
  ]);
  assert.deepEqual(payload.map((item) => item.name), ["Notes ☀.md", "Photo.JPEG", "empty.py"]);
  assert.equal(Buffer.from(payload[0].data, "base64").toString("utf8"), "a\r\nb\nΔ");
  assert.deepEqual(new Uint8Array(Buffer.from(payload[1].data, "base64")), image);
  assert.equal(payload[2].data, "");
});

test("validate the whole batch and existing session quota before reading bytes", async () => {
  let reads = 0;
  const chosen = (name, size) => ({ name, size, arrayBuffer() { reads++; return new ArrayBuffer(0); } });
  await assert.rejects(encodeAttachments([]), /Choose/);
  await assert.rejects(encodeAttachments([chosen("small.py", 1)], Array(8).fill({ size: 0 })), /8 files/);
  await assert.rejects(encodeAttachments([chosen("valid.txt", 1), chosen("big.py", 4 * MiB)]), /4 MiB/);
  await assert.rejects(encodeAttachments([chosen("large.PNG", 2 * MiB + 1)]), /large.PNG.*2 MiB/);
  await assert.rejects(encodeAttachments([chosen("image.jpg", 2 * MiB)], [{ size: 2 * MiB + 1 }]), /4 MiB/);
  assert.equal(reads, 0);
});

test("unreadable external files reject the batch with their name", async () => {
  const denied = { name: "locked.txt", size: 32, arrayBuffer: () => Promise.reject(Error("Denied")) };
  await assert.rejects(encodeAttachments([file("ok.txt", "ok"), denied]), /Could not read locked.txt/);
});

test("send waits for native confirmation, removal and review of rejected uploads", () => {
  assert.equal(sendIssue([]), "");
  assert.equal(sendIssue([{ status: "attached" }]), "");
  assert.match(sendIssue([], { pending: "Reading…" }), /Wait/);
  assert.match(sendIssue([], { error: "Too large" }), /Review/);
  for (const status of ["attaching", "removing", "unknown"])
    assert.match(sendIssue([{ status: "attached" }, { status }]), /confirm/);
  assert.match(sendIssue([{ status: "failed" }]), /Remove/);
});

const target = { id: "session-a", model: "ollama_chat/local", workspace: "repo" };
const defaults = { target, existing: [], supportsContext: true, isCurrent: () => true,
  review: () => assert.fail("Unexpected review") };
const warning = { estimated_tokens: 950, remaining_tokens: 1000, window_tokens: 8192,
  source: "Ollama loaded window", model: target.model, reason: "near", confirmation: "budget-a" };

test("a fitting batch sends text larger than the previous cap once with the live model", async () => {
  const contents = "test\n".repeat(60000);
  const result = await uploadSelection([file("large.txt", contents)], { ...defaults,
    post: async body => {
      assert.equal(body.model, target.model);
      assert.equal(body.session, target.id);
      assert.equal(Buffer.from(body.files[0].data, "base64").toString(), contents);
      assert.equal(body.context_confirmation, undefined);
      return { attachments: ["large.txt"] };
    } });
  assert.deepEqual(result.attachments, ["large.txt"]);
});

test("confirmation retries the unchanged batch once and cancellation submits nothing further", async () => {
  const files = [file("a.md", "one"), file("b.md", "two")];
  for (const accepted of [true, false]) {
    let requests = [];
    const result = await uploadSelection(files, { ...defaults,
      post: async body => {
        requests.push(body);
        return requests.length === 1 ? { context_review: warning } : { attachments: ["a", "b"] };
      },
      review: report => { assert.deepEqual(report, warning); return accepted; },
    });
    assert.equal(requests.length, accepted ? 2 : 1);
    if (accepted) {
      assert.deepEqual(requests[1], { ...requests[0], context_confirmation: warning.confirmation });
      assert.deepEqual(result.attachments, ["a", "b"]);
    } else assert.equal(result, null);
  }
});

test("a workspace, generation or model change cancels the outstanding review", async () => {
  for (const changeDuring of ["assessment", "review"]) {
    let current = true, posts = 0, reviews = 0;
    const result = await uploadSelection([file("notes.md", "draft")], { ...defaults,
      isCurrent: () => current,
      post: async () => {
        assert.equal(++posts, 1);
        if (changeDuring === "assessment") current = false;
        return { context_review: warning };
      },
      review: () => { reviews++; current = false; return true; },
    });
    assert.equal(result, null);
    assert.equal(reviews, changeDuring === "review" ? 1 : 0);
  }
});

test("a changed budget requires a new selection after a stale confirmation", async () => {
  let calls = 0;
  await assert.rejects(uploadSelection([file("notes.md", "hi")], {
    ...defaults, review: () => true,
    post: async body => {
      calls++;
      if (!body.context_confirmation) return { context_review: warning };
      throw Object.assign(Error("The available context changed. Choose the files again."), { status: 409 });
    },
  }), { status: 409, message: /Choose the files again/ });
  assert.equal(calls, 2);
});

test("context warning distinguishes unknown and exhausted budgets and labels mixed batches", () => {
  assert.match(contextWarning(warning), /950 tokens.*ollama_chat\/local.*1,000 tokens remaining.*Ollama loaded window.*8,192/);
  assert.match(contextWarning({ ...warning, reason: "over", remaining_tokens: 0 }), /0 tokens remaining/);
  assert.match(contextWarning({ ...warning, reason: "capacity", source: "Ollama model capacity" }),
    /up to 1,000 tokens remaining.*Ollama model capacity.*loaded window may be smaller/);
  assert.match(contextWarning({ ...warning, reason: "unknown", remaining_tokens: null, has_images: true }),
    /remaining context is unknown.*ollama_chat\/local.*Images/);
});

test("older servers can still attach small text after review and images directly", async () => {
  let posts = 0, reviews = 0;
  const legacy = { ...defaults, supportsContext: false,
    post: async () => { posts++; return { attachments: [] }; },
    review: report => { reviews++; assert.equal(report.reason, "legacy"); return false; } };
  assert.equal(await uploadSelection([file("tiny.txt", "hi")], legacy), null);
  assert.equal(posts, 0);
  const confirmed = await uploadSelection([file("tiny.txt", "hi")], { ...legacy, review: () => true });
  assert.deepEqual(confirmed.attachments, []);
  await uploadSelection([file("photo.png", "sample")], legacy);
  await assert.rejects(uploadSelection([file("large.txt", new Uint8Array(MiB / 4 + 1))], legacy), /Restart the Deck server/);
  assert.equal(posts, 2);
  assert.equal(reviews, 1);
});
