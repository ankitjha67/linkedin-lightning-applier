"""Tests for profile_setup.gather_profile_text (the /setup documents reader)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import profile_setup as ps


class TestGatherProfileText(unittest.TestCase):
    def _docs(self):
        d = tempfile.mkdtemp()
        Path(d, "cv").mkdir()
        Path(d, "cv", "master.md").write_text("# CV\nCredit risk manager, Basel III.")
        Path(d, "cv", "notes.txt").write_text("7 years experience in RWA modelling.")
        Path(d, "cv", ".gitkeep").write_text("")
        Path(d, "cover.png").write_bytes(b"\x89PNG binary")  # unsupported
        return d

    def test_reads_text_files(self):
        d = self._docs()
        out = ps.gather_profile_text(d)
        self.assertIn("Credit risk manager", out["text"])
        self.assertIn("RWA modelling", out["text"])
        self.assertIn(os.path.join("cv", "master.md"), out["read"])

    def test_skips_unsupported_and_gitkeep(self):
        d = self._docs()
        out = ps.gather_profile_text(d)
        self.assertIn("cover.png", out["skipped"])
        # .gitkeep is ignored entirely (not in found/read/skipped)
        self.assertFalse(any(".gitkeep" in f for f in out["found"]))

    def test_missing_dir(self):
        out = ps.gather_profile_text("/no/such/dir")
        self.assertEqual(out["text"], "")
        self.assertEqual(out["read"], [])

    def test_truncation(self):
        d = tempfile.mkdtemp()
        Path(d, "big.txt").write_text("x" * 5000)
        out = ps.gather_profile_text(d, max_chars=1000)
        self.assertTrue(out["truncated"])
        self.assertLessEqual(len(out["text"]), 1100)  # header + capped body

    def test_read_file_text_unknown_ext(self):
        d = tempfile.mkdtemp()
        p = Path(d, "x.bin")
        p.write_bytes(b"\x00\x01")
        self.assertEqual(ps.read_file_text(str(p)), "")


if __name__ == "__main__":
    unittest.main()
