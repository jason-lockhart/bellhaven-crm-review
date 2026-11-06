from __future__ import annotations

import re
from collections import deque
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

WEBSITE_ROOT = "https://analyst-assessment-production.up.railway.app"
COMMUNITIES_URL = f"{WEBSITE_ROOT}/communities"
USER_AGENT = "BellhavenCRMReview/1.0 (read-only community directory audit)"


class ScrapeError(RuntimeError):
    """Raised when the public directory cannot be read reliably."""


def _same_site(url: str) -> bool:
    return urlparse(url).netloc == urlparse(WEBSITE_ROOT).netloc


def _is_community_detail(url: str) -> bool:
    parsed = urlparse(url)
    return _same_site(url) and parsed.path.startswith("/communities/") and parsed.path != "/communities/"


def _absolute(href: str, current_url: str) -> str:
    return urljoin(current_url, href.split("#", 1)[0])


def parse_listing_page(html: str, current_url: str) -> tuple[list[dict], list[str]]:
    """Parse one directory page and discover its detail and pagination links."""
    soup = BeautifulSoup(html, "html.parser")
    communities: list[dict] = []
    detail_urls: set[str] = set()

    for card in soup.select(".card"):
        link = card.select_one("h3 a[href]")
        if not link:
            continue
        detail_url = _absolute(link["href"], current_url)
        if not _is_community_detail(detail_url):
            continue
        detail_urls.add(detail_url)
        communities.append(
            {
                "slug": urlparse(detail_url).path.rstrip("/").rsplit("/", 1)[-1],
                "name": link.get_text(" ", strip=True),
                "source_url": detail_url,
            }
        )

    next_pages: set[str] = set()
    for link in soup.select(".pager a[href], a[rel='next'][href]"):
        url = _absolute(link["href"], current_url)
        parsed = urlparse(url)
        if _same_site(url) and parsed.path.rstrip("/") == "/communities":
            next_pages.add(url)

    return communities, sorted(next_pages)


def parse_community_detail(html: str, source_url: str) -> dict:
    """Extract the fields shown on one community's detail page."""
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one("h1")
    if not heading:
        raise ScrapeError(f"Community detail page is missing its name: {source_url}")

    values: dict[str, object] = {
        "name": heading.get_text(" ", strip=True),
        "slug": urlparse(source_url).path.rstrip("/").rsplit("/", 1)[-1],
        "address": "",
        "city": "",
        "state": "",
        "zip": "",
        "care_offerings": [],
        "source_url": source_url,
    }

    detail = soup.select_one("dl.detail")
    if detail:
        for term in detail.find_all("dt"):
            label = term.get_text(" ", strip=True).casefold()
            definition = term.find_next_sibling("dd")
            if not definition:
                continue
            if label == "address":
                lines = [
                    line.strip()
                    for line in definition.get_text("\n", strip=True).splitlines()
                    if line.strip()
                ]
                if lines:
                    values["address"] = lines[0]
                locality = " ".join(lines[1:]) if len(lines) > 1 else ""
                match = re.match(r"^(.*?),\s*([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$", locality)
                if match:
                    values["city"], values["state"], values["zip"] = match.groups()
            elif label == "care offerings":
                values["care_offerings"] = [
                    badge.get_text(" ", strip=True)
                    for badge in definition.select(".badge")
                    if badge.get_text(" ", strip=True)
                ]

    if not values["name"] or not values["address"] or not values["city"] or not values["state"]:
        raise ScrapeError(f"Community detail page is missing required location fields: {source_url}")
    return values


def scrape_communities(
    session: requests.Session | None = None,
    timeout: float = 20,
) -> list[dict]:
    """Crawl the home page and every linked directory page, then read each detail page."""
    client = session or requests.Session()
    client.headers.setdefault("User-Agent", USER_AGENT)

    pending_pages = deque([WEBSITE_ROOT + "/"])
    visited_pages: set[str] = set()
    detail_urls: dict[str, str] = {}

    while pending_pages:
        page_url = pending_pages.popleft()
        page_url = page_url.split("#", 1)[0]
        if page_url in visited_pages:
            continue
        visited_pages.add(page_url)

        try:
            response = client.get(page_url, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ScrapeError(f"Could not read Bellhaven page {page_url}: {exc}") from exc

        soup = BeautifulSoup(response.text, "html.parser")
        for link in soup.select("a[href]"):
            detail_url = _absolute(link["href"], response.url)
            if _is_community_detail(detail_url):
                detail_urls[detail_url] = detail_url
                continue
            parsed_link = urlparse(detail_url)
            if (
                _same_site(detail_url)
                and parsed_link.path.rstrip("/") == "/communities"
                and detail_url not in visited_pages
            ):
                pending_pages.append(detail_url)

        if urlparse(response.url).path.rstrip("/") == "/communities":
            _, next_pages = parse_listing_page(response.text, response.url)
            for next_page in next_pages:
                if next_page not in visited_pages:
                    pending_pages.append(next_page)

    communities: list[dict] = []
    for detail_url in sorted(detail_urls):
        try:
            response = client.get(detail_url, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise ScrapeError(f"Could not read Bellhaven community {detail_url}: {exc}") from exc
        communities.append(parse_community_detail(response.text, response.url))

    if not communities:
        raise ScrapeError("The Bellhaven site returned no community detail pages.")
    return communities
