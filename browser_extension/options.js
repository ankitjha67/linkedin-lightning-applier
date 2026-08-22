import { PROVIDERS } from "./lib/llm.js";

const $ = (id) => document.getElementById(id);

const PROFILE_FIELDS = ["firstName", "lastName", "email", "phone", "city",
  "country", "linkedin", "github", "yearsExperience", "currentCompany",
  "noticePeriod", "salary"];
const TOP_FIELDS = ["includeTerms", "excludeTerms", "locationTerms", "watchlist",
  "scanIntervalMinutes", "minScore", "maxPerDay", "maxPerScan", "cvText"];

function populateProviders(selected) {
  const sel = $("provider");
  sel.innerHTML = "";
  const groups = {
    "NVIDIA": ["nvidia"],
    "Frontier APIs": ["openai", "anthropic", "gemini", "openrouter", "xai", "mistral", "groq"],
    "Local LLMs (no key)": ["ollama", "lmstudio", "custom"],
  };
  for (const [groupLabel, keys] of Object.entries(groups)) {
    const og = document.createElement("optgroup");
    og.label = groupLabel;
    for (const k of keys) {
      const o = document.createElement("option");
      o.value = k;
      o.textContent = PROVIDERS[k].label;
      if (k === selected) o.selected = true;
      og.appendChild(o);
    }
    sel.appendChild(og);
  }
  refreshPlaceholders();
}

const GROUP_OF = {
  nvidia: "grpNvidia",
  openai: "grpFrontier", anthropic: "grpFrontier", gemini: "grpFrontier",
  openrouter: "grpFrontier", xai: "grpFrontier", mistral: "grpFrontier",
  groq: "grpFrontier",
  ollama: "grpLocal", lmstudio: "grpLocal", custom: "grpLocal",
};

function refreshPlaceholders() {
  const key = $("provider").value;
  const p = PROVIDERS[key];
  $("apiKey").placeholder = p.keyPlaceholder;
  $("model").placeholder = p.model;
  $("baseUrl").placeholder = p.baseUrl;
  $("keyHint").textContent = p.needsKey
    ? "Stored only in this browser's extension storage."
    : "Local provider — no API key, nothing leaves your machine.";
  for (const id of ["grpNvidia", "grpFrontier", "grpLocal"]) {
    document.getElementById(id)?.classList.toggle("active", GROUP_OF[key] === id);
  }
}

async function load() {
  const s = await chrome.storage.local.get(null);
  populateProviders(s.llm?.provider || "openrouter");
  $("apiKey").value = s.llm?.apiKey || "";
  $("model").value = s.llm?.model || "";
  $("baseUrl").value = s.llm?.baseUrl || "";
  for (const f of PROFILE_FIELDS) $(f).value = s.profile?.[f] || "";
  for (const f of TOP_FIELDS) if (s[f] !== undefined) $(f).value = s[f];
  $("citizenship").value = s.workAuth?.citizenship || "";
  $("visas").value = s.workAuth?.visas || "";
  $("autoSubmit").checked = !!s.autoSubmit;
  if (s.resumeName) $("resumeHint").textContent = `Stored: ${s.resumeName}`;
}

async function saveAll() {
  const patch = {
    llm: {
      provider: $("provider").value,
      apiKey: $("apiKey").value.trim(),
      model: $("model").value.trim(),
      baseUrl: $("baseUrl").value.trim(),
    },
    profile: Object.fromEntries(PROFILE_FIELDS.map((f) => [f, $(f).value.trim()])),
    workAuth: { citizenship: $("citizenship").value, visas: $("visas").value },
    autoSubmit: $("autoSubmit").checked,
  };
  for (const f of TOP_FIELDS) {
    const el = $(f);
    patch[f] = el.type === "number" ? Number(el.value) : el.value;
  }
  const file = $("resumeFile").files[0];
  if (file) {
    const buf = await file.arrayBuffer();
    let bin = "";
    const bytes = new Uint8Array(buf);
    for (let i = 0; i < bytes.length; i += 0x8000) {
      bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
    }
    patch.resumeB64 = btoa(bin);
    patch.resumeName = file.name;
    $("resumeHint").textContent = `Stored: ${file.name}`;
  }
  await chrome.storage.local.set(patch);
  $("saved").style.opacity = 1;
  setTimeout(() => ($("saved").style.opacity = 0), 1500);
}

$("provider").addEventListener("change", refreshPlaceholders);
// Clicking a group chip jumps to that group's first provider.
const GROUP_FIRST = { grpNvidia: "nvidia", grpFrontier: "openrouter", grpLocal: "ollama" };
for (const [id, prov] of Object.entries(GROUP_FIRST)) {
  document.getElementById(id)?.addEventListener("click", () => {
    $("provider").value = prov;
    refreshPlaceholders();
  });
}
$("save").addEventListener("click", saveAll);
load();
