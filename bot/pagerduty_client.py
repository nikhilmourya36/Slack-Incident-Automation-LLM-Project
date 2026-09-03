"""
PagerDuty Client
==================
Triggers incidents via PagerDuty's Events API v2 (routing key -- no REST
API token needed for this part), and optionally looks up the resulting
incident's web URL via PagerDuty's REST API (needs a separate API token,
since Events API v2 doesn't return a clickable link on its own).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

PD_EVENTS_URL = "https://events.pagerduty.com/v2/enqueue"
PD_REST_INCIDENTS_URL = "https://api.pagerduty.com/incidents"
REQUEST_TIMEOUT = 10  # seconds


def _build_dedup_key(title: str) -> str:
    """Stable dedup key so repeated triggers on the same day merge into one incident."""
    safe_title = title[:50].lower().replace(" ", "-")
    date_str = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"incident-bot-{safe_title}-{date_str}"


def trigger_incident(
    routing_key: str,
    title: str,
    description: str,
    severity: str = "critical",
    url: str | None = None,
    component: str = "Website",
    source: str = "SlackIncidentBot",
    dedup_key: str | None = None,
) -> dict:
    """
    Trigger a PagerDuty incident via Events API v2.

    Returns:
        dict with keys: success (bool), dedup_key (str), message (str)
    """
    dedup_key = dedup_key or _build_dedup_key(title)

    payload: dict = {
        "routing_key": routing_key,
        "event_action": "trigger",
        "dedup_key": dedup_key,
        "payload": {
            "summary": title,
            "severity": severity,
            "source": source,
            "component": component,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "custom_details": {
                "description": description,
                "affected_url": url or "N/A",
                "reported_via": "Slack",
                "automation": "IncidentBot",
            },
        },
    }
    if url:
        payload["links"] = [{"href": url, "text": "Affected Service"}]

    try:
        resp = requests.post(
            PD_EVENTS_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("PagerDuty incident triggered. dedup_key=%s", dedup_key)
        return {
            "success": True,
            "dedup_key": data.get("dedup_key", dedup_key),
            "message": data.get("message", "Event created"),
        }
    except requests.exceptions.HTTPError as exc:
        logger.error("PagerDuty HTTP error: %s — %s", exc, exc.response.text if exc.response else "")
        return {"success": False, "dedup_key": dedup_key, "message": str(exc)}
    except requests.exceptions.RequestException as exc:
        logger.error("PagerDuty request failed: %s", exc)
        return {"success": False, "dedup_key": dedup_key, "message": str(exc)}


def resolve_incident(routing_key: str, dedup_key: str) -> dict:
    """Resolve an existing PagerDuty incident by dedup key."""
    payload = {
        "routing_key": routing_key,
        "event_action": "resolve",
        "dedup_key": dedup_key,
    }
    try:
        resp = requests.post(
            PD_EVENTS_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        logger.info("PagerDuty incident resolved. dedup_key=%s", dedup_key)
        return {"success": True, "message": "Incident resolved"}
    except requests.exceptions.RequestException as exc:
        logger.error("Failed to resolve PagerDuty incident: %s", exc)
        return {"success": False, "message": str(exc)}


def get_incident_url(
    api_key: str,
    dedup_key: str,
    retries: int = 3,
    retry_delay: float = 2.0,
    timeout: float = REQUEST_TIMEOUT,
) -> str | None:
    """
    Look up the web URL of the incident created from *dedup_key*, via
    PagerDuty's REST API. Requires a REST API token (Settings -> API
    Access in PagerDuty) -- NOT the same credential as the Events API v2
    routing key used by trigger_incident().

    PagerDuty can take a moment to make a freshly-triggered incident
    queryable via REST, so this retries briefly before giving up.

    Returns the incident's html_url, or None if not found / no api_key set.
    """
    if not api_key:
        return None

    headers = {
        "Authorization": f"Token token={api_key}",
        "Accept": "application/vnd.pagerduty+json;version=2",
    }

    for attempt in range(retries):
        try:
            resp = requests.get(
                PD_REST_INCIDENTS_URL,
                params={"incident_key": dedup_key},
                headers=headers,
                timeout=timeout,
            )
            resp.raise_for_status()
            incidents = resp.json().get("incidents", [])
            if incidents:
                return incidents[0].get("html_url")
        except requests.exceptions.RequestException as exc:
            logger.warning(
                "PagerDuty incident lookup attempt %d/%d failed: %s",
                attempt + 1, retries, exc,
            )

        if attempt < retries - 1:
            time.sleep(retry_delay)

    logger.warning("Could not resolve a PagerDuty incident URL for dedup_key=%s", dedup_key)
    return None