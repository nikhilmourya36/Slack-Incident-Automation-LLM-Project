"""
Minimal Slack Socket Mode connectivity test.
No LLM, no sanity checks, no channel filtering — just proves the
back-and-forth between Slack and this process actually works.

Run this standalone (not through app.py / bot/slack_handler.py).
"""
import logging
import os

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logging.getLogger("slack_bolt").setLevel(logging.DEBUG)
logging.getLogger("slack_sdk").setLevel(logging.DEBUG)

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_SIGNING_SECRET = os.environ["SLACK_SIGNING_SECRET"]
SLACK_APP_TOKEN = os.environ["SLACK_APP_TOKEN"]

app = App(token=SLACK_BOT_TOKEN, signing_secret=SLACK_SIGNING_SECRET)


@app.event("message")
def handle_message(event, say, logger):
    logger.info("RECEIVED message event: %r", event)
    text = event.get("text", "")
    if event.get("bot_id"):
        return  # ignore our own / other bots' messages
    say(text=f"👋 pong — I received: {text!r}")


@app.event("app_mention")
def handle_mention(event, say, logger):
    logger.info("RECEIVED app_mention event: %r", event)
    say(text="👋 pong — got your mention")


if __name__ == "__main__":
    logging.getLogger(__name__).info("Starting connectivity test bot...")
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()
