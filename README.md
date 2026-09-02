# Slack Incident Bot — Lite Demo

Listens to a monitored Slack channel. When someone reports a possible outage,
an LLM (Gemini, Claude, or Grok — your choice) decides whether the message is
really reporting the site being down. If so, the bot runs a real sanity check
(DNS / HTTP / latency / SSL) against your site, and if the result is degraded
or down, it confirms that in a thread reply.

```
Slack message
     │
     ▼
LLM: "is this saying the site is down?" (YES / NO)
     │
     ├── NO ──► bot stays silent
     │
     └── YES ─► run sanity check
                     │
                     ├── GREEN ──► stays silent
                     └── YELLOW / RED ──► reply in thread:
                                          "Confirmed — <url> is really down"
```

This is a deliberately lite prototype: no PagerDuty paging, no incident
tracking — just message in, verified confirmation out. Those pieces can be
layered back on later.

## Setup

### 1. Install dependencies
```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Then install the SDK for whichever LLM provider you're using:
pip install google-genai     # if LLM_PROVIDER=gemini
pip install anthropic        # if LLM_PROVIDER=claude
pip install openai           # if LLM_PROVIDER=grok
```

### 2. Create your Slack app
1. [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**
2. **Socket Mode** → enable → generate an app-level token (scope `connections:write`) → this is `SLACK_APP_TOKEN`
3. **OAuth & Permissions** → Bot Token Scopes:
   - `channels:history`, `channels:read`, `chat:write`
4. **Event Subscriptions** → enable → subscribe to bot event `message.channels`
5. **Install App** to workspace → copy the Bot User OAuth Token as `SLACK_BOT_TOKEN`
6. Copy the Signing Secret (Basic Information) as `SLACK_SIGNING_SECRET`
7. Invite the bot to your monitored channel: `/invite @YourBotName`

### 3. Get an LLM API key
Pick one provider and get its key:
- **Gemini**: [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
- **Claude**: [console.anthropic.com](https://console.anthropic.com)
- **Grok**: [console.x.ai](https://console.x.ai)

### 4. Configure
```bash
cp .env.example .env
# fill in SLACK_*, MONITORED_URLS, LLM_PROVIDER, and that provider's key
```

### 5. Run
```bash
python app.py
```

## Project structure
```
.
├── app.py                 # entry point — starts Socket Mode
├── requirements.txt
├── .env.example
│
├── config/
│   └── settings.py         # all configuration, loaded from .env
│
├── tools/
│   └── sanity_checker.py   # DNS · HTTP · latency · SSL checks
│
└── bot/
    ├── slack_handler.py    # Slack event listener + flow logic
    └── llm_agent.py        # LLM classification (Gemini/Claude/Grok)
```

## Switching LLM providers
Change `LLM_PROVIDER` in `.env` to `gemini`, `claude`, or `grok`, fill in that
provider's API key, and restart. No code changes needed.
