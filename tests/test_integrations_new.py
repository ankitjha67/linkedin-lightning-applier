"""
Tests for autopilot-jobhunt-inspired integrations:
- New AI providers (openrouter free chain, claude_cli)
- Careers-page scanner (companies.json + ATS APIs)
- Protocol-agnostic tools layer (MCP foundation)
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestNewAIProviders(unittest.TestCase):
    """OpenRouter free chain + Claude CLI provider."""

    def test_openrouter_provider_init(self):
        from ai import AIAnswerer, OPENROUTER_FREE_CHAIN
        ai = AIAnswerer({"ai": {"enabled": True, "provider": "openrouter", "api_key": "test"}})
        self.assertEqual(ai.provider, "openrouter")
        self.assertEqual(ai.base_url, "https://openrouter.ai/api/v1")
        self.assertEqual(ai.openrouter_chain, OPENROUTER_FREE_CHAIN)
        self.assertEqual(len(ai.openrouter_chain), 4)

    def test_openrouter_custom_chain(self):
        from ai import AIAnswerer
        custom = ["model-a:free", "model-b:free"]
        ai = AIAnswerer({"ai": {"enabled": True, "provider": "openrouter",
                                "api_key": "k", "openrouter_fallback_models": custom}})
        self.assertEqual(ai.openrouter_chain, custom)

    def test_claude_cli_provider_init(self):
        from ai import AIAnswerer
        ai = AIAnswerer({"ai": {"enabled": True, "provider": "claude_cli",
                                "claude_cli_model": "sonnet"}})
        self.assertEqual(ai.provider, "claude_cli")
        self.assertEqual(ai.claude_cli_model, "sonnet")

    def test_claude_cli_client_sentinel(self):
        from ai import AIAnswerer
        ai = AIAnswerer({"ai": {"enabled": True, "provider": "claude_cli"}})
        # claude_cli returns a sentinel (no HTTP client needed)
        self.assertEqual(ai.client, "claude_cli")

    def test_openrouter_default_model(self):
        from ai import AIAnswerer
        ai = AIAnswerer({"ai": {"enabled": True, "provider": "openrouter", "api_key": "k"}})
        self.assertTrue(ai.model.endswith(":free"))


class TestCareersScanner(unittest.TestCase):
    """Curated company careers-page scanner."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmpdir, "data"), exist_ok=True)
        from state import State
        self.state = State(os.path.join(self.tmpdir, "data", "test.db"))
        # Write a small companies file
        self.companies_path = os.path.join(self.tmpdir, "companies.json")
        with open(self.companies_path, "w") as f:
            json.dump([
                {"name": "Stripe", "careers_url": "https://stripe.com/jobs",
                 "ats": "greenhouse", "location": "Remote", "region": "Remote"},
                {"name": "Mistral AI", "careers_url": "https://mistral.ai/careers",
                 "ats": "lever", "location": "Paris", "region": "EU"},
            ], f)

    def tearDown(self):
        self.state.close()

    def test_load_companies(self):
        from careers_scanner import CareersScanner
        cs = CareersScanner(
            {"careers_scanner": {"enabled": True, "companies_file": self.companies_path}},
            self.state)
        companies = cs.load_companies()
        self.assertEqual(len(companies), 2)

    def test_region_filter(self):
        from careers_scanner import CareersScanner
        cs = CareersScanner(
            {"careers_scanner": {"enabled": True, "companies_file": self.companies_path,
                                 "regions": ["EU"]}},
            self.state)
        companies = cs.load_companies()
        self.assertEqual(len(companies), 1)
        self.assertEqual(companies[0]["name"], "Mistral AI")

    def test_ats_slug_derivation(self):
        from careers_scanner import CareersScanner
        cs = CareersScanner({"careers_scanner": {"enabled": True}}, self.state)
        self.assertEqual(cs._ats_slug({"name": "Stripe"}), "stripe")
        self.assertEqual(cs._ats_slug({"name": "Mistral AI"}), "mistralai")
        self.assertEqual(cs._ats_slug({"name": "X", "ats_slug": "custom"}), "custom")

    def test_html_strip(self):
        from careers_scanner import CareersScanner
        cs = CareersScanner({"careers_scanner": {"enabled": True}}, self.state)
        self.assertEqual(cs._strip_html("<p>Hello <b>world</b></p>"), "Hello world")
        self.assertEqual(cs._strip_html("A&amp;B"), "A&B")
        self.assertEqual(cs._strip_html(""), "")

    def test_disabled_scanner_returns_empty(self):
        from careers_scanner import CareersScanner
        cs = CareersScanner({"careers_scanner": {"enabled": False}}, self.state)
        self.assertEqual(cs.scan(), [])

    def test_scan_summary_formatting(self):
        from careers_scanner import CareersScanner
        cs = CareersScanner({"careers_scanner": {"enabled": True}}, self.state)
        jobs = [{"company": "Stripe", "title": "ML Engineer", "score": 85,
                 "location": "Remote", "url": "https://x.com/1"}]
        summary = cs.get_scan_summary(jobs)
        self.assertIn("Stripe", summary)
        self.assertIn("ML Engineer", summary)
        self.assertIn("85", summary)

    def test_empty_scan_summary(self):
        from careers_scanner import CareersScanner
        cs = CareersScanner({"careers_scanner": {"enabled": True}}, self.state)
        self.assertIn("No new", cs.get_scan_summary([]))


