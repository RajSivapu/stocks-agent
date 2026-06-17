"""Telegram HTML sender.

Sends an HTML-formatted message to the owner's chat, splitting long messages at
~3500 chars on blank-line (block) boundaries so we never exceed Telegram's 4096 cap
or break an HTML tag mid-block. Returns the last message_id. stdlib urllib only.
"""
import json, ssl, urllib.parse, urllib.request
from lib import config
ctx = ssl.create_default_context()


def send(html: str) -> int:
    tok = config.secret("telegram_bot_token"); chat = config.secret("telegram_chat_id")
    parts = [html]
    if len(html) > 3500:
        parts = []; buf = ""
        for block in html.split("\n\n"):
            if len(buf) + len(block) > 3500:
                parts.append(buf); buf = block
            else:
                buf = (buf + "\n\n" + block) if buf else block
        if buf:
            parts.append(buf)
    last = None
    for p in parts:
        data = urllib.parse.urlencode({"chat_id": chat, "text": p, "parse_mode": "HTML",
                                       "disable_web_page_preview": "true"}).encode()
        r = urllib.request.urlopen(urllib.request.Request(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data), timeout=25, context=ctx)
        last = json.loads(r.read())["result"]["message_id"]
    return last
