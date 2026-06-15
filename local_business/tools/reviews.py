"""
Reviews tool — fetch Google reviews + post approved replies.

Two implementations behind one interface:
  • API mode  — Google Business Profile API (when the client grants access).
  • Scrape mode — agent-driven WebFetch of the public Google Maps listing
    (read-only, no creds) for clients who won't connect the API.

Only the read side runs unattended. Posting a reply is always gated by owner
approval upstream, then performed here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Review:
    review_id: str
    author: str
    stars: int
    text: str
    posted_at: str


def fetch_since(business, cursor: str | None) -> list[Review]:
    """Return reviews newer than `cursor`. TODO: wire GBP API or scrape mode."""
    mode = business.integrations.get("reviews")
    if mode == "google_api":
        raise NotImplementedError("GBP API client — add credentials in business.json")
    # Scrape mode: the agent gathers via WebFetch inside its prompt, so this
    # returns [] and the workflow's Claude step does the reading. Kept as the
    # seam for when we move scraping out of the prompt into deterministic code.
    return []


def post_reply(business, review_id: str, reply: str) -> bool:
    """Post an owner-approved reply. TODO: GBP API write."""
    raise NotImplementedError("Posting replies requires GBP API write scope")
