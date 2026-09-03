from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def normalized(path: str) -> str:
    return " ".join((ROOT / path).read_text(encoding="utf-8").lower().split())


def test_friend_onboarding_requires_every_human_and_technical_gate():
    text = normalized("docs/runbooks/friend-onboarding.md")
    for phrase in (
        "custom smtp",
        "non-team",
        "phone mail client",
        "owner-soak receipt",
        "36 hours",
        "30 days",
        "synthetic owner first",
        "separate claude account",
        "unassisted",
        "real no-write handshake",
        "private telegram",
        "six-digit otp",
        "consent",
        "no brokerage",
        "account export",
        "account deletion",
        "single operator",
        "one trusted friend",
        "never batch owners",
        "disable further invitations",
    ):
        assert phrase in text


def test_readme_describes_the_complete_candidate_without_legacy_secret_setup():
    text = normalized("README.md")
    for phrase in (
        "not deployed to staging or production",
        "provider-neutral",
        "claude routines is the only release-one provider adapter",
        "one unscheduled routine per owner",
        "application owns the schedule",
        "your mac can be off",
        "no model api key",
        "cannot place, modify, or cancel",
        "verify_schema_parity.py --verify",
        "friend invitations remain disabled",
    ):
        assert phrase in text
    for forbidden in (
        "this release is single-owner",
        "market_agent_secret",
        "telegram_owner_chat_id",
        "three anthropic cloud routines",
    ):
        assert forbidden not in text


def test_routine_setup_is_per_owner_unscheduled_and_credential_proxy_only():
    text = normalized("routines/README.md")
    for phrase in (
        "one unscheduled routine per owner",
        "application owns every market schedule",
        "environment variables empty",
        "setup script empty",
        "connectors off",
        "schedule off",
        "host-bound api credential",
        "green provider status is not proof",
        "daily routine allowance",
    ):
        assert phrase in text
    for forbidden in ("market_agent_secret", "finnhub_api_key=", "three weekday routines"):
        assert forbidden not in text


def test_privacy_and_risk_documents_cover_provider_and_operator_limits():
    privacy = normalized("docs/privacy.md")
    risk = normalized("docs/risk-disclosure.md")
    for phrase in (
        "separate provider account",
        "operator can access",
        "encrypted recovery archives",
        "telegram",
        "account deletion",
    ):
        assert phrase in privacy
    for phrase in (
        "not continuously monitored",
        "not a stop order",
        "same-model checker",
        "partial or total loss",
        "personally place every trade",
    ):
        assert phrase in risk
