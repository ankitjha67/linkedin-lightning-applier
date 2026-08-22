"""
Validation tests for the browser extension (browser_extension/).

No Node in CI, so these check what Python can: the MV3 manifest is valid and
complete, every referenced file exists, the provider placeholders (NVIDIA /
frontier / local) are present, and no secrets are baked into the source.
"""

import json
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EXT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "browser_extension")


def _read(*parts):
    with open(os.path.join(EXT, *parts), encoding="utf-8") as fh:
        return fh.read()


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.m = json.loads(_read("manifest.json"))

    def test_mv3_and_core_keys(self):
        self.assertEqual(self.m["manifest_version"], 3)
        for key in ("name", "version", "background", "content_scripts",
                    "options_page", "action", "permissions", "icons"):
            self.assertIn(key, self.m)

    def test_permissions(self):
        for p in ("storage", "alarms", "tabs", "notifications"):
            self.assertIn(p, self.m["permissions"])
        self.assertIn("<all_urls>", self.m["host_permissions"])

    def test_service_worker_is_module(self):
        self.assertEqual(self.m["background"]["service_worker"], "background.js")
        self.assertEqual(self.m["background"]["type"], "module")

    def test_content_script_boards(self):
        matches = " ".join(self.m["content_scripts"][0]["matches"])
        for host in ("greenhouse.io", "lever.co", "ashbyhq.com",
                     "myworkdayjobs.com", "smartrecruiters.com", "workable.com",
                     "icims.com", "taleo.net"):
            self.assertIn(host, matches)
        self.assertTrue(self.m["content_scripts"][0]["all_frames"])

    def test_all_referenced_files_exist(self):
        files = [self.m["background"]["service_worker"], self.m["options_page"],
                 self.m["action"]["default_popup"]]
        files += self.m["content_scripts"][0]["js"]
        files += list(self.m["icons"].values())
        for f in files:
            self.assertTrue(os.path.exists(os.path.join(EXT, f)), f)


class TestProviderPlaceholders(unittest.TestCase):
    def setUp(self):
        self.llm = _read("lib", "llm.js")

    def test_nvidia_placeholder(self):
        self.assertIn("integrate.api.nvidia.com", self.llm)
        self.assertIn("nemotron", self.llm.lower())

    def test_frontier_placeholders(self):
        for host in ("api.openai.com", "api.anthropic.com",
                     "generativelanguage.googleapis.com", "openrouter.ai",
                     "api.x.ai", "api.mistral.ai", "api.groq.com"):
            self.assertIn(host, self.llm)

    def test_local_placeholders(self):
        self.assertIn("localhost:11434", self.llm)   # Ollama
        self.assertIn("localhost:1234", self.llm)    # LM Studio
        self.assertIn("custom", self.llm)
        # Local providers must not demand a key.
        self.assertIn("needsKey: false", self.llm)

    def test_no_real_keys_committed(self):
        for fname in ("lib/llm.js", "background.js", "options.js",
                      "content/filler.js", "popup.js"):
            src = _read(*fname.split("/"))
            self.assertIsNone(
                re.search(r"sk-[A-Za-z0-9]{20}|nvapi-[A-Za-z0-9]{20}|AIza[0-9A-Za-z_-]{30}", src),
                f"possible real key in {fname}")


class TestBehaviorContracts(unittest.TestCase):
    def test_background_has_24x7_loop(self):
        bg = _read("background.js")
        self.assertIn("chrome.alarms.create", bg)
        self.assertIn("onAlarm", bg)
        self.assertIn("maxPerDay", bg)
        self.assertIn("boards-api.greenhouse.io", bg)
        self.assertIn("api.lever.co", bg)
        self.assertIn("api.ashbyhq.com", bg)

    def test_background_defaults_are_safe(self):
        bg = _read("background.js")
        self.assertIn("enabled: false", bg)      # off until the user opts in
        self.assertIn("autoSubmit: false", bg)   # fill-only by default

    def test_filler_has_work_auth_and_resume(self):
        f = _read("content", "filler.js")
        self.assertIn("workAuthAnswer", f)
        self.assertIn("DataTransfer", f)         # resume attach
        self.assertIn("AUTO_APPLY", f)
        # sponsorship inversion present
        self.assertIn('ok ? "No" : "Yes"', f)

    def test_answer_memory_us_not_stopword(self):
        bg = _read("background.js")
        stop_line = re.search(r'STOP = new Set\(\("([^"]+)"', bg).group(1)
        self.assertNotIn(" us ", f" {stop_line} ")

    def test_icons_are_valid_png(self):
        for size in (16, 48, 128):
            path = os.path.join(EXT, "icons", f"icon{size}.png")
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")


if __name__ == "__main__":
    unittest.main()
