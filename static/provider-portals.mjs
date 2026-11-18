// Public entry points work before sign-in and never depend on a field's value.
const portals = new Map([
  ["OPENAI_API_KEY", ["OpenAI API platform", "https://platform.openai.com/"]],
  ["ANTHROPIC_API_KEY", ["Claude API platform", "https://platform.claude.com/"]],
  ["GEMINI_API_KEY", ["Google AI Studio", "https://aistudio.google.com/"]],
  ["GOOGLE_API_KEY", ["Google AI Studio", "https://aistudio.google.com/"]],
  ["DEEPSEEK_API_KEY", ["DeepSeek API guide", "https://api-docs.deepseek.com/guides/codex"]],
  ["COHERE_API_KEY", ["Cohere API setup", "https://docs.cohere.com/docs/get-started-installation"]],
  ["GROQ_API_KEY", ["Groq API quickstart", "https://console.groq.com/docs/quickstart"]],
  ["MISTRAL_API_KEY", ["Mistral API docs", "https://docs.mistral.ai/"]],
  ["OPENROUTER_API_KEY", ["OpenRouter API quickstart", "https://openrouter.ai/docs/quickstart"]],
]);

export function providerPortal(name) {
  return portals.get(name.replace(/^AIDER_(OPENAI|ANTHROPIC)_API_KEY$/, "$1_API_KEY"));
}
