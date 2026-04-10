"""
Run once after deploy to register the webhook with Telegram.
Usage: BOT_TOKEN=xxx RENDER_URL=https://your-app.onrender.com python setup_webhook.py
"""
import os, urllib.request, json

token = os.environ["BOT_TOKEN"]
url = os.environ["RENDER_URL"].rstrip("/")

webhook_url = f"https://api.telegram.org/bot{token}/setWebhook?url={url}"
with urllib.request.urlopen(webhook_url) as r:
    print(json.loads(r.read()))
