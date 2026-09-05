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


def _build_dedup_key(title: str, window_minutes: int = 24 * 60) -> str:
    """
    Stable dedup key so repeated triggers within the same window merge into
    one incident, instead of paging on-call again for a report of the same
    ongoing outage. This is intentional -- randomizing the key would defeat
    the purpose and page on-call once per report instead of once per outage.

    window_minutes controls how fresh a report needs to be to reuse an
    existing incident vs. start a new one -- default 24h (one incident per
    title per day). Pass a smaller value (e.g. 60 for hourly) if a
    recurring outage later the same day should page again rather than
    silently reuse an old incident.
    """
    safe_title = title[:50].lower().replace(" ", "-")
    now = datetime.now(timezone.utc)
    # Bucket the current time into windows since the epoch, so any two
    # calls within the same window produce the identical key.
    bucket = int(now.timestamp() // (window_minutes * 60))
    return f"incident-bot-{safe_title}-{bucket}"


def trigger_incident(
    api_key: str,
    service_id: str,
    title: str,
    description: str,
    urgency: str = "high",
    escalation_policy_id: str | None = None,
    from_email: str | None = None,
    incident_key: str | None = None,
    dedup_window_minutes: int = 24 * 60,
    timeout: float = REQUEST_TIMEOUT,
) -> dict:
    """
    Create a PagerDuty incident directly via the REST API (POST /incidents).

    Unlike Events API v2, this creates the incident synchronously and
    returns its real URL in the same response -- no separate lookup needed,
    and no risk of the event getting silently dropped by orchestration
    rules further down an async pipeline.

    Args:
        api_key: PagerDuty REST API token (Settings -> API Access).
        service_id: The target service's ID, e.g. "PWIXJZS".
        title: Incident title/summary.
        description: Longer description, goes in the incident body.
        urgency: "high" or "low".
        escalation_policy_id: Optional -- if omitted, the service's default
            escalation policy is used.
        from_email: Required if api_key is an account-level API token (not
            a user-level one) -- must be the email of a real user on the
            account. PagerDuty uses this to attribute the incident.
        incident_key: Optional custom dedup key; auto-generated if omitted.
        dedup_window_minutes: How long repeated reports of the same title
            reuse one incident before a fresh one is created. Default 24h.
            Ignored if incident_key is explicitly provided.

    Returns:
        dict with keys: success (bool), incident_key (str),
        incident_url (str | None), incident_number (int | None), message (str)
    """
    incident_key = incident_key or _build_dedup_key(title, dedup_window_minutes)

    incident_body: dict = {
        "type": "incident",
        "title": title,
        "service": {"id": service_id, "type": "service_reference"},
        "urgency": urgency,
        "incident_key": incident_key,
        "body": {"type": "incident_body", "details": description},
    }
    if escalation_policy_id:
        incident_body["escalation_policy"] = {
            "id": escalation_policy_id,
            "type": "escalation_policy_reference",
        }

    headers = {
        "Accept": "application/json",
        "Authorization": f"Token token={api_key}",
        "Content-Type": "application/json",
    }
    if from_email:
        headers["From"] = from_email

    try:
        resp = requests.post(
            PD_REST_INCIDENTS_URL,
            json={"incident": incident_body},
            headers=headers,
            timeout=timeout,
        )
        resp.raise_for_status()
        incident = resp.json().get("incident", {})
        logger.info(
            "PagerDuty incident created via REST API: #%s (%s)",
            incident.get("incident_number"), incident.get("html_url"),
        )
        return {
            "success": True,
            "incident_key": incident_key,
            "incident_url": incident.get("html_url"),
            "incident_number": incident.get("incident_number"),
            "message": "Incident created",
        }
    except requests.exceptions.HTTPError as exc:
        body_text = exc.response.text if exc.response else ""
        status_code = exc.response.status_code if exc.response else None
        logger.error("PagerDuty REST API error: %s — %s", exc, body_text)

        # PagerDuty rejects POST /incidents with 400 if incident_key already
        # has an OPEN incident on this service (unlike Events API v2, which
        # merges automatically). The exact error wording -- and even whether
        # a body is returned at all -- isn't reliable enough to pattern-match
        # on, so on ANY 400 we just check directly: does an incident with
        # this key already exist? If so, reuse it. If not, this was some
        # other genuine validation error, and we report it as a failure below.
        if status_code == 400:
            existing = _lookup_incident_by_key(api_key, incident_key, timeout)
            if existing:
                logger.info(
                    "incident_key %s already has an open incident -- reusing it instead of failing.",
                    incident_key,
                )
                return {
                    "success": True,
                    "incident_key": incident_key,
                    "incident_url": existing.get("html_url"),
                    "incident_number": existing.get("incident_number"),
                    "message": "Reused existing open incident (already reported today)",
                }

        return {
            "success": False,
            "incident_key": incident_key,
            "incident_url": None,
            "incident_number": None,
            "message": f"{exc} — {body_text}",
        }
    except requests.exceptions.RequestException as exc:
        logger.error("PagerDuty request failed: %s", exc)
        return {
            "success": False,
            "incident_key": incident_key,
            "incident_url": None,
            "incident_number": None,
            "message": str(exc),
        }


def _lookup_incident_by_key(api_key: str, incident_key: str, timeout: float) -> dict | None:
    """Find an existing incident by its incident_key, via REST API."""
    try:
        resp = requests.get(
            PD_REST_INCIDENTS_URL,
            params={"incident_key": incident_key},
            headers={
                "Authorization": f"Token token={api_key}",
                "Accept": "application/json",
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        incidents = resp.json().get("incidents", [])
        return incidents[0] if incidents else None
    except requests.exceptions.RequestException as exc:
        logger.error("Failed to look up existing incident for key %s: %s", incident_key, exc)
        return None


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