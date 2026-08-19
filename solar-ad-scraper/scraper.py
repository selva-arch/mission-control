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

import random
import time
from pathlib import Path
from urllib.parse import quote

# Imported lazily inside Session.__enter__ so that enrichment, CSV export and
# dashboard queries work on a machine without a browser installed.

# Persisted browser profile so you only log into Facebook once.
PROFILE_DIR = Path(__file__).parent / ".fb-profile"

AD_LIBRARY_URL = (
    "https://www.facebook.com/ads/library/"
    "?active_status={active_status}&ad_type=all&country={country}"
    "&q={query}&media_type=all"
)

# "Everything this Page is running" — richer per request than a keyword search,
# because it returns an advertiser's whole live rotation rather than only the
# ads whose copy happens to match a search term.
PAGE_LIBRARY_URL = (
    "https://www.facebook.com/ads/library/"
    "?active_status={active_status}&ad_type=all&country={country}"
    "&view_all_page_id={page_id}&media_type=all"
)


def _search_url(keyword: str, country: str, active_status: str) -> str:
    return AD_LIBRARY_URL.format(
        country=country, query=quote(keyword), active_status=active_status
    )


def _page_url(page_id: str, country: str, active_status: str) -> str:
    return PAGE_LIBRARY_URL.format(
        country=country, page_id=quote(str(page_id)), active_status=active_status
    )


def search(keyword: str, country: str, max_scrolls: int, headless: bool,
           active_status: str = "active") -> list[str]:
    """Run one Ad Library keyword search and return raw GraphQL response bodies.

    Kept for backwards compatibility and one-off `--keyword` runs. A full sweep
    should use Session, which reuses one browser across many queries instead of
    paying browser startup 200+ times.
    """
    with Session(country, headless, active_status) as s:
        return s.fetch_keyword(keyword, max_scrolls)


class Session:
    """A single browser kept open across many Ad Library queries.

    Launching Chromium per query would dominate runtime on a 200-target sweep,
    and repeated cold starts look far more robotic than one long human-ish
    session. Paces itself between queries; this is personal competitive
    research, not bulk harvesting.
    """

    def __init__(self, country: str, headless: bool, active_status: str = "all",
                 min_query_gap_s: float = 4.0):
        self.country = country
        self.headless = headless
        self.active_status = active_status
        self.min_query_gap_s = min_query_gap_s
        self._pw = None
        self._context = None
        self._page = None
        self._last_query_at = 0.0

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._context = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=self.headless,
            viewport={"width": 1280, "height": 900},
            locale="en-AU",
        )
        self._page = self._context.pages[0] if self._context.pages \
            else self._context.new_page()
        return self

    def __exit__(self, *exc):
        try:
            if self._context:
                self._context.close()
        finally:
            if self._pw:
                self._pw.stop()
        return False

    # -- internals ---------------------------------------------------------

    def _cooldown(self):
        """Space out queries with jitter so the cadence is not machine-regular."""
        elapsed = time.time() - self._last_query_at
        wait = self.min_query_gap_s - elapsed
        if wait > 0:
            time.sleep(wait)
        time.sleep(random.uniform(0.4, 1.6))

    def _collect(self, url: str, max_scrolls: int) -> list[str]:
        bodies: list[str] = []
        page = self._page

        def on_response(response):
            if "/api/graphql/" not in response.url and \
               "/ads/library/async/" not in response.url:
                return
            try:
                text = response.text()
            except Exception:
                return
            if "snapshot" in text or "ad_archive_id" in text or "adArchiveID" in text:
                bodies.append(text)

        page.on("response", on_response)
        try:
            self._cooldown()
            page.goto(url, wait_until="domcontentloaded")

            if "login" in page.url or "checkpoint" in page.url:
                print("  → Facebook wants a login. Log in in the opened window, "
                      "then results will load automatically.")
                _wait_for_results(page, timeout_s=180)

            _wait_for_results(page, timeout_s=30)
            _scroll_to_load(page, max_scrolls)
            page.wait_for_timeout(1500)
        finally:
            page.remove_listener("response", on_response)
            self._last_query_at = time.time()
        return bodies

    # -- public ------------------------------------------------------------

    def fetch_keyword(self, keyword: str, max_scrolls: int) -> list[str]:
        return self._collect(
            _search_url(keyword, self.country, self.active_status), max_scrolls
        )

    def fetch_page(self, page_id: str, max_scrolls: int) -> list[str]:
        return self._collect(
            _page_url(page_id, self.country, self.active_status), max_scrolls
        )


def _wait_for_results(page, timeout_s: int) -> None:
    """Wait until the results count text or an ad card appears."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        body = page.inner_text("body") if page.locator("body").count() else ""
        if "result" in body.lower() and "library" in body.lower():
            return
        page.wait_for_timeout(1000)


def _scroll_to_load(page, max_scrolls: int) -> None:
    """Scroll to the bottom repeatedly to trigger lazy-loaded result batches.

    Delays are jittered rather than fixed: a metronome-regular 1800 ms scroll is
    both a fingerprint and harder on the endpoint than a human reading pace.
    """
    last_height = 0
    for _ in range(max_scrolls):
        page.mouse.wheel(0, random.randint(15000, 24000))
        page.wait_for_timeout(random.randint(1500, 3200))
        height = page.evaluate("document.body.scrollHeight")
        if height == last_height:
            # Nudge once more in case of a slow batch, then stop if still stuck.
            page.wait_for_timeout(random.randint(1200, 2200))
            height = page.evaluate("document.body.scrollHeight")
            if height == last_height:
                break
        last_height = height
