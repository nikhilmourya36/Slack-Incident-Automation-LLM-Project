"""
LLM Agent (provider-agnostic, simple classification)
======================================================
Sends a Slack message to the configured LLM provider (Gemini, Claude, or
Grok) and asks one yes/no question: is this message reporting that a
site/service is down? Switch providers via LLM_PROVIDER in config/settings.py.
"""
from __future__ import annotations

import logging

from config.settings import LLM_PROVIDER

logger = logging.getLogger(__name__)

_CLASSIFY_PROMPT = """\
You are an SRE assistant. A teammate posted this message in a DevOps Slack channel:

"{message}"

Is this message reporting that a website or service is down, broken, or having
an outage? Answer with exactly one word: YES or NO. Do not explain.
"""


class LLMAgent:
    """Classifies whether a Slack message reports an outage."""

    def __init__(self) -> None:
        self._provider = (LLM_PROVIDER or "gemini").strip().lower()
        if self._provider not in {"gemini", "claude", "grok", "groq"}:
            raise ValueError(
                f"Unsupported LLM_PROVIDER: {LLM_PROVIDER!r}. "
                "Use 'gemini', 'claude', 'grok', or 'groq'."
            )

    def is_site_down_report(self, message: str) -> bool:
        """Ask the LLM whether *message* is reporting an outage."""
        prompt = _CLASSIFY_PROMPT.format(message=message)
        try:
            if self._provider == "gemini":
                answer = self._ask_gemini(prompt)
            elif self._provider == "claude":
                answer = self._ask_claude(prompt)
            elif self._provider == "grok":
                answer = self._ask_grok(prompt)
            else:
                answer = self._ask_groq(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.error("%s classification failed: %s", self._provider, exc)
            return False

        logger.info("LLM classification for %r -> %r", message[:80], answer.strip())
        return answer.strip().upper().startswith("YES")

    # ------------------------------------------------------------------
    # Provider calls — each is a single plain-text completion, no tools.
    # ------------------------------------------------------------------

    def _ask_gemini(self, prompt: str) -> str:
        """Requires: pip install google-genai"""
        from google import genai
        from config.settings import GEMINI_API_KEY, GEMINI_MODEL

        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        return response.text or ""

    def _ask_claude(self, prompt: str) -> str:
        """Requires: pip install anthropic"""
        import anthropic
        from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=10,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(block.text for block in response.content if block.type == "text")

    def _ask_grok(self, prompt: str) -> str:
        """Requires: pip install openai (xAI is OpenAI-compatible)"""
        from openai import OpenAI
        from config.settings import XAI_API_KEY, GROK_MODEL

        client = OpenAI(api_key=XAI_API_KEY, base_url="https://api.x.ai/v1")
        response = client.chat.completions.create(
            model=GROK_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""

    def _ask_groq(self, prompt: str) -> str:
        """Requires: pip install openai (Groq is OpenAI-compatible).
        Groq (groq.com) is a fast-inference host for open models like
        Qwen/Llama — not the same company as Grok/xAI above."""
        from openai import OpenAI
        from config.settings import GROQ_API_KEY, GROQ_MODEL

        client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""