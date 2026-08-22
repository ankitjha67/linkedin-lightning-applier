#!/usr/bin/env node
/**
 * Browser end-to-end test for the Lightning Applier extension.
 *
 * Loads the unpacked extension into a real Chrome/Chromium and drives it the
 * way a person does — install, configure through the Settings UI, open a job
 * posting, click "Fill this page" — then reports every console error, page
 * exception and behavioural failure it saw.
 *
 * This is the harness that caught four real bugs unit tests could not,
 * including a consent question being answered with the notice period on the
 * live Greenhouse form. Run it after touching anything in browser_extension/.
 *
 *   node tests/e2e/extension-e2e.js                     # default live posting
 *   node tests/e2e/extension-e2e.js --headed            # watch it happen
 *   node tests/e2e/extension-e2e.js --url <job-url>     # any ATS posting
 *   node tests/e2e/extension-e2e.js --profile "C:/Users/you/lla-profile"
 *
 * --profile reuses a persistent Chrome profile, so sites you are already
 * logged into (LinkedIn, a Workday tenant) stay logged in between runs. That
 * is the one thing a throwaway profile cannot test.
 *
 * Exit code is 0 only when no bugs were found, so it can gate CI.
 */

const { chromium } = require("playwright");
const fs = require("fs");
const os = require("os");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const EXT = path.join(ROOT, "browser_extension");

// ── args ──────────────────────────────────────────────────────────────────
const argv = process.argv.slice(2);
const flag = (n, d = null) => {
  const i = argv.indexOf(`--${n}`);
  return i >= 0 ? (argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[i + 1] : true) : d;
};
const HEADED = !!flag("headed", false);
const JOB_URL = flag("url", "https://job-boards.greenhouse.io/monzo/jobs/7991227");
const PROFILE = flag("profile", null);
const SHOTS = flag("shots", path.join(os.tmpdir(), "lla-e2e-shots"));
const KEEP_OPEN = !!flag("keep-open", false);

const findings = [];
const log = (...a) => console.log(...a);
const bug = (sev, where, msg) => {
  findings.push({ sev, where, msg });
  log(`  ${sev === "BUG" ? "🐞" : "⚠️ "} [${where}] ${msg}`);
};

// Noise that belongs to the job board, not to us.
const IGNORE = /favicon|ERR_CERT|self-signed|Failed to load resource|Autofill|third-party cookie|Deprecat|preload|React error|Unauthorized|net::ERR_ABORTED/i;
function watch(target, label) {
  target.on("console", (m) => {
    const t = m.type();
    if (t !== "error" && t !== "warning") return;
    const text = m.text();
    if (IGNORE.test(text)) return;
    bug(t === "error" ? "BUG" : "WARN", label, `console.${t}: ${text.slice(0, 190)}`);
  });
  target.on("pageerror", (e) => {
    const s = String(e);
    if (IGNORE.test(s)) return;
    bug("BUG", label, `uncaught: ${s.slice(0, 190)}`);
  });
}

// Profile used to configure the extension for the run.
const PROFILE_DATA = {
  firstName: "Test", lastName: "Candidate",
  email: "test.candidate@example.com", phone: "+91 9876543210",
  city: "Gurugram", country: "India",
  linkedin: "https://linkedin.com/in/test-candidate",
  yearsExperience: "7", currentCompany: "Example Corp",
  noticePeriod: "30 days", salary: "Negotiable",
  citizenship: "India",
  cvText: "Credit risk manager, 7+ yrs: Basel III, IRB, PD/LGD/EAD, RWA, Python, SQL.",
};

