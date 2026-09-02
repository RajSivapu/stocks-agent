import json, os, pathlib
ROOT = pathlib.Path(__file__).resolve().parents[1]

def load_settings() -> dict:
    return json.loads((ROOT / "config" / "settings.json").read_text())

def load_watchlist() -> dict:
    return json.loads((ROOT / "config" / "watchlist.json").read_text())

def secret(name: str) -> str:
    """Env var wins (cloud Routine); fall back to local git-ignored secrets file."""
    env_map = {
        "finnhub_api_key": "FINNHUB_API_KEY",
        "alphavantage_api_key": "ALPHAVANTAGE_API_KEY",
        "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
        "telegram_chat_id": "TELEGRAM_CHAT_ID",
        "supabase_url": "SUPABASE_URL",
        "supabase_service_role_key": "SUPABASE_SERVICE_ROLE_KEY",
        "market_agent_secret": "MARKET_AGENT_SECRET",
    }
    v = os.environ.get(env_map.get(name, name.upper()))
    if v:
        return v
    f = ROOT / "config" / "secrets.local.json"
    if f.exists():
        return json.loads(f.read_text()).get(name, "")
    raise KeyError(f"secret {name!r} not found in env or secrets.local.json")


def optional_secret(name: str) -> str:
    """Return a local secret when present, or allow an outbound proxy to inject it."""
    try:
        return secret(name)
    except KeyError:
        return ""
