const $ = (id) => document.getElementById(id);

async function refresh() {
  const s = await chrome.storage.local.get(null);
  $("enabled").checked = !!s.enabled;
  const today = s.stats?.day === new Date().toISOString().slice(0, 10)
    ? s.stats.appliedToday : 0;
  $("today").textContent = today ?? 0;
  $("total").textContent = s.stats?.totalApplied ?? 0;
  $("answers").textContent = (s.answers || []).length;
  $("lastScan").textContent = s.stats?.lastScan
    ? new Date(s.stats.lastScan).toLocaleTimeString() : "never";
  $("mode").textContent = s.autoSubmit
    ? "Mode: auto-submit ON — applications are submitted without review."
    : "Mode: fill-only — you review and click submit yourself.";
}

$("enabled").addEventListener("change", async (e) => {
  await chrome.storage.local.set({ enabled: e.target.checked });
});
$("runNow").addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "RUN_NOW" });
  setTimeout(refresh, 1500);
});
$("fillPage").addEventListener("click", async () => {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab?.id) chrome.tabs.sendMessage(tab.id, { type: "FILL_NOW" }).catch(() => {});
});
$("openOptions").addEventListener("click", () => chrome.runtime.openOptionsPage());

refresh();
setInterval(refresh, 3000);
