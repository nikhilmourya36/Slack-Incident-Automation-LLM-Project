"""
Entry point.
Starts the Slack Bolt app in Socket Mode.
"""
from __future__ import annotations

import logging

from slack_bolt.adapter.socket_mode import SocketModeHandler

from config.settings import SLACK_APP_TOKEN
from bot.slack_handler import app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

if __name__ == "__main__":
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    logging.getLogger(__name__).info("Starting Slack Incident Bot (Socket Mode)...")
    handler.start()
