import assert from "node:assert/strict";
import test from "node:test";
import { providerPortal } from "../static/provider-portals.mjs";

test("each built-in provider key has an official public portal", () => {
  for (const [provider, host] of Object.entries({
    OPENAI: "platform.openai.com", ANTHROPIC: "platform.claude.com", GEMINI: "aistudio.google.com",
    COHERE: "docs.cohere.com", DEEPSEEK: "api-docs.deepseek.com", GROQ: "console.groq.com",
    MISTRAL: "docs.mistral.ai", OPENROUTER: "openrouter.ai",
  })) {
    const [label, address] = providerPortal(provider + "_API_KEY");
    const url = new URL(address);
    assert.ok(label.length);
    assert.equal(url.origin, "https://" + host);
    assert.equal(url.search + url.hash, "");
  }
});

test("aliases use the same portal and arbitrary variables never become links", () => {
  assert.deepEqual(providerPortal("GOOGLE_API_KEY"), providerPortal("GEMINI_API_KEY"));
  assert.deepEqual(providerPortal("AIDER_OPENAI_API_KEY"), providerPortal("OPENAI_API_KEY"));
  for (const name of ["OPENAI_API_BASE", "CUSTOM_API_KEY", "https://example.org/", "__proto__"])
    assert.equal(providerPortal(name), undefined);
});
