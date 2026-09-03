"""
Central configuration.
Loads everything from environment variables (via a .env file in dev).
"""
from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value or ""


def _get_list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw else default


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------

SLACK_BOT_TOKEN = _get("SLACK_BOT_TOKEN", required=True)
SLACK_SIGNING_SECRET = _get("SLACK_SIGNING_SECRET", required=True)
SLACK_APP_TOKEN = _get("SLACK_APP_TOKEN", required=True)  # xapp-... for Socket Mode

MONITORED_CHANNEL = _get("MONITORED_CHANNEL", default="all-devopssre")

# ---------------------------------------------------------------------------
# Sites to check
# ---------------------------------------------------------------------------

MONITORED_URLS = _get_list("MONITORED_URLS", default="https://example.com")

# ---------------------------------------------------------------------------
# Sanity check tuning
# ---------------------------------------------------------------------------

SANITY_CHECK_TIMEOUT = _get_float("SANITY_CHECK_TIMEOUT", default=10.0)
LATENCY_THRESHOLD = _get_float("LATENCY_THRESHOLD", default=3.0)

# ---------------------------------------------------------------------------
# PagerDuty
# ---------------------------------------------------------------------------

# Events API v2 routing key -- from the PagerDuty service's Integrations tab
# (Add integration -> Events API v2). This is what actually triggers a page.
PAGERDUTY_ROUTING_KEY = _get("PAGERDUTY_ROUTING_KEY")

# Optional: a separate REST API token (Settings -> API Access in PagerDuty).
# Only needed to fetch a clickable incident URL to post in Slack -- paging
# itself works fine without this.
PAGERDUTY_API_KEY = _get("PAGERDUTY_API_KEY")

# Just a label used in the incident title/component -- not an auth credential.
PAGERDUTY_SERVICE_NAME = _get("PAGERDUTY_SERVICE_NAME", default="Web-Frontend-Team")

# Slack user group to @-mention when paging (the group's ID, e.g. "S0123ABC" --
# find it via the group's "Copy link" in Slack, or the usergroups.list API;
# NOT the same as the group's @handle).
SLACK_WEB_ONCALL_GROUP_ID = _get("SLACK_WEB_ONCALL_GROUP_ID")

# ---------------------------------------------------------------------------
# LLM provider — pick one: "gemini" | "claude" | "grok"
# ---------------------------------------------------------------------------

LLM_PROVIDER = _get("LLM_PROVIDER", default="gemini")

# Gemini
GEMINI_API_KEY = _get("GEMINI_API_KEY")
GEMINI_MODEL = _get("GEMINI_MODEL", default="gemini-3.5-flash-lite")

# Claude
ANTHROPIC_API_KEY = _get("ANTHROPIC_API_KEY")
CLAUDE_MODEL = _get("CLAUDE_MODEL", default="claude-sonnet-5")

# Grok (xAI — OpenAI-compatible API)
XAI_API_KEY = _get("XAI_API_KEY")
GROK_MODEL = _get("GROK_MODEL", default="grok-4.6")

# Groq (groq.com — fast inference host for open models like Qwen/Llama.
# NOT the same as Grok/xAI above — different company, different API key.)
GROQ_API_KEY = _get("GROQ_API_KEY")
GROQ_MODEL = _get("GROQ_MODEL", default="qwen/qwen3.8-27b")