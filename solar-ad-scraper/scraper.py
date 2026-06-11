"""
Drive the Meta Ad Library web UI with Playwright and capture the underlying
GraphQL responses for a search query.

Why GraphQL interception instead of DOM scraping?
The Ad Library renders ad cards with obfuscated, frequently-changing CSS class
names, so DOM selectors break constantly. The page fetches its results from
Facebook's GraphQL endpoint as structured JSON, which is far more stable. We
listen to network responses, keep the ones that look like Ad Library payloads,
and let extract.py walk the JSON for ad nodes.

This only touches the *public* Ad Library. It paces itself like a human and
reuses a single logged-in session. Keep volume modest — this is for personal
research, not bulk harvesting (see README for the ToS note).
"""

from __future__ import annotations

import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

# Persisted browser profile so you only log into Facebook once.
PROFILE_DIR = Path(__file__).parent / ".fb-profile"

AD_LIBRARY_URL = (
    "https://www.facebook.com/ads/library/"
    "?active_status=active&ad_type=all&country={country}"
    "&q={query}&media_type=all"
)


def _search_url(keyword: str, country: str) -> str:
    return AD_LIBRARY_URL.format(country=country, query=quote(keyword))


def search(keyword: str, country: str, max_scrolls: int, headless: bool) -> list[bytes]:
    """Run one Ad Library search and return the raw GraphQL response bodies.

    Returns a list of response-body strings (JSON, possibly newline-delimited
    multi-object streams). Parsing is left to extract.parse_ad_nodes().
    """
    bodies: list[str] = []

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=headless,
            viewport={"width": 1280, "height": 900},
            locale="en-AU",
        )
        page = context.pages[0] if context.pages else context.new_page()

        def on_response(response):
            url = response.url
            if "/api/graphql/" not in url and "/ads/library/async/" not in url:
                return
            try:
                text = response.text()
            except Exception:
                return
            # Cheap pre-filter: only keep payloads that mention ad-library shapes.
            if "snapshot" in text or "ad_archive_id" in text or "adArchiveID" in text:
                bodies.append(text)

        page.on("response", on_response)

        page.goto(_search_url(keyword, country), wait_until="domcontentloaded")

        # First run: give the user time to log in if redirected to a login wall.
        if "login" in page.url or "checkpoint" in page.url:
            print("  → Facebook wants a login. Log in in the opened window, "
                  "then results will load automatically.")
            _wait_for_results(page, timeout_s=180)

        _wait_for_results(page, timeout_s=30)
        _scroll_to_load(page, max_scrolls)

        # Let the final batch of responses settle.
        page.wait_for_timeout(1500)
        context.close()

    return bodies


def _wait_for_results(page, timeout_s: int) -> None:
    """Wait until the results count text or an ad card appears."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        body = page.inner_text("body") if page.locator("body").count() else ""
        if "result" in body.lower() and "library" in body.lower():
            return
        page.wait_for_timeout(1000)


def _scroll_to_load(page, max_scrolls: int) -> None:
    """Scroll to the bottom repeatedly to trigger lazy-loaded result batches."""
    last_height = 0
    for _ in range(max_scrolls):
        page.mouse.wheel(0, 20000)
        # Human-ish pause so we don't hammer the endpoint.
        page.wait_for_timeout(1800)
        height = page.evaluate("document.body.scrollHeight")
        if height == last_height:
            # Nudge once more in case of a slow batch, then stop if still stuck.
            page.wait_for_timeout(1500)
            height = page.evaluate("document.body.scrollHeight")
            if height == last_height:
                break
        last_height = height
