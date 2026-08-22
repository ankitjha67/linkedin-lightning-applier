// Lightning Applier — background service worker.
//
// The 24/7 engine: a chrome.alarms tick wakes this worker on your interval
// (surviving MV3 worker shutdowns), scans the job-board watchlist via free
// ATS JSON APIs, scores relevance (keywords, then your configured LLM), and
// opens matching postings in background tabs where the content script fills
// and submits. Caps + an applied-set make it safe to leave running forever.

import { chat } from "./lib/llm.js";

const ALARM = "lla-scan";

const DEFAULTS = {
  enabled: false,            // master switch — user flips it on in the popup
  autoSubmit: false,         // fill-only until the user opts into submitting
  scanIntervalMinutes: 30,
  maxPerDay: 15,
  maxPerScan: 3,
  minScore: 60,
  includeTerms: "credit risk, risk manager, basel",
  excludeTerms: "intern, staff nurse",
  locationTerms: "",         // empty = any location
  watchlist: [
    "greenhouse:monzo", "greenhouse:gocardless", "greenhouse:adyen",
    "greenhouse:affirm", "greenhouse:marqeta", "greenhouse:mercury",
    "greenhouse:nubank", "greenhouse:robinhood", "greenhouse:sofi",
    "lever:*add-your-own*",
  ].join("\n"),
  llm: { provider: "openrouter", apiKey: "", baseUrl: "", model: "" },
  profile: {},               // personal fields — set in Options
  workAuth: { citizenship: "", visas: "" },
  cvText: "",
  resumeName: "", resumeB64: "",   // stored resume for file inputs
  answers: [],               // learned Q→A memory
  seen: [],                  // job URLs already opened this cycle-set (dedup)
  applied: [],               // job URLs actually SUBMITTED
  stats: { day: "", appliedToday: 0, lastScan: "", totalApplied: 0 },
};

// ───────────────────────── storage helpers ─────────────────────────

async function getState() {
  const s = await chrome.storage.local.get(null);
  return { ...DEFAULTS, ...s, llm: { ...DEFAULTS.llm, ...(s.llm || {}) } };
}
const save = (patch) => chrome.storage.local.set(patch);

function todayKey() { return new Date().toISOString().slice(0, 10); }

async function bumpApplied(url, submitted) {
  const s = await getState();
  const stats = s.stats.day === todayKey()
    ? s.stats : { ...s.stats, day: todayKey(), appliedToday: 0, filledToday: 0 };
  if (submitted) {
    // Only a real submission counts against the daily cap and the counters.
    stats.appliedToday += 1;
    stats.totalApplied = (stats.totalApplied || 0) + 1;
    const applied = [...new Set([...s.applied, url])].slice(-2000);
    await save({ stats, applied });
    chrome.action.setBadgeText({ text: String(stats.appliedToday) });
  } else {
    // Fill-only: the form was completed for review, nothing was sent.
    stats.filledToday = (stats.filledToday || 0) + 1;
    await save({ stats });
  }
}

// ───────────────────────── answer memory (RAG-lite) ─────────────────────────

const STOP = new Set(("a an the of to in on for with do does you your have has is are be " +
  "this that any please what how many much if or and at we our can will would there").split(" "));
// NOTE: "us" is NOT a stopword — it collides with the country (US).

function tokens(text) {
  return (text || "").toLowerCase().match(/[a-z][a-z0-9+#.]*/g)?.filter(
    (t) => t.length > 1 && !STOP.has(t)) || [];
}
function similarity(a, b) {
  const A = new Set(tokens(a)), B = new Set(tokens(b));
  if (!A.size || !B.size) return 0;
  let inter = 0; for (const t of A) if (B.has(t)) inter++;
  return inter / Math.sqrt(A.size * B.size);
}
function memoryLookup(answers, question) {
  let best = null, bestSim = 0;
  for (const e of answers) {
    const sim = similarity(e.q, question);
    if (sim > bestSim) { best = e; bestSim = sim; }
  }
  return bestSim >= 0.85 ? { answer: best.a, sim: bestSim } : null;
}

// ───────────────────────── question answering ─────────────────────────

async function answerQuestion(q) {
  const s = await getState();
  // 1. memory reuse — zero tokens
  const hit = memoryLookup(s.answers, q.question);
  if (hit) return hit.answer;
  // 2. LLM with profile context
  const sys = `You answer job-application form questions for a candidate. Reply with ONLY the answer — no explanation. For choices, reply with EXACTLY one option. Be truthful to this profile:\n${s.cvText.slice(0, 2500)}`;
  let user = `Question: ${q.question}`;
  if (q.options?.length) user += `\nOptions: ${q.options.join(", ")}`;
  if (q.jobTitle) user += `\nJob: ${q.jobTitle} at ${q.company || ""}`;
  if (q.location) user += `\nJob location: ${q.location}`;
  const ans = await chat(s, sys, user);
  if (ans && !q.noSave) {
    await save({ answers: [...s.answers, { q: q.question, a: ans }].slice(-1000) });
  }
  return ans;
}

// ───────────────────────── relevance scoring ─────────────────────────

function keywordScore(title, s) {
  const t = title.toLowerCase();
  const inc = s.includeTerms.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean);
  const exc = s.excludeTerms.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean);
  if (exc.some((x) => t.includes(x))) return 0;
  if (!inc.length) return 70;
  return inc.some((x) => t.includes(x)) ? 80 : 0;
}

