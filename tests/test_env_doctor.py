"""
Tests for the environment doctor (env_doctor.py) — version parsing, package
checks, mismatch self-heal parsing, and the report structure. Subprocess/pip
are mocked; nothing is installed during tests.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import env_doctor as ed


class TestPython(unittest.TestCase):
    def test_python_ok_structure(self):
        ok, ver = ed.python_ok()
        self.assertTrue(ok)  # tests run on >=3.10
        self.assertRegex(ver, r"^\d+\.\d+\.\d+$")


class TestVersionParsing(unittest.TestCase):
    def test_version_from_output(self):
        self.assertEqual(ed._version_from_output("Google Chrome 126.0.6478.127"), 126)
        self.assertEqual(ed._version_from_output("Chromium 121.0.6167.85 snap"), 121)
        self.assertIsNone(ed._version_from_output("no version here"))
        self.assertIsNone(ed._version_from_output(""))

    def test_mismatch_error_parsing(self):
        err = ("session not created: This version of ChromeDriver only supports "
               "Chrome version 127\nCurrent browser version is 126.0.6478.127")
        self.assertEqual(ed.chrome_version_from_error(err), 127)
        self.assertIsNone(ed.chrome_version_from_error("some other error"))
        self.assertIsNone(ed.chrome_version_from_error(None))

    def test_detect_returns_int_or_none(self):
        v = ed.detect_chrome_version()
        self.assertTrue(v is None or (isinstance(v, int) and v > 60))


class TestPackages(unittest.TestCase):
    def test_check_packages_shape(self):
        pkgs = ed.check_packages()
        names = {p["name"] for p in pkgs}
        self.assertIn("selenium", names)
        self.assertIn("PyYAML", names)
        self.assertIn("fpdf2", names)
        for p in pkgs:
            self.assertIn(p["kind"], ("required", "optional"))

    def test_required_only(self):
        pkgs = ed.check_packages(include_optional=False)
        self.assertTrue(all(p["kind"] == "required" for p in pkgs))

    def test_installed_detection(self):
        # PyYAML is installed in this environment; a fake package is not.
        self.assertIsNotNone(ed._installed_version("PyYAML"))
        self.assertIsNone(ed._installed_version("definitely-not-a-real-package-xyz"))

    def test_install_packages_invokes_current_interpreter(self):
        ed._build_tools_bootstrapped = True  # skip the bootstrap pip call
        with patch.object(ed.subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 0
            self.assertTrue(ed.install_packages(["foo", "bar"]))
        # One pip call per package (installed independently).
        installed = [c.args[0][-1] for c in mock_run.call_args_list]
        self.assertIn("foo", installed)
        self.assertIn("bar", installed)
        for c in mock_run.call_args_list:
            self.assertEqual(c.args[0][:4], [sys.executable, "-m", "pip", "install"])

    def test_one_failure_does_not_block_rest(self):
        ed._build_tools_bootstrapped = True
        with patch.object(ed, "_pip_install_one",
                          side_effect=[False, True]) as mock_one:
            ok = ed.install_packages(["bad", "good"])
        self.assertFalse(ok)                       # overall failure reported
        self.assertEqual(mock_one.call_count, 2)   # but 'good' still attempted

    def test_install_upgrade_flag(self):
        ed._build_tools_bootstrapped = True
        with patch.object(ed.subprocess, "run") as mock_run:
            mock_run.return_value.returncode = 0
            ed.install_packages(["selenium"], upgrade=True)
            self.assertTrue(any("--upgrade" in c.args[0]
                                for c in mock_run.call_args_list))

    def test_install_empty_noop(self):
        with patch.object(ed.subprocess, "run") as mock_run:
            self.assertTrue(ed.install_packages([]))
            mock_run.assert_not_called()


class TestDoctorReport(unittest.TestCase):
    def test_report_structure_no_fix(self):
        with patch.object(ed, "install_packages") as mock_install:
            rep = ed.run_doctor(fix=False, upgrade=False)
            mock_install.assert_not_called()
        for key in ("python", "chrome", "packages", "missing_required",
                    "installed_now", "upgraded_now", "tools", "ok"):
            self.assertIn(key, rep)

    def test_fix_installs_missing_required(self):
        fake = [{"name": "ghost-pkg", "module": "ghost", "kind": "required",
                 "installed": None}]
        with patch.object(ed, "check_packages", return_value=fake), \
             patch.object(ed, "install_packages", return_value=True) as mock_install:
            rep = ed.run_doctor(fix=True, include_optional=False)
        mock_install.assert_called_once_with(["ghost-pkg"])
        self.assertEqual(rep["installed_now"], ["ghost-pkg"])
        self.assertEqual(rep["missing_required"], [])
        self.assertTrue(rep["ok"])

    def test_upgrade_targets_browser_stack(self):
        with patch.object(ed, "install_packages", return_value=True) as mock_install:
            rep = ed.run_doctor(upgrade=True, include_optional=False)
        args, kwargs = mock_install.call_args
        self.assertIn("undetected-chromedriver", args[0])
        self.assertTrue(kwargs.get("upgrade"))
        self.assertEqual(rep["upgraded_now"], ["undetected-chromedriver", "selenium"])

    def test_tools_shape(self):
        t = ed.check_optional_tools()
        for key in ("latex_engine", "pdftotext", "ollama_running", "lmstudio_running"):
            self.assertIn(key, t)


if __name__ == "__main__":
    unittest.main()
