"""
LLM Agent (provider-agnostic)
===============================
Three jobs, all via plain-text completions to whichever provider is
configured (Gemini, Claude, Grok, or Groq -- set via LLM_PROVIDER):

  1. classify_message()      -- classify a report + write an immediate
                                 acknowledgment reply.
  2. explain_site_status()    -- given a real sanity-check result, write a
                                 reply confirming down/degraded, OR (if the
                                 site is actually fine) reassure the
                                 reporter and suggest local troubleshooting
                                 steps.
  3. explain_product_status() -- given a real product-search result, write
                                 a reply confirming missing, OR (if it was
                                 actually found) let the reporter know.
  4. summarize_thread()       -- summarize a Slack thread transcript when
                                 the bot is @-mentioned and asked to.
"""
from __future__ import annotations

import logging
import re

from config.settings import LLM_PROVIDER

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Step 1: classify + immediate acknowledgment
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Step 2/3: explain the real check result
# ---------------------------------------------------------------------------

_SITE_STATUS_PROMPT = """\
You are an SRE assistant bot. A teammate reported:

"{original_message}"

You just ran a real sanity check against {url}. Result:
Status: {status}
Checks: {checks_summary}

Write a short Slack reply (use mrkdwn like *bold*, keep it to 2-4 sentences):
- If status is RED or YELLOW: confirm the site really is down/degraded, and
  mention the status plainly.
- If status is GREEN: let the reporter know the site looks healthy from your
  side, and suggest 2-4 quick things they can try locally, since it's likely
  on their end (e.g. clear browser cache, try incognito/private mode, check
  their VPN, flush local DNS, try a different network).
"""

_PRODUCT_STATUS_PROMPT = """\
You are an SRE assistant bot. A teammate reported that this product/item
isn't showing up: "{product_query}"

Original message: "{original_message}"

You just ran a real product search. Result:
Found: {found}
Result count: {result_count}
Matching products (if any): {products_summary}

Write a short Slack reply (use mrkdwn like *bold*, keep it to 2-3 sentences):
- If not found (result count is 0): confirm it genuinely isn't appearing in
  search results.
- If found: let them know it does show up, and mention what was found
  (title/brand/price if available) -- so they know it's likely a caching or
  browser issue on their end, not a real listing problem.
"""

# ---------------------------------------------------------------------------
# Step 4: thread summarization
# ---------------------------------------------------------------------------

_SUMMARIZE_PROMPT = """\
You are an SRE assistant bot. Summarize the following Slack thread for
someone catching up. Use Slack mrkdwn. Keep it concise -- a short paragraph
or a few bullet points covering what was reported, what was found/done, and
the current status/outcome if one is clear from the thread.

Thread (oldest to newest, "user: message" per line):
{transcript}
"""


class LLMAgent:
    """Classifies reports, explains real check results, and summarizes threads."""

    def __init__(self) -> None:
        self._provider = (LLM_PROVIDER or "gemini").strip().lower()
        if self._provider not in {"gemini", "claude", "grok", "groq"}:
            raise ValueError(
                f"Unsupported LLM_PROVIDER: {LLM_PROVIDER!r}. "
                "Use 'gemini', 'claude', 'grok', or 'groq'."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def classify_message(self, message: str) -> dict:
        """
        Classify *message* and get an immediate acknowledgment reply.

        Returns:
            {
                "category": "site_down" | "product_missing" | "none",
                "product_query": str | None,
                "reply": str,
            }
        """
        prompt = _CLASSIFY_PROMPT.format(message=message)
        try:
            answer = self._complete(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.error("%s classification failed: %s", self._provider, exc)
            return {
                "category": "none",
                "product_query": None,
                "reply": "Sorry, I hit an error trying to process that -- please try again.",
            }

        logger.info("LLM classification for %r -> %r", message[:80], answer.strip())
        return self._parse_classify_response(answer)

    def explain_site_status(
        self, original_message: str, url: str, status: str, checks_summary: str
    ) -> str:
        """Write a reply explaining a real sanity-check result."""
        prompt = _SITE_STATUS_PROMPT.format(
            original_message=original_message,
            url=url,
            status=status,
            checks_summary=checks_summary,
        )
        try:
            return self._complete(prompt).strip()
        except Exception as exc:  # noqa: BLE001
            logger.error("%s site-status explanation failed: %s", self._provider, exc)
            if status in ("YELLOW", "RED"):
                return f":rotating_light: Confirmed — *{url}* is down/degraded (status: {status})."
            return f":white_check_mark: *{url}* looks healthy from our side."

    def explain_product_status(
        self,
        original_message: str,
        product_query: str,
        found: bool,
        result_count: int,
        products_summary: str,
    ) -> str:
        """Write a reply explaining a real product-search result."""
        prompt = _PRODUCT_STATUS_PROMPT.format(
            original_message=original_message,
            product_query=product_query,
            found=found,
            result_count=result_count,
            products_summary=products_summary or "none",
        )
        try:
            return self._complete(prompt).strip()
        except Exception as exc:  # noqa: BLE001
            logger.error("%s product-status explanation failed: %s", self._provider, exc)
            if found:
                return f":white_check_mark: Actually found *{product_query}* ({result_count} result(s))."
            return f":package: Confirmed — *{product_query}* isn't showing up in search (0 results)."

    def summarize_thread(self, transcript: str) -> str:
        """Summarize a Slack thread transcript."""
        prompt = _SUMMARIZE_PROMPT.format(transcript=transcript)
        try:
            return self._complete(prompt).strip()
        except Exception as exc:  # noqa: BLE001
            logger.error("%s thread summary failed: %s", self._provider, exc)
            return "Sorry, I ran into an error trying to summarize this thread."

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_classify_response(answer: str) -> dict:
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
    # Provider dispatch -- each is a single plain-text completion, no tools.
    # ------------------------------------------------------------------

    def _complete(self, prompt: str) -> str:
        if self._provider == "gemini":
            return self._ask_gemini(prompt)
        elif self._provider == "claude":
            return self._ask_claude(prompt)
        elif self._provider == "grok":
            return self._ask_grok(prompt)
        else:
            return self._ask_groq(prompt)

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
            max_tokens=300,
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