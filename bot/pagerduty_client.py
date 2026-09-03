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
    api_key: str,
    service_id: str,
    title: str,
    description: str,
    urgency: str = "high",
    escalation_policy_id: str | None = None,
    from_email: str | None = None,
    incident_key: str | None = None,
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

    Returns:
        dict with keys: success (bool), incident_key (str),
        incident_url (str | None), incident_number (int | None), message (str)
    """
    incident_key = incident_key or _build_dedup_key(title)

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
        logger.error("PagerDuty REST API error: %s — %s", exc, body_text)
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