"""
Slack Bolt Event Handler (simple demo version)
================================================
Listens for messages in the monitored Slack channel. For each message:
  1. Ask the LLM: is this reporting that the site is down?
  2. If yes, run a sanity check against the monitored URL(s).
  3. If the sanity check comes back YELLOW or RED, post a message in the
     same thread confirming the site is really down.
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

    # 1. Ask the LLM whether this message is reporting an outage
    if not _llm_agent.is_site_down_report(text):
        return

    logger.info("LLM flagged possible outage report in #%s: %s", channel_id, text[:100])

    # 2. Run the sanity check against the monitored site(s)
    for url in MONITORED_URLS:
        report = run_sanity_check(
            url, timeout=SANITY_CHECK_TIMEOUT, latency_threshold=LATENCY_THRESHOLD
        )
        status = report.to_dict().get("status", "GREEN")

        # 3. If degraded or down, confirm it in the thread
        if status in ("YELLOW", "RED"):
            try:
                app.client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=ts,
                    text=f":rotating_light: Confirmed — *{url}* is really down (status: {status}).",
                    mrkdwn=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to post outage confirmation: %s", exc)
            break  # one confirmed hit is enough for this demo
