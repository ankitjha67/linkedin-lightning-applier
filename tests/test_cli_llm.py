"""
Tests for the `lla test-llm` connectivity probe (cli._probe_llm).

Pure-logic, no network: verifies the probe surfaces real errors instead of the
silent "" that AIAnswerer.generate() returns, routes the special backends to
generate(), and gives a clear hint when the client couldn't be built.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cli import _probe_llm


class TestProbeLLM(unittest.TestCase):
    def _openai_compatible_ai(self, content):
        ai = MagicMock()
        ai.provider = "openrouter"
        ai.model = "nvidia/nemotron-3-super-120b-a12b:free"
        ai.client.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content=content))
        ]
        return ai

    def test_returns_stripped_text(self):
        ai = self._openai_compatible_ai("  OK  ")
        self.assertEqual(_probe_llm(ai, "hi"), "OK")

    def test_calls_client_with_model(self):
        ai = self._openai_compatible_ai("OK")
        _probe_llm(ai, "hi")
        _, kwargs = ai.client.chat.completions.create.call_args
        self.assertEqual(kwargs["model"], "nvidia/nemotron-3-super-120b-a12b:free")

    def test_none_client_raises_helpful_error(self):
        ai = MagicMock()
        ai.provider = "openrouter"
        ai.client = None
        with self.assertRaises(RuntimeError) as ctx:
            _probe_llm(ai, "hi")
        self.assertIn("pip install openai", str(ctx.exception))

    def test_api_error_propagates(self):
        # A rerank model / bad id would raise inside the client call — must NOT be
        # swallowed into "".
        ai = MagicMock()
        ai.provider = "openrouter"
        ai.model = "nvidia/llama-nemotron-rerank-vl-1b-v2:free"
        ai.client.chat.completions.create.side_effect = ValueError("404 model not found")
        with self.assertRaises(ValueError):
            _probe_llm(ai, "hi")

    def test_claude_cli_uses_generate(self):
        ai = MagicMock()
        ai.provider = "claude_cli"
        ai.generate.return_value = "hello from cli"
        self.assertEqual(_probe_llm(ai, "hi"), "hello from cli")
        ai.generate.assert_called_once()


if __name__ == "__main__":
    unittest.main()
