"""
Slack Bolt Event Handler
==========================
Listens for messages in the monitored Slack channel. For each message:
  1. Ask the LLM to classify it (SITE_DOWN, PRODUCT_MISSING, or NONE) and
     write a reply -- always posted, regardless of category.
  2. SITE_DOWN        -> run a sanity check against the monitored URL(s);
                          post a follow-up if the result is YELLOW/RED.
  3. PRODUCT_MISSING   -> search for the product; post a follow-up if it
                          genuinely isn't found.
  4. NONE              -> nothing further (the LLM's reply already covered it).
"""
from __future__ import annotations

import logging

from slack_bolt import App

from config.settings import (
    MONITORED_CHANNEL,
    MONITORED_URLS,
    SLACK_BOT_TOKEN,
    SLACK_SIGNING_SECRET,
    SANITY_CHECK_TIMEOUT,
    LATENCY_THRESHOLD,
)
from bot.llm_agent import LLMAgent
from tools.sanity_checker import run_sanity_check
from tools.product_search import search_product

logger = logging.getLogger(__name__)

app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)

_llm_agent = LLMAgent()


def _channel_name(channel_id: str) -> str:
    """Resolve a channel ID to its name, with a safe fallback."""
    try:
        info = app.client.conversations_info(channel=channel_id)
        return info["channel"]["name"]
    except Exception:  # noqa: BLE001
        return channel_id


def _reply(channel_id: str, thread_ts: str, text: str) -> None:
    try:
        app.client.chat_postMessage(
            channel=channel_id,
            thread_ts=thread_ts,
            text=text,
            mrkdwn=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to post reply: %s", exc)


def _handle_site_down(channel_id: str, ts: str) -> None:
    """Run a sanity check against the monitored site(s); confirm if degraded/down."""
    for url in MONITORED_URLS:
        report = run_sanity_check(
            url, timeout=SANITY_CHECK_TIMEOUT, latency_threshold=LATENCY_THRESHOLD
        )
        status = report.to_dict().get("status", "GREEN")

        if status in ("YELLOW", "RED"):
            _reply(
                channel_id,
                ts,
                f":rotating_light: Confirmed — *{url}* is really down (status: {status}).",
            )
            break  # one confirmed hit is enough for this demo


def _handle_product_missing(channel_id: str, ts: str, product_query: str | None) -> None:
    """Search for the reported product; confirm in-thread if it's genuinely missing."""
    if not product_query:
        logger.info("PRODUCT_MISSING classified but no product query extracted; skipping follow-up.")
        return

    result = search_product(product_query)

    if not result.found:
        _reply(
            channel_id,
            ts,
            f":package: Confirmed — *{product_query}* isn't showing up in search "
            f"(0 results).",
        )
    else:
        logger.info(
            "Product %r was actually found (%d result(s)) -- likely a false alarm, no follow-up needed.",
            product_query, result.result_count,
        )


@app.event("message")
def handle_message(event: dict, logger: logging.Logger) -> None:  # type: ignore[override]
    """
    Guard conditions (silently ignored):
    - Messages from bots / system subtypes
    - Thread replies (only top-level messages are processed)
    - Messages outside the monitored channel
    """
    if event.get("bot_id") or event.get("subtype"):
        return
    if event.get("thread_ts") and event["thread_ts"] != event.get("ts"):
        return

    channel_id: str = event.get("channel", "")
    text: str = event.get("text", "").strip()
    ts: str = event.get("ts", "")

    if not (text and channel_id and ts):
        return

    if _channel_name(channel_id) != MONITORED_CHANNEL:
        return

    # 1. Ask the LLM to classify the message and write a reply
    result = _llm_agent.classify_message(text)
    category = result["category"]

    logger.info("LLM classified message as %s: %s", category.upper(), text[:100])

    # 2. Always post the LLM's reply -- acknowledgment if a tool applies,
    #    an honest "can't help with that" otherwise.
    _reply(channel_id, ts, result["reply"])

    # 3. Run the matching check and follow up if it confirms a real problem
    if category == "site_down":
        _handle_site_down(channel_id, ts)
    elif category == "product_missing":
        _handle_product_missing(channel_id, ts, result.get("product_query"))