async function llmScore(job, s) {
  const out = await chat(s,
    "You score job-candidate fit. Reply with ONLY an integer 0-100.",
    `Candidate:\n${s.cvText.slice(0, 1500)}\n\nJob: ${job.title} at ${job.company} (${job.location})\nReply with the fit score 0-100.`, 8);
  const n = parseInt((out.match(/\d+/) || [])[0], 10);
  return Number.isFinite(n) ? n : null;
}

// ───────────────────────── board scanning (free ATS APIs) ─────────────────────────

async function fetchBoard(entry) {
  const [ats, slug] = entry.split(":").map((x) => x.trim());
  if (!ats || !slug || slug.includes("*")) return [];
  try {
    if (ats === "greenhouse") {
      const r = await fetch(`https://boards-api.greenhouse.io/v1/boards/${slug}/jobs`);
      if (!r.ok) return [];
      return (await r.json()).jobs.map((j) => ({
        title: j.title, company: slug, location: j.location?.name || "",
        url: j.absolute_url,
      }));
    }
    if (ats === "lever") {
      const r = await fetch(`https://api.lever.co/v0/postings/${slug}?mode=json`);
      if (!r.ok) return [];
      return (await r.json()).map((j) => ({
        title: j.text, company: slug, location: j.categories?.location || "",
        url: j.hostedUrl,
      }));
    }
    if (ats === "ashby") {
      const r = await fetch(`https://api.ashbyhq.com/posting-api/job-board/${slug}`);
      if (!r.ok) return [];
      return (await r.json()).jobs.map((j) => ({
        title: j.title, company: slug, location: j.location || "",
        url: j.jobUrl || j.applyUrl,
      }));
    }
  } catch { /* board unreachable — skip */ }
  return [];
}

function locationOk(job, s) {
  const wanted = s.locationTerms.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean);
  if (!wanted.length) return true;
  const loc = (job.location || "").toLowerCase();
  return wanted.some((w) => loc.includes(w));
}

async function runScan(manual = false) {
  const s = await getState();
  if (!s.enabled && !manual) return;
  const stats = s.stats.day === todayKey()
    ? s.stats : { ...s.stats, day: todayKey(), appliedToday: 0 };
  if (stats.appliedToday >= s.maxPerDay) return;

  await save({ stats: { ...stats, lastScan: new Date().toISOString() } });
  // Dedup against both: already-submitted AND already-opened this run.
  const seenSet = new Set([...(s.seen || []), ...s.applied]);
  let opened = 0;

  for (const entry of s.watchlist.split("\n").map((x) => x.trim()).filter(Boolean)) {
    if (opened >= s.maxPerScan) break;
    for (const job of await fetchBoard(entry)) {
      if (opened >= s.maxPerScan) break;
      if (!job.url || seenSet.has(job.url) || !locationOk(job, s)) continue;
      let score = keywordScore(job.title, s);
      if (score === 0) continue;
      const ai = await llmScore(job, s);          // null when no LLM configured
      if (ai !== null) score = ai;
      if (score < s.minScore) continue;

      const tab = await chrome.tabs.create({ url: job.url, active: false });
      pendingJobs.set(tab.id, job);
      seenSet.add(job.url);                       // don't reopen while pending
      opened++;
    }
  }
  await save({ seen: [...seenSet].slice(-2000) });
}

// ───────────────────────── auto-apply orchestration ─────────────────────────

const pendingJobs = new Map();   // tabId -> job (in-memory; alarms restart scans anyway)

chrome.tabs.onUpdated.addListener(async (tabId, info) => {
  if (info.status !== "complete" || !pendingJobs.has(tabId)) return;
  const job = pendingJobs.get(tabId);
  const s = await getState();
  setTimeout(() => {
    chrome.tabs.sendMessage(tabId, {
      type: "AUTO_APPLY", job, autoSubmit: s.autoSubmit,
    }).catch(() => {});
  }, 4000); // let the SPA hydrate
});

chrome.tabs.onRemoved.addListener((tabId) => pendingJobs.delete(tabId));

// ───────────────────────── message router ─────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    if (msg.type === "ANSWER") {
      sendResponse({ answer: await answerQuestion(msg) });
    } else if (msg.type === "GET_SETTINGS") {
      sendResponse(await getState());
    } else if (msg.type === "APPLY_DONE") {
      await bumpApplied(msg.url, msg.submitted);
      chrome.notifications.create({
        type: "basic", iconUrl: "icons/icon128.png",
        title: msg.submitted ? "Application submitted" : "Application filled (review it)",
        message: `${msg.jobTitle || msg.url}`,
      });
      if (msg.submitted && sender.tab?.id && pendingJobs.has(sender.tab.id)) {
        setTimeout(() => chrome.tabs.remove(sender.tab.id).catch(() => {}), 5000);
      }
      sendResponse({ ok: true });
    } else if (msg.type === "RUN_NOW") {
      runScan(true);
      sendResponse({ ok: true });
    }
  })();
  return true; // async response
});

// ───────────────────────── lifecycle ─────────────────────────

chrome.runtime.onInstalled.addListener(async () => {
  const s = await getState();
  await save(s); // persist defaults
  chrome.alarms.create(ALARM, { periodInMinutes: s.scanIntervalMinutes });
});
chrome.runtime.onStartup.addListener(async () => {
  const s = await getState();
  chrome.alarms.create(ALARM, { periodInMinutes: s.scanIntervalMinutes });
});
chrome.alarms.onAlarm.addListener((a) => { if (a.name === ALARM) runScan(); });
chrome.storage.onChanged.addListener((changes) => {
  if (changes.scanIntervalMinutes) {
    chrome.alarms.create(ALARM, { periodInMinutes: changes.scanIntervalMinutes.newValue });
  }
});
