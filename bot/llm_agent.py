"""
LLM Agent (provider-agnostic, structured classification + reply)
====================================================================
Sends a Slack message to the configured LLM provider (Gemini, Claude,
Grok, or Groq) and gets back THREE things in one call:
  - category: SITE_DOWN | PRODUCT_MISSING | NONE
  - product_query: extracted product name/item number (PRODUCT_MISSING only)
  - reply: a natural-language Slack reply the LLM writes itself --
           "on it, checking now" style if a tool applies, or an honest
           "I don't have a tool for that yet" if not.

Switch providers via LLM_PROVIDER in config/settings.py.
"""
from __future__ import annotations

import logging
import re

from config.settings import LLM_PROVIDER

logger = logging.getLogger(__name__)

_CLASSIFY_PROMPT = """\
You are an SRE assistant bot in a DevOps Slack channel. A teammate posted:

"{message}"

Step 1 -- Classify this message into exactly ONE category:
- SITE_DOWN: the message says a website or service is down, broken, or having an outage.
- PRODUCT_MISSING: the message says a specific product or item isn't showing up,
  isn't visible, or can't be found on the site (but the site itself seems fine).
- NONE: neither of the above -- a generic message, question, or chat you have no tool for.

Step 2 -- If PRODUCT_MISSING, extract the product name or item/SKU number as written.

Step 3 -- Write a short, natural Slack reply (1-2 sentences, friendly, use Slack
mrkdwn like *bold* sparingly):
- If SITE_DOWN or PRODUCT_MISSING: acknowledge the report and say you're checking
  it now (you DO have a tool for this).
- If NONE: politely say you don't have a tool to help with that kind of request
  right now. Be honest and brief, in your own words.

Respond in EXACTLY this format, nothing else before or after it:
CATEGORY: <SITE_DOWN|PRODUCT_MISSING|NONE>
PRODUCT: <extracted product name or item number, or NONE if not applicable>
REPLY: <your Slack reply text, one or two sentences>
"""

_CATEGORY_RE = re.compile(r"CATEGORY:\s*(SITE_DOWN|PRODUCT_MISSING|NONE)", re.IGNORECASE)
_PRODUCT_RE = re.compile(r"PRODUCT:\s*(.+?)(?:\n|$)", re.IGNORECASE)
_REPLY_RE = re.compile(r"REPLY:\s*(.+)", re.IGNORECASE | re.DOTALL)


class LLMAgent:
    """Classifies Slack messages and writes a reply, in one call."""

    def __init__(self) -> None:
        self._provider = (LLM_PROVIDER or "gemini").strip().lower()
        if self._provider not in {"gemini", "claude", "grok", "groq"}:
            raise ValueError(
                f"Unsupported LLM_PROVIDER: {LLM_PROVIDER!r}. "
                "Use 'gemini', 'claude', 'grok', or 'groq'."
            )

    def classify_message(self, message: str) -> dict:
        """
        Classify *message* and get a reply for it.

        Returns:
            {
                "category": "site_down" | "product_missing" | "none",
                "product_query": str | None,  # only set for product_missing
                "reply": str,                  # always set -- what to post in Slack
            }
        """
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
            return {
                "category": "none",
                "product_query": None,
                "reply": "Sorry, I hit an error trying to process that -- please try again.",
            }

        logger.info("LLM classification for %r -> %r", message[:80], answer.strip())
        return self._parse_response(answer)

    @staticmethod
    def _parse_response(answer: str) -> dict:
        category_match = _CATEGORY_RE.search(answer)
        product_match = _PRODUCT_RE.search(answer)
        reply_match = _REPLY_RE.search(answer)

        category = category_match.group(1).upper() if category_match else "NONE"

        product_query = product_match.group(1).strip() if product_match else None
        if product_query and product_query.upper() == "NONE":
            product_query = None

        reply = reply_match.group(1).strip() if reply_match else (
            "Got your message, but I'm not able to help with that right now."
        )

        return {
            "category": category.lower(),
            "product_query": product_query if category == "PRODUCT_MISSING" else None,
            "reply": reply,
        }

    # ------------------------------------------------------------------
    # Provider calls -- each is a single plain-text completion, no tools.
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
            max_tokens=150,
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
        """Requires: pip install openai (Groq is OpenAI-compatible)"""
        from openai import OpenAI
        from config.settings import GROQ_API_KEY, GROQ_MODEL

        client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content or ""