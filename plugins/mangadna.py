"""MangaDNA: chapters from mangadna.com (English manhwa, much of it adult).

Search is the site's /search?q= page (24 a page, paged with &page=); the lists are its /manga list in the site's own
orders (trending, latest, rating); a series' chapters are all on its page, newest first; a chapter's pictures are
lazy-loaded <img>s (data-src) in its reader.
"""
import re
import time
from urllib.parse import urlencode

import requests

RECAP_PLUGIN = 1
NAME = "MangaDNA"
VERSION = "1.0.0"
SITES = ["mangadna.com"]
LANGUAGE = "en"
NSFW = True
LISTS = [{"id": "popular", "name": "Trending"}, {"id": "latest", "name": "Latest updates"},
         {"id": "rating", "name": "Top rated"}]
HEADERS = {"Referer": "https://mangadna.com/"}
DELAY = 0
TEST_QUERY = "fist"

# @include dom

BASE = "https://mangadna.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
ORDER = {"popular": "trending", "latest": "latest", "rating": "rating"}
KINDS = {"manhwa": "webtoon", "manhua": "webtoon", "webtoon": "webtoon", "manga": "manga", "comic": "comic"}
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), 1)}
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})


def _get(url):
    for attempt in range(2):
        r = _session.get(url, timeout=45)
        if r.status_code == 429 and attempt == 0:
            time.sleep(min(30, int(r.headers.get("Retry-After") or 10)))
            continue
        if r.status_code in (403, 503) and ("cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        if r.status_code == 404:
            raise ValueError(f"{NAME} has no such page: {url}")
        r.raise_for_status()
        return r
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _results(url):
    """Search and list results (the site's .home-item cards): {"items", "more"}."""
    doc = parse(_get(url).text)
    items, seen = [], set()
    for card in doc.select(".home-item"):
        link = card.one(".htitle a") or card.one("a[href*='/manga/']")
        if link is None:
            continue
        url = absolute(BASE, link.get("href"))
        if url in seen:
            continue
        seen.add(url)
        img = card.one(".hthumb img") or card.one("img")
        items.append({"url": url, "title": link.get("title") or link.text or (img.get("alt") if img is not None else ""),
                      "cover": picture(img, BASE), "kind": "webtoon"})
    more = doc.one("ul.pagination li.next a") is not None
    return {"items": items, "more": bool(items) and more}


def search(query, page=1):
    values = {"q": query.strip()}
    if page > 1:
        values["page"] = page
    return _results(f"{BASE}/search?{urlencode(values)}")


def browse(list_id, page=1):
    if list_id not in ORDER:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    values = {"orderby": ORDER[list_id]}
    if page > 1:
        values["page"] = page
    return _results(f"{BASE}/manga?{urlencode(values)}")


def _series_url(url):
    """The series' link from its own or one of its chapters' (/manga/<slug>/chapter-<n>)."""
    m = re.match(r"https?://[^/]+/manga/([^/?#]+)", url.strip())
    if not m:
        raise ValueError(f"That isn't a series' link on {NAME}: {url}")
    return f"{BASE}/manga/{m.group(1)}"


def _date(text):
    """The site's "01 Nov 26" / "Nov 01, 2026" as "2026-11-01" (else as it is)."""
    text = (text or "").strip()
    m = re.match(r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{2,4})$", text) or re.match(r"([A-Za-z]{3})\w*\s+(\d{1,2}),\s*(\d{4})$", text)
    if not m:
        return text
    if m.group(1).isdigit():
        day, mon, year = m.group(1), m.group(2), m.group(3)
    else:
        mon, day, year = m.group(1), m.group(2), m.group(3)
    if mon.lower() not in MONTHS:
        return text
    year = int(year)
    return f"{year + 2000 if year < 100 else year}-{MONTHS[mon.lower()]:02d}-{int(day):02d}"


def _detail(doc, label):
    """The value of a "Type" / "Release" row on a series' page."""
    for row in doc.select(".post-content_item"):
        heading, value = row.one(".summary-heading"), row.one(".summary-content")
        if heading is not None and value is not None and heading.text.rstrip(": ").lower() == label:
            return value.text
    return ""


def series(url):
    url = _series_url(url)
    doc = parse(_get(url).text)
    title = doc.one(".post-title h1, h1")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    cover = doc.one("meta[property='og:image']")
    about = doc.one(".panel-story-description")
    summary = ""
    if about is not None:  # ("Summary" heading, then the text)
        body = about.one("div, p")
        summary = body.text if body is not None else about.text.removeprefix("Summary").strip()
    year = _detail(doc, "release")
    kind = KINDS.get(_detail(doc, "type").strip().lower(), "webtoon")

    chapters, seen = [], set()
    for row in doc.select("ul.row-content-chapter li, li.a-h"):
        link = row.one("a.chapter-name") or row.one("a[href*='/chapter-']")
        if link is None:
            continue
        href = absolute(BASE, link.get("href"))
        if href in seen:
            continue
        seen.add(href)
        name = link.text or link.get("title")
        when = row.one(".chapter-time")
        chapters.append({"url": href, "name": name, "number": number(name),
                         "date": _date(when.get("title") or when.text) if when is not None else ""})
    chapters.reverse()  # (newest first on the page)
    return {"url": url, "title": title.text,
            "cover": absolute(BASE, cover.get("content")) if cover is not None else picture(doc.one(".summary_image img"), BASE),
            "summary": summary, "kind": kind, "year": int(year) if year.strip().isdigit() else None,
            "chapters": chapters}


def pages(chapter_url):
    doc = parse(_get(chapter_url.strip()).text)
    found = []
    for img in doc.select(".read-content img, .read-manga img"):
        src = picture(img, BASE)
        if src and "/images/" not in src and src not in found:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
