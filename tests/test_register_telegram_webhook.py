from scripts import register_telegram_webhook


def test_main_uses_edge_env_webhook_secret_when_root_config_is_missing(
    monkeypatch, tmp_path
):
    edge_env = tmp_path / ".env.local"
    edge_env.write_text("TELEGRAM_WEBHOOK_SECRET=edge-only-secret\n")
    configured = {
        "telegram_bot_token": "test-token",
        "supabase_url": "https://example.supabase.co",
    }
    calls = []

    def config_secret(name):
        if name == "telegram_webhook_secret":
            raise KeyError(name)
        return configured[name]

    monkeypatch.setattr(
        register_telegram_webhook.config,
        "secret",
        config_secret,
    )
    monkeypatch.setattr(register_telegram_webhook, "EDGE_ENV_FILE", edge_env)

    def fake_telegram(token, method, fields=None):
        calls.append((token, method, fields))
        if method == "getWebhookInfo":
            return {
                "url": "https://example.supabase.co/functions/v1/telegram-portfolio"
            }
        return True

    monkeypatch.setattr(register_telegram_webhook, "_telegram", fake_telegram)

    assert register_telegram_webhook.main() == 0
    assert calls[0][1] == "setWebhook"
    assert calls[0][2]["secret_token"] == "edge-only-secret"
