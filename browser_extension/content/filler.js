// Lightning Applier — content script: fills ATS application forms in-page.
//
// Runs on the supported job boards. When the background worker opens a posting
// it sends AUTO_APPLY; the filler sweeps every field: work-authorization logic
// first (per-country, deterministic), then the profile keyword map, then the
// background's answer-memory/LLM. Resume file inputs are satisfied from the
// stored resume via the DataTransfer trick. React-friendly events throughout.

(() => {
  if (window.__llaFiller) return; // one instance per frame
  window.__llaFiller = true;

  let SETTINGS = null;
  let JOB = { title: "", company: "", location: "" };

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ── work authorization (port of work_auth.py, per-country truth) ──
  const COUNTRIES = {
    india: ["india", "bharat"], "united kingdom": ["united kingdom", "uk", "great britain", "britain", "england", "scotland", "wales"],
    "united states": ["united states", "usa", "america", "u.s."],
    singapore: ["singapore"], canada: ["canada"], australia: ["australia"],
    "united arab emirates": ["united arab emirates", "uae", "emirates", "dubai", "abu dhabi"],
    "hong kong": ["hong kong"], germany: ["germany"], switzerland: ["switzerland"],
    netherlands: ["netherlands"], france: ["france"], ireland: ["ireland"],
    japan: ["japan"], "new zealand": ["new zealand"], qatar: ["qatar"],
    "saudi arabia": ["saudi arabia", "ksa"],
  };
  const CITY2COUNTRY = {
    london: "united kingdom", manchester: "united kingdom", "new york": "united states",
    "san francisco": "united states", toronto: "canada", sydney: "australia",
    dubai: "united arab emirates", "abu dhabi": "united arab emirates",
    frankfurt: "germany", dublin: "ireland", amsterdam: "netherlands",
    mumbai: "india", bangalore: "india", bengaluru: "india", gurugram: "india",
    gurgaon: "india", delhi: "india", pune: "india", hyderabad: "india",
  };

  function countryFrom(text) {
    if (!text) return "";
    const low = text.toLowerCase();
    if (/\bUS\b/.test(text)) return "united states";
    if (/\bUK\b/.test(text)) return "united kingdom";
    if (/\bIN\b/.test(text) && /visa|authori|work/i.test(text)) return "india";
    for (const [canon, aliases] of Object.entries(COUNTRIES)) {
      if (aliases.some((a) => low.includes(a))) return canon;
    }
    for (const [city, canon] of Object.entries(CITY2COUNTRY)) {
      if (low.includes(city)) return canon;
    }
    return "";
  }

  function workAuthAnswer(label) {
    const wa = SETTINGS?.workAuth || {};
    const covered = new Set(
      `${wa.citizenship || ""},${wa.visas || ""}`.split(",")
        .map((c) => countryFrom(c) || c.trim().toLowerCase()).filter(Boolean));
    if (!covered.size) return null;
    const l = label.toLowerCase();
    const isAuth = /authori[sz]ed|right to work|eligible to work|work permit|legally/.test(l);
    const isSponsor = /sponsor|require.{0,20}visa|need.{0,20}visa/.test(l);
    const isCitizen = /citizen/.test(l);
    if (!isAuth && !isSponsor && !isCitizen) return null;
    const country = countryFrom(label) || countryFrom(JOB.location);
    if (!country) return null;
    const citizenship = new Set((wa.citizenship || "").split(",")
      .map((c) => countryFrom(c) || c.trim().toLowerCase()).filter(Boolean));
    if (isCitizen && !isAuth && !isSponsor) return citizenship.has(country) ? "Yes" : "No";
    const ok = covered.has(country);
    if (isAuth) return ok ? "Yes" : "No";
    return ok ? "No" : "Yes"; // pure sponsorship question is inverted
  }

  // ── profile keyword map ──
  function profileAnswer(label) {
    const p = SETTINGS?.profile || {};
    const l = label.toLowerCase();
    const map = [
      [/first name|given name/, p.firstName],
      [/last name|surname|family name/, p.lastName],
      [/full name|^name$|your name/, p.fullName || `${p.firstName || ""} ${p.lastName || ""}`.trim()],
      [/e-?mail/, p.email],
      [/phone|mobile|telephone/, p.phone],
      [/city|town/, p.city],
      [/state|province/, p.state],
      [/zip|postal/, p.zip],
      [/country/, p.country],
      [/linkedin/, p.linkedin],
      [/github|portfolio|website/, p.github],
      [/years.*experience|total experience/, p.yearsExperience],
      [/current (company|employer)/, p.currentCompany],
      [/notice/, p.noticePeriod],
      [/salary|compensation|ctc/, p.salary],
      [/how did you hear/, "LinkedIn"],
    ];
    for (const [re, val] of map) if (re.test(l) && val) return String(val);
    return null;
  }

  // ── field helpers ──
  function fire(el) {
    for (const ev of ["input", "change", "blur"]) {
      el.dispatchEvent(new Event(ev, { bubbles: true }));
    }
  }
  function setText(el, value) {
    const proto = el.tagName === "TEXTAREA"
      ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
    setter ? setter.call(el, value) : (el.value = value);
    fire(el);
  }
  function labelFor(el) {
    const aria = el.getAttribute("aria-label"); if (aria) return aria.trim();
    const ariaBy = el.getAttribute("aria-labelledby");
    if (ariaBy) {
      const t = ariaBy.split(/\s+/).map((id) => document.getElementById(id)?.textContent || "").join(" ").trim();
      if (t) return t;
    }
    if (el.id) {
      const lab = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (lab?.textContent.trim()) return lab.textContent.trim();
    }
    const anc = el.closest("label") || el.closest("[data-automation-id]") || el.closest("div");
    const t = anc?.querySelector("label")?.textContent || anc?.textContent || "";
    const first = t.trim().split("\n")[0].trim();
    return first.length && first.length < 140 ? first : (el.placeholder || el.name || "");
  }

  async function resolveAnswer(label, options) {
    const wa = workAuthAnswer(label);
    if (wa !== null) {
      if (!options?.length) return wa;
      const fit = options.find((o) => o.toLowerCase() === wa.toLowerCase()) ||
        options.find((o) => o.toLowerCase().startsWith(wa.toLowerCase()));
      return fit || null; // never lie when options can't express the truth
    }
    const prof = profileAnswer(label);
    if (prof) return prof;
    try {
      const res = await chrome.runtime.sendMessage({
        type: "ANSWER", question: label, options,
        jobTitle: JOB.title, company: JOB.company, location: JOB.location,
        noSave: wa !== null,
      });
      return res?.answer || null;
    } catch { return null; }
  }

  // ── resume upload via DataTransfer ──
  function attachResume(input) {
    const { resumeB64, resumeName } = SETTINGS || {};
    if (!resumeB64 || !resumeName) return false;
    try {
      const bytes = Uint8Array.from(atob(resumeB64), (c) => c.charCodeAt(0));
      const file = new File([bytes], resumeName, { type: "application/pdf" });
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      fire(input);
      return true;
    } catch { return false; }
  }

  // ── the sweep ──
  async function sweep() {
    // Workday and some boards gate the form behind an Apply button
    for (const txt of ["apply manually", "apply now", "apply", "i'm interested", "apply for this job"]) {
      const btn = [...document.querySelectorAll("button, a[role=button]")]
        .find((b) => b.offsetParent && b.textContent.trim().toLowerCase() === txt);
      if (btn) { btn.click(); await sleep(2500); break; }
    }

    for (const fi of document.querySelectorAll("input[type=file]")) attachResume(fi);

    for (const el of document.querySelectorAll(
      "input[type=text], input[type=email], input[type=tel], input[type=url], input[type=number], input:not([type]), textarea")) {
      if (!el.offsetParent || el.value.trim()) continue;
      const label = labelFor(el);
      if (!label) continue;
      const ans = await resolveAnswer(label, null);
      if (ans) setText(el, ans);
    }

    for (const sel of document.querySelectorAll("select")) {
      if (!sel.offsetParent) continue;
      const options = [...sel.options].map((o) => o.text.trim())
        .filter((t) => t && !/^select|^choose|^--/i.test(t));
      if (!options.length) continue;
      const cur = sel.options[sel.selectedIndex]?.text.trim().toLowerCase();
      if (cur && !/^select|^choose|^--/i.test(cur)) continue;
      const ans = await resolveAnswer(labelFor(sel), options);
      if (!ans) continue;
      const idx = [...sel.options].findIndex((o) =>
        o.text.trim().toLowerCase() === ans.toLowerCase() ||
        o.text.trim().toLowerCase().includes(ans.toLowerCase()));
      if (idx >= 0) { sel.selectedIndex = idx; fire(sel); }
    }

    for (const fs of document.querySelectorAll("fieldset, [role=radiogroup]")) {
      const radios = [...fs.querySelectorAll("input[type=radio]")];
      if (!radios.length || radios.some((r) => r.checked)) continue;
      const q = fs.querySelector("legend, label")?.textContent.trim();
      const labels = radios.map((r) => labelFor(r));
      if (!q || !labels.some(Boolean)) continue;
      const ans = await resolveAnswer(q, labels);
      if (!ans) continue;
      const i = labels.findIndex((l) => l && l.toLowerCase().includes(ans.toLowerCase()));
      if (i >= 0) { radios[i].click(); fire(radios[i]); }
    }

    for (const cb of document.querySelectorAll("input[type=checkbox]")) {
      if (cb.checked || !cb.offsetParent) continue;
      const ctx = (cb.closest("label, div")?.textContent || "").toLowerCase();
      if (/agree|consent|acknowledg|certif|i have read|privacy|terms/.test(ctx)) {
        cb.click(); fire(cb);
      }
    }
  }

  function findSubmit() {
    const labels = ["submit application", "submit", "send application", "apply"];
    return [...document.querySelectorAll("button, input[type=submit]")]
      .find((b) => b.offsetParent && !b.disabled &&
        labels.includes((b.textContent || b.value || "").trim().toLowerCase()));
  }

  async function autoApply(job, autoSubmit) {
    JOB = job || JOB;
    SETTINGS = await chrome.runtime.sendMessage({ type: "GET_SETTINGS" });
    await sweep();
    await sleep(1000);
    await sweep(); // second pass: conditional fields revealed by the first
    let submitted = false;
    if (autoSubmit) {
      const btn = findSubmit();
      if (btn) { btn.click(); submitted = true; await sleep(3000); }
    }
    chrome.runtime.sendMessage({
      type: "APPLY_DONE", url: location.href.split("?")[0],
      jobTitle: JOB.title, submitted,
    }).catch(() => {});
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg.type === "AUTO_APPLY") {
      autoApply(msg.job, msg.autoSubmit);
      sendResponse({ ok: true });
    } else if (msg.type === "FILL_NOW") {  // manual trigger from popup
      autoApply(msg.job || {}, false);
      sendResponse({ ok: true });
    }
    return true;
  });
})();