class TestToolsLayer(unittest.TestCase):
    """Protocol-agnostic tools layer (MCP foundation)."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmpdir, "data"), exist_ok=True)
        self.old_cwd = os.getcwd()
        os.chdir(self.tmpdir)
        with open("config.yaml", "w") as f:
            f.write("ai:\n  enabled: false\nsearch:\n  search_terms: [Eng]\n  search_locations: [NYC]\npersonal:\n  first_name: Test\n")

    def tearDown(self):
        os.chdir(self.old_cwd)

    def test_tool_stats_returns_string(self):
        import tools_layer
        result = tools_layer.tool_stats()
        self.assertIsInstance(result, str)
        self.assertIn("Statistics", result)

    def test_tool_list_applied_empty(self):
        import tools_layer
        result = tools_layer.tool_list_applied(5)
        self.assertIsInstance(result, str)

    def test_tool_salary_benchmark(self):
        import tools_layer
        result = tools_layer.tool_salary_benchmark("Engineer", "NYC")
        self.assertIsInstance(result, str)

    def test_all_13_tools_exist(self):
        import tools_layer
        tools = [x for x in dir(tools_layer) if x.startswith("tool_")]
        self.assertGreaterEqual(len(tools), 13)

    def test_tools_never_raise(self):
        """Every tool must return a string, never raise — even with no data."""
        import tools_layer
        for name in dir(tools_layer):
            if not name.startswith("tool_"):
                continue
            fn = getattr(tools_layer, name)
            if not callable(fn):
                continue
            import inspect
            sig = inspect.signature(fn)
            # Build minimal args
            args = []
            for p in sig.parameters.values():
                if p.default is inspect.Parameter.empty:
                    args.append("test")
            try:
                result = fn(*args)
                self.assertIsInstance(result, str, f"{name} returned non-string")
            except Exception as e:
                self.fail(f"{name} raised {e}")


class TestCompaniesDatabase(unittest.TestCase):
    """Verify the shipped companies.json is valid."""

    def test_companies_json_valid(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "companies.json")
        with open(path) as f:
            companies = json.load(f)
        self.assertGreater(len(companies), 20)
        for c in companies:
            self.assertIn("name", c)
            self.assertIn("careers_url", c)
            self.assertIn("region", c)


if __name__ == "__main__":
    unittest.main()
