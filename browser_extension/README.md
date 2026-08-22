# Lightning Applier — Browser Extension

A Chrome/Edge (Manifest V3) companion that runs **inside your real browser
session**: no chromedriver, no separate automation profile, your existing
logins just work. It scans job boards on a schedule, scores relevance with the
LLM you pick, and auto-fills ATS applications — around the clock while your
browser is open.

## Install (no build step)

1. Open `chrome://extensions` (or `edge://extensions`)
2. Enable **Developer mode**
3. **Load unpacked** → select this `browser_extension/` folder
4. Click the ⚡ icon → **Settings** → fill in your profile and pick an LLM

## LLM providers (placeholders — paste a key and go)

| Group | Providers | Key |
|---|---|---|
| **NVIDIA** | NVIDIA NIM (`integrate.api.nvidia.com`) — Nemotron models | free at build.nvidia.com |
| **Frontier** | OpenAI · Anthropic Claude · Google Gemini · OpenRouter (free models) · xAI Grok · Mistral · Groq | provider key |
| **Local** | Ollama (`:11434`) · LM Studio (`:1234`) · Custom endpoint (vLLM / llama.cpp / LocalAI) | **none** — nothing leaves your machine |

All providers speak the OpenAI-compatible `/chat/completions` dialect, so
switching is just a dropdown. Model and Base URL fields override the defaults.

> **Ollama note:** allow the extension origin once:
> `OLLAMA_ORIGINS=* ollama serve` (or set the env var system-wide).

## How the 24/7 loop works

1. A `chrome.alarms` tick fires every N minutes (survives MV3 worker suspends).
2. The background worker fetches your **watchlist** boards via free ATS JSON
   APIs (Greenhouse / Lever / Ashby) — `ats:company-slug` per line.
3. Titles are filtered by your include/exclude/location terms, then scored by
   your LLM against your CV summary; jobs ≥ min-score proceed.
4. Each match opens in a background tab where the content script:
   - answers **work-authorization questions per-country** (citizenship + visas
     you hold; anywhere else auto-answers "No / sponsorship required"),
   - fills identity fields from your profile (zero tokens),
   - reuses **learned answers** for similar questions (zero tokens),
   - asks your LLM only for genuinely new questions,
   - attaches your stored **resume PDF** to file inputs,
   - and (only if you enabled auto-submit) clicks Submit.
5. Daily/per-scan caps + a URL dedup set keep it safe to leave running.

Fill-only mode is the default: forms are completed and left open for your
review; you click Submit. Flip **auto-submit** in Settings when you trust it.

You can also click **Fill this page** in the popup on any supported ATS page
you opened yourself.

## Supported boards (content script)

Greenhouse, Lever, Ashby, Workday (`*.myworkdayjobs.com`), SmartRecruiters,
Workable, Jobvite, BambooHR, iCIMS, Taleo.

## Honest limits

- "24/7" means **while the browser is running** — an extension cannot run with
  Chrome closed. For truly headless operation use the Python bot in the parent
  repo (`python main.py`); the extension is the zero-setup, real-session
  complement to it.
- Chrome may throttle alarms to ≥1 min and suspend workers between ticks —
  scans resume on the next tick, nothing is lost.
- Workday tenants requiring fresh account creation are better served by the
  Python bot's full Workday handler; the extension handles the form pages your
  session can already reach.
- Everything (profile, keys, resume, learned answers) is stored in
  `chrome.storage.local` on your machine only. Nothing is synced or uploaded
  anywhere except the LLM calls you configure.
- Automating job-board interactions may violate their Terms of Service — same
  disclaimer as the parent project. Use caps, keep volume human.
