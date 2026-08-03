// LLM client — one OpenAI-compatible interface, many providers.
//
// Provider PLACEHOLDERS ship empty: pick one in Options and paste a key (or
// none for local). Every entry speaks the /chat/completions dialect, so a
// single fetch covers NVIDIA NIM, the frontier APIs, and local servers.

export const PROVIDERS = {
  // ── NVIDIA ────────────────────────────────────────────────────────────
  nvidia: {
    label: "NVIDIA NIM (build.nvidia.com)",
    baseUrl: "https://integrate.api.nvidia.com/v1",
    model: "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    keyPlaceholder: "nvapi-…  (free key at build.nvidia.com)",
    needsKey: true,
  },
  // ── Frontier APIs ─────────────────────────────────────────────────────
  openai: {
    label: "OpenAI",
    baseUrl: "https://api.openai.com/v1",
    model: "gpt-4o-mini",
    keyPlaceholder: "sk-…",
    needsKey: true,
  },
  anthropic: {
    label: "Anthropic Claude",
    baseUrl: "https://api.anthropic.com/v1",
    model: "claude-sonnet-5",
    keyPlaceholder: "sk-ant-…",
    needsKey: true,
  },
  gemini: {
    label: "Google Gemini",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta/openai",
    model: "gemini-2.5-flash",
    keyPlaceholder: "AIza…  (free at aistudio.google.com)",
    needsKey: true,
  },
  openrouter: {
    label: "OpenRouter (free models)",
    baseUrl: "https://openrouter.ai/api/v1",
    model: "nvidia/nemotron-3-super-120b-a12b:free",
    keyPlaceholder: "sk-or-…  (free at openrouter.ai/keys)",
    needsKey: true,
  },
  xai: {
    label: "xAI Grok",
    baseUrl: "https://api.x.ai/v1",
    model: "grok-4",
    keyPlaceholder: "xai-…",
    needsKey: true,
  },
  mistral: {
    label: "Mistral",
    baseUrl: "https://api.mistral.ai/v1",
    model: "mistral-large-latest",
    keyPlaceholder: "…",
    needsKey: true,
  },
  groq: {
    label: "Groq",
    baseUrl: "https://api.groq.com/openai/v1",
    model: "llama-3.3-70b-versatile",
    keyPlaceholder: "gsk_…  (free tier at groq.com)",
    needsKey: true,
  },
  // ── Local LLMs (no key, nothing leaves your machine) ─────────────────
  ollama: {
    label: "Ollama (local)",
    baseUrl: "http://localhost:11434/v1",
    model: "llama3.1",
    keyPlaceholder: "not needed",
    needsKey: false,
  },
  lmstudio: {
    label: "LM Studio (local)",
    baseUrl: "http://localhost:1234/v1",
    model: "local-model",
    keyPlaceholder: "not needed",
    needsKey: false,
  },
  custom: {
    label: "Custom endpoint (vLLM / llama.cpp / LocalAI …)",
    baseUrl: "http://localhost:8000/v1",
    model: "local-model",
    keyPlaceholder: "optional",
    needsKey: false,
  },
};

// One call. Returns the reply text, or "" on any failure (callers degrade).
export async function chat(settings, systemPrompt, userPrompt, maxTokens = 200) {
  const p = settings?.llm || {};
  const preset = PROVIDERS[p.provider] || PROVIDERS.openrouter;
  const baseUrl = (p.baseUrl || preset.baseUrl).replace(/\/$/, "");
  const model = p.model || preset.model;
  const key = p.apiKey || "";
  if (preset.needsKey && !key) return "";

  try {
    const res = await fetch(`${baseUrl}/chat/completions`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(key ? { Authorization: `Bearer ${key}` } : {}),
      },
      body: JSON.stringify({
        model,
        messages: [
          { role: "system", content: systemPrompt },
          { role: "user", content: userPrompt },
        ],
        temperature: 0.2,
        max_tokens: maxTokens,
      }),
    });
    if (!res.ok) return "";
    const data = await res.json();
    return (data.choices?.[0]?.message?.content || "").trim();
  } catch {
    return "";
  }
}
