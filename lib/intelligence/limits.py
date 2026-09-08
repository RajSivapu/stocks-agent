"""Capability-specific collection bounds shared by cursors and adapters."""

from __future__ import annotations


DEFAULT_MAX_COLLECTION_PAGE = 10
# One index request plus 20 admitted child sitemaps with up to 5,000 entries
# each, fetched in 50-item pages.
WHITE_HOUSE_SITEMAP_MAX_PAGE = 2_001


def maximum_collection_page(capability_id: str | None) -> int:
    if capability_id == "white_house_sitemap":
        return WHITE_HOUSE_SITEMAP_MAX_PAGE
    return DEFAULT_MAX_COLLECTION_PAGE


__all__ = [
    "DEFAULT_MAX_COLLECTION_PAGE",
    "WHITE_HOUSE_SITEMAP_MAX_PAGE",
    "maximum_collection_page",
]
