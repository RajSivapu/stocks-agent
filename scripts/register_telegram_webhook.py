"""Register the Supabase Edge Function as the Telegram bot webhook."""
import json
from pathlib import Path
import ssl
import string
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import config


CTX = ssl.create_default_context()


def _telegram(token, method, fields=None):
    data = urllib.parse.urlencode(fields or {}).encode()
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=25, context=CTX) as response:
        body = json.loads(response.read())
    if not body.get("ok"):
        raise RuntimeError(f"Telegram {method} failed")
    return body.get("result")


def main():
    token = config.secret("telegram_bot_token")
    webhook_secret = config.secret("telegram_webhook_secret")
    supabase_url = config.secret("supabase_url").rstrip("/")
    webhook_url = f"{supabase_url}/functions/v1/telegram-portfolio"

    parsed = urllib.parse.urlparse(webhook_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Supabase function URL must use HTTPS")
    allowed_secret_chars = set(string.ascii_letters + string.digits + "_-")
    if not 1 <= len(webhook_secret) <= 256 or not set(webhook_secret) <= allowed_secret_chars:
        raise ValueError("Telegram webhook secret must contain only A-Z, a-z, 0-9, _ and -")

    _telegram(token, "setWebhook", {
        "url": webhook_url,
        "secret_token": webhook_secret,
        "allowed_updates": json.dumps(["message", "callback_query"]),
    })
    info = _telegram(token, "getWebhookInfo")
    if not isinstance(info, dict) or info.get("url") != webhook_url:
        raise RuntimeError("Telegram did not retain the expected webhook URL")
    print("PASS: Telegram webhook registered for message and callback updates")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FAIL: webhook registration ({type(exc).__name__})", file=sys.stderr)
        raise SystemExit(1)