(async () => {
  fs.mkdirSync(SHOTS, { recursive: true });
  if (!fs.existsSync(path.join(EXT, "manifest.json"))) {
    console.error(`No manifest.json in ${EXT}`);
    process.exit(1);
  }

  const userDataDir = PROFILE || fs.mkdtempSync(path.join(os.tmpdir(), "lla-e2e-"));
  log(`\n⚡ Lightning Applier — extension E2E`);
  log(`   extension : ${EXT}`);
  log(`   profile   : ${userDataDir}${PROFILE ? " (persistent — logins kept)" : " (throwaway)"}`);
  log(`   posting   : ${JOB_URL}\n`);

  const launch = {
    headless: !HEADED,
    viewport: { width: 1300, height: 1000 },
    ignoreHTTPSErrors: true,
    args: [
      ...(HEADED ? [] : ["--headless=new"]),
      "--no-sandbox", "--disable-dev-shm-usage",
      `--disable-extensions-except=${EXT}`, `--load-extension=${EXT}`,
    ],
  };
  // Sandboxed/CI environments often export a proxy that Chrome cannot use.
  if (process.env.LLA_E2E_NO_PROXY) {
    launch.args.push("--no-proxy-server", "--ignore-certificate-errors");
    launch.env = { ...process.env, HTTPS_PROXY: "", https_proxy: "", HTTP_PROXY: "", http_proxy: "" };
  }
  if (process.env.LLA_E2E_CHROME) launch.executablePath = process.env.LLA_E2E_CHROME;
  else if (!process.env.PLAYWRIGHT_BROWSERS_PATH) launch.channel = "chrome"; // the user's real Chrome

  const ctx = await chromium.launchPersistentContext(userDataDir, launch);

  // ── 1. install ──
  let sw = ctx.serviceWorkers()[0];
  if (!sw) {
    try { sw = await ctx.waitForEvent("serviceworker", { timeout: 20000 }); } catch { /* below */ }
  }
  if (!sw) {
    bug("BUG", "install", "service worker never registered — background.js failed to load");
    return finish(ctx);
  }
  watch(sw, "service-worker");
  const id = new URL(sw.url()).host;
  log(`━━ 1. INSTALLED — ${id}`);

  // ── 2. settings ──
  log(`━━ 2. SETTINGS — configuring through the real UI`);
  const opts = await ctx.newPage(); watch(opts, "options");
  await opts.goto(`chrome-extension://${id}/options.html`);
  await opts.waitForTimeout(900);

  const providers = await opts.evaluate(() => document.getElementById("provider")?.options?.length || 0);
  if (!providers) bug("BUG", "options", "provider dropdown empty — options.js module import failed");
  else log(`   providers offered: ${providers}`);

  await opts.click("#grpLocal").catch(() => bug("BUG", "options", "LOCAL provider tier not clickable"));
  for (const [k, v] of Object.entries(PROFILE_DATA)) {
    await opts.fill(`#${k}`, v).catch(() => bug("WARN", "options", `no field #${k}`));
  }
  await opts.click("#save");
  await opts.waitForTimeout(900);

  const saved = await opts.evaluate(async () => {
    const s = await chrome.storage.local.get(null);
    return { first: s.profile?.firstName, citizenship: s.workAuth?.citizenship, provider: s.llm?.provider };
  });
  if (saved.first !== PROFILE_DATA.firstName) bug("BUG", "options", "profile did not persist");
  if (saved.citizenship !== PROFILE_DATA.citizenship) bug("BUG", "options", "work authorization did not persist");
  await opts.reload(); await opts.waitForTimeout(800);
  const rt = await opts.evaluate(() => document.getElementById("firstName").value);
  if (rt !== PROFILE_DATA.firstName) bug("BUG", "options", "settings did not reload from storage");
  log(`   saved + round-tripped ✓  (${JSON.stringify(saved)})`);
  await opts.screenshot({ path: path.join(SHOTS, "options.png"), fullPage: true });

  // ── 3. popup ──
  log(`━━ 3. POPUP`);
  const pop = await ctx.newPage(); watch(pop, "popup");
  await pop.goto(`chrome-extension://${id}/popup.html`);
  await pop.waitForTimeout(800);
  const stats = await pop.evaluate(() => ({
    today: document.getElementById("today")?.textContent,
    mode: document.getElementById("mode")?.textContent,
  }));
  if (!stats.mode) bug("BUG", "popup", "mode footer empty — refresh() did not run");
  if (stats.today === "–") bug("BUG", "popup", "stats never populated");
  await pop.click("#enabled"); await pop.waitForTimeout(500);
  const on = await pop.evaluate(async () => (await chrome.storage.local.get("enabled")).enabled);
  if (on !== true) bug("BUG", "popup", "autopilot toggle did not persist");
  log(`   stats ✓  toggle ✓  (${JSON.stringify(stats)})`);
  await pop.screenshot({ path: path.join(SHOTS, "popup.png") });

  // ── 4. real posting ──
  log(`━━ 4. APPLY — ${JOB_URL}`);
  const job = await ctx.newPage(); watch(job, "job-page");
  try {
    await job.goto(JOB_URL, { waitUntil: "domcontentloaded", timeout: 60000 });
  } catch (e) {
    bug("WARN", "job-page", `posting unreachable: ${String(e).split("\n")[0].slice(0, 110)}`);
    return finish(ctx);
  }
  await job.waitForTimeout(8000);
  const before = await job.evaluate(() => ({
    text: document.querySelectorAll("input[type=text],input[type=email],input[type=tel],textarea").length,
    file: document.querySelectorAll("input[type=file]").length,
  }));
  log(`   form: ${before.text} text field(s), ${before.file} file input(s)`);
  await job.screenshot({ path: path.join(SHOTS, "job-before.png"), fullPage: true });

  // Trigger the fill exactly as the popup's button does. NOTE: this must be
  // sent from an extension page — the content script lives in an isolated
  // world, so page-context evaluate() can neither see it nor message it.
  const sent = await opts.evaluate(async (u) => {
    const tabs = await chrome.tabs.query({});
    const t = tabs.find((x) => x.url && x.url.startsWith(u));
    if (!t) return { error: "tab not found" };
    try { return { resp: await chrome.tabs.sendMessage(t.id, { type: "FILL_NOW" }) }; }
    catch (e) { return { error: String(e) }; }
  }, JOB_URL);
  if (sent.error) bug("BUG", "content-script", `FILL_NOW failed: ${sent.error}`);
  await job.waitForTimeout(12000);

  const after = await job.evaluate(() => {
    const labelOf = (el) => el.getAttribute("aria-label")
      || (el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`)?.textContent.trim())
      || el.name || el.id || "?";
    const out = { fields: [], files: [] };
    document.querySelectorAll("input[type=text],input[type=email],input[type=tel],textarea")
      .forEach((i) => out.fields.push({ q: labelOf(i).slice(0, 50), v: (i.value || "").slice(0, 44) }));
    document.querySelectorAll("input[type=file]")
      .forEach((f) => out.files.push({ n: f.name || f.id, count: f.files.length }));
    out.mentionsAttachment = /\.pdf|\.docx?/i.test(document.body.innerText);
    return out;
  });
  const filled = after.fields.filter((f) => f.v);
  log(`   filled ${filled.length}/${after.fields.length}:`);
  after.fields.forEach((f) => log(`      ${f.v ? "✓" : "·"} ${f.q.padEnd(52)} ${f.v ? `= "${f.v}"` : ""}`));
  if (!filled.length && after.fields.length) {
    bug("BUG", "content-script", `filled 0 of ${after.fields.length} fields`);
  }
  // The identity fields must always be filled — they need no LLM.
  for (const want of [["first name", PROFILE_DATA.firstName], ["last name", PROFILE_DATA.lastName],
                      ["email", PROFILE_DATA.email]]) {
    const f = after.fields.find((x) => new RegExp(want[0], "i").test(x.q));
    if (f && !f.v) bug("BUG", "content-script", `identity field "${f.q}" left empty (needs no LLM)`);
  }
  // A consent/privacy question must never receive a keyword answer.
  const consent = after.fields.find((f) => /privacy|consent|data safe|gdpr/i.test(f.q));
  if (consent && consent.v) {
    bug("BUG", "content-script",
        `consent question "${consent.q}" was auto-answered "${consent.v}" — prose labels must not be keyword-matched`);
  }
  await job.screenshot({ path: path.join(SHOTS, "job-after.png"), fullPage: true });

  // ── 5. accounting: fill-only must not count as applied ──
  const acct = await opts.evaluate(async () => {
    const s = await chrome.storage.local.get(null);
    return { applied: (s.applied || []).length, appliedToday: s.stats?.appliedToday || 0,
             autoSubmit: !!s.autoSubmit };
  });
  log(`━━ 5. ACCOUNTING — ${JSON.stringify(acct)}`);
  if (!acct.autoSubmit && acct.applied > 0) {
    bug("BUG", "background",
        `${acct.applied} job(s) marked applied while auto-submit is OFF — only real submissions may count`);
  }
  await finish(ctx);
})().catch((e) => { console.error("HARNESS ERROR:", e); process.exit(2); });

async function finish(ctx) {
  if (!KEEP_OPEN) await ctx.close().catch(() => {});
  const seen = new Set(), uniq = [];
  for (const f of findings) {
    const k = f.where + f.msg.slice(0, 70);
    if (!seen.has(k)) { seen.add(k); uniq.push(f); }
  }
  const bugs = uniq.filter((f) => f.sev === "BUG");
  log(`\n━━━━━━ RESULT ━━━━━━`);
  log(`  ${bugs.length} bug(s), ${uniq.length - bugs.length} warning(s)   screenshots: ${SHOTS}`);
  uniq.forEach((f) => log(`  [${f.sev}] ${f.where}: ${f.msg}`));
  if (!bugs.length) log(`  ✅ extension healthy`);
  process.exit(bugs.length ? 1 : 0);
}
