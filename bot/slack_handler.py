"""
Slack Bolt Event Handler
==========================
Two entry points:

1. @app.event("message") -- for reports posted in the monitored channel:
   a. LLM classifies (SITE_DOWN / PRODUCT_MISSING / NONE) + writes an
      immediate acknowledgment reply -- always posted.
   b. For SITE_DOWN / PRODUCT_MISSING, the matching check actually runs,
      and the LLM writes a follow-up reply explaining the REAL result --
      confirming a real problem, or reassuring the reporter (with
      troubleshooting suggestions) if everything's actually fine.

2. @app.event("app_mention") -- when the bot is @-mentioned, e.g. asked to
   summarize a thread: fetches the thread's messages and has the LLM
   summarize them.

Note: Slack fires BOTH a "message" event and an "app_mention" event for a
message that @-mentions the bot. To avoid double replies, the "message"
handler skips any message that mentions the bot (app_mention handles it).
"""
from __future__ import annotations

import logging
import re

from slack_bolt import App

from config.settings import (
    MONITORED_CHANNEL,
    MONITORED_URLS,
    SLACK_BOT_TOKEN,
    SLACK_SIGNING_SECRET,
    SANITY_CHECK_TIMEOUT,
    LATENCY_THRESHOLD,
    PAGERDUTY_API_KEY,
    PAGERDUTY_SERVICE_ID,
    PAGERDUTY_ESCALATION_POLICY_ID,
    PAGERDUTY_FROM_EMAIL,
    PAGERDUTY_SERVICE_NAME,
    SLACK_WEB_ONCALL_GROUP_ID,
)
from bot.llm_agent import LLMAgent
from bot.pagerduty_client import trigger_incident
from tools.sanity_checker import run_sanity_check
from tools.product_search import search_product

logger = logging.getLogger(__name__)

app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)

_llm_agent = LLMAgent()

_bot_user_id: str | None = None
_user_name_cache: dict[str, str] = {}


def _get_bot_user_id() -> str:
    global _bot_user_id  # noqa: PLW0603
    if _bot_user_id is None:
        try:
            auth = app.client.auth_test()
            _bot_user_id = auth.get("user_id", "")
        except Exception as exc:  # noqa: BLE001
            logger.error("auth_test failed: %s", exc)
            _bot_user_id = ""
    return _bot_user_id


def _channel_name(channel_id: str) -> str:
    """Resolve a channel ID to its name, with a safe fallback."""
    try:
        info = app.client.conversations_info(channel=channel_id)
        return info["channel"]["name"]
    except Exception:  # noqa: BLE001
        return channel_id


def _user_display_name(user_id: str) -> str:
    """Resolve a user ID to a display name, with a safe fallback. Cached."""
    if user_id in _user_name_cache:
        return _user_name_cache[user_id]
    try:
        info = app.client.users_info(user=user_id)
        user = info["user"]
        name = user.get("display_name") or user.get("real_name") or user_id
    except Exception:  # noqa: BLE001
        name = user_id
    _user_name_cache[user_id] = name
    return name


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


# ---------------------------------------------------------------------------
# Report handling (SITE_DOWN / PRODUCT_MISSING)
# ---------------------------------------------------------------------------


def _handle_site_down(channel_id: str, ts: str, original_message: str) -> None:
    """Run a real sanity check and always reply with the LLM's explanation of it.
    If genuinely down/degraded, also page the on-call via PagerDuty and
    @-mention the on-call Slack group in the thread."""
    for url in MONITORED_URLS:
        report = run_sanity_check(
            url, timeout=SANITY_CHECK_TIMEOUT, latency_threshold=LATENCY_THRESHOLD
        ).to_dict()
        status = report.get("status", "GREEN")
        checks_summary = "; ".join(
            f"{c['name']}: {c['message']}" for c in report.get("checks", [])
        )

        reply = _llm_agent.explain_site_status(
            original_message=original_message,
            url=url,
            status=status,
            checks_summary=checks_summary,
        )
        _reply(channel_id, ts, reply)

        if status in ("YELLOW", "RED"):
            _page_on_call(channel_id, ts, url, status, original_message)

        break  # one checked URL is enough for this demo


