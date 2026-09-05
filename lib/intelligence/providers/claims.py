"""Conservative news claim identity; unrecognized claims retain exact wording."""
import hashlib
import re
from urllib.parse import urlsplit

from . import bounded_text, parse_timestamp


def news_claim(title, summary, published_at, source, url):
    text = bounded_text(f"{title or ''} {summary or ''}").lower()
    words = re.sub(r"[^a-z0-9]+", " ", text).strip()
    positive = bool(re.search(r"\b(raise[sd]?|lift[sed]*|boost[sed]*|increase[sd]?|upgrade[sd]?)\b", words))
    negative = bool(re.search(r"\b(cut[s]?|lower[sed]*|reduce[sd]?|downgrade[sd]?)\b", words))
    polarity = "positive" if positive and not negative else "negative" if negative and not positive else "neutral"
    published = parse_timestamp(published_at)
    years = set(re.findall(r"\b20[0-9]{2}\b", words))
    if not years and published and re.search(r"\b(annual|full year)\b", words):
        years = {str(published.year)}
    # Group only an explicit metric and annual period. Ambiguous or quarterly
    # claims keep wording identity; a headline cannot supply invented exposure.
    if (len(years) == 1 and re.search(r"\b(revenue|sales)\b", words)
            and re.search(r"\b(guidance|outlook|forecast)\b", words)
            and not re.search(r"\b(q[1-4]|quarter)\b", words)):
        claim = f"annual_revenue_guidance:{next(iter(years))}"
    else:
        claim = "news:" + hashlib.sha256(words.encode()).hexdigest()
    parsed = urlsplit(str(url or ""))
    publisher = re.sub(r"[^a-z0-9]+", "", str(source or "").lower())
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return {"claim_key": claim, "polarity": polarity,
            "claim_polarity": "denied" if negative else "affirmed" if positive else "unknown",
            "source_identity": publisher or host,
            "upstream_identity": f"{host}{parsed.path.rstrip('/')}"}