def _page_on_call(channel_id: str, ts: str, url: str, status: str, original_message: str) -> None:
    """Create a PagerDuty incident via REST API, @-mention the on-call group,
    and post the incident link -- returned directly, no separate lookup needed."""
    if not (PAGERDUTY_API_KEY and PAGERDUTY_SERVICE_ID):
        logger.warning(
            "Site is down but PAGERDUTY_API_KEY/PAGERDUTY_SERVICE_ID isn't set -- skipping page."
        )
        _reply(
            channel_id, ts,
            ":warning: This looks like a real outage, but PagerDuty isn't configured "
            "yet -- please page on-call manually.",
        )
        return

    title = f"{PAGERDUTY_SERVICE_NAME}: Website Down — {url}"
    pd_result = trigger_incident(
        api_key=PAGERDUTY_API_KEY,
        service_id=PAGERDUTY_SERVICE_ID,
        title=title,
        description=f"Reported in Slack: {original_message}\nStatus: {status}\nURL: {url}",
        urgency="high",
        escalation_policy_id=PAGERDUTY_ESCALATION_POLICY_ID or None,
        from_email=PAGERDUTY_FROM_EMAIL or None,
    )

    oncall_mention = (
        f"<!subteam^{SLACK_WEB_ONCALL_GROUP_ID}>" if SLACK_WEB_ONCALL_GROUP_ID else "on-call"
    )

    if not pd_result.get("success"):
        if pd_result.get("likely_duplicate"):
            _reply(
                channel_id, ts,
                f":information_source: {oncall_mention} — a similar issue already appears to "
                f"be open in PagerDuty (couldn't confirm the exact incident automatically). "
                f"Please check PagerDuty directly rather than paging again.",
            )
        else:
            _reply(
                channel_id, ts,
                f":warning: {oncall_mention} — tried to page automatically but it failed "
                f"({pd_result.get('message', 'unknown error')}). Please page manually.",
            )
        return

    incident_url = pd_result.get("incident_url")
    incident_number = pd_result.get("incident_number")

    if incident_url:
        link_line = f"PagerDuty incident #{incident_number}: {incident_url}"
    else:
        link_line = f"Incident key: `{pd_result.get('incident_key')}` (no URL returned)"

    _reply(
        channel_id, ts,
        f":pager: {oncall_mention} — paged for *{PAGERDUTY_SERVICE_NAME}*, please take a look.\n"
        f"{link_line}",
    )


def _handle_product_missing(channel_id: str, ts: str, original_message: str, product_query: str | None) -> None:
    """Run a real product search and always reply with the LLM's explanation of it."""
    if not product_query:
        logger.info("PRODUCT_MISSING classified but no product query extracted; skipping.")
        return

    result = search_product(product_query)
    products_summary = "; ".join(
        f"{p.get('title')} ({p.get('brand')}) - ${p.get('price')}"
        for p in result.products[:3]
    )

    reply = _llm_agent.explain_product_status(
        original_message=original_message,
        product_query=product_query,
        found=result.found,
        result_count=result.result_count,
        products_summary=products_summary,
    )
    _reply(channel_id, ts, reply)


# ---------------------------------------------------------------------------
# Message event -- reports in the monitored channel
# ---------------------------------------------------------------------------


@app.event("message")
def handle_message(event: dict, logger: logging.Logger) -> None:  # type: ignore[override]
    """
    Guard conditions (silently ignored):
    - Messages from bots / system subtypes
    - Thread replies (only top-level messages are processed)
    - Messages outside the monitored channel
    - Messages that @-mention the bot (handled by app_mention instead)
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

    bot_id = _get_bot_user_id()
    if bot_id and re.search(rf"<@{re.escape(bot_id)}(\||>)", text):
        return  # let app_mention handle it

    # 1. Classify + immediate acknowledgment
    result = _llm_agent.classify_message(text)
    category = result["category"]
    logger.info("LLM classified message as %s: %s", category.upper(), text[:100])
    _reply(channel_id, ts, result["reply"])

    # 2. Run the real check and reply with the actual outcome
    if category == "site_down":
        _handle_site_down(channel_id, ts, text)
    elif category == "product_missing":
        _handle_product_missing(channel_id, ts, text, result.get("product_query"))


# ---------------------------------------------------------------------------
# App mention event -- e.g. "@bot can you summarize this thread"
# ---------------------------------------------------------------------------


@app.event("app_mention")
def handle_mention(event: dict, logger: logging.Logger) -> None:  # type: ignore[override]
    logger.info("DEBUG app_mention event received: %r", event.get("text", ""))

    channel_id: str = event.get("channel", "")
    ts: str = event.get("ts", "")
    thread_ts: str = event.get("thread_ts") or ts
    raw_text: str = event.get("text", "")

    # Strip the "<@BOTID>" mention itself out of the text
    clean_text = re.sub(r"<@[^>]+>\s*", "", raw_text).strip()

    if "summar" not in clean_text.lower():
        _reply(
            channel_id,
            thread_ts,
            "I can summarize a thread if you ask me to — try "
            "`@devops-bot can you summarize this thread`.",
        )
        return

    if thread_ts == ts:
        # Mentioned outside of an existing thread -- nothing to summarize.
        _reply(
            channel_id,
            thread_ts,
            "This doesn't look like it's part of a thread yet — mention me "
            "inside a thread reply and I'll summarize it.",
        )
        return

    try:
        result = app.client.conversations_replies(channel=channel_id, ts=thread_ts, limit=200)
        messages = result.get("messages", [])
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to fetch thread replies: %s", exc)
        _reply(channel_id, thread_ts, "Sorry, I couldn't fetch this thread to summarize it.")
        return

    lines = []
    for m in messages:
        if m.get("bot_id"):
            continue  # skip the bot's own messages in the transcript
        user_id = m.get("user", "unknown")
        text = m.get("text", "")
        if text:
            lines.append(f"{_user_display_name(user_id)}: {text}")

    if not lines:
        _reply(channel_id, thread_ts, "This thread doesn't have anything for me to summarize yet.")
        return

    transcript = "\n".join(lines)
    logger.info("Summarizing thread %s (%d messages)", thread_ts, len(lines))

    summary = _llm_agent.summarize_thread(transcript)
    _reply(channel_id, thread_ts, summary)