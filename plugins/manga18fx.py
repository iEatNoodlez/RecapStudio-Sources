"""Manga18fx: chapters from manga18fx.com, an adult manhwa site with a layout of its own (Madara-like, not Madara).

A Recap Studio source plugin from RecapStudio-Sources. Search, the lists, a series' chapters and a chapter's
pictures are all on the site's pages. Pictures need the site as Referer (HEADERS has it).
"""
RECAP_PLUGIN = 1
NAME = "Manga18fx"
VERSION = "1.0.0"
SITES = ["manga18fx.com"]
LANGUAGE = "en"
NSFW = True
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}]
HEADERS = {"Referer": "https://manga18fx.com/"}
DELAY = 0
TEST_QUERY = "love"

import time
from datetime import datetime
from urllib.parse import urlsplit

import requests

# @include dom

BASE = "https://manga18fx.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})


def _get(url, **kw):
    for attempt in range(2):
        r = _session.get(url, timeout=45, **kw)
        if r.status_code == 429 and attempt == 0:
            wait = r.headers.get("Retry-After", "")
            time.sleep(min(30, int(wait) if wait.isdigit() else 10))
            continue
        if r.status_code in (403, 503) and ("cf_chl" in r.text or "cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        if r.status_code == 404:
            raise ValueError(f"{NAME} has no such page: {url}")
        r.raise_for_status()
        return r
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _cards(doc):
    """Search and list results: [{"url", "title", "cover"}], leaving out the home page's trending strip."""
    out, seen = [], set()
    for block in doc.select(".listupd"):
        if "trending-block" in block.classes:
            continue
        for card in block.select(".page-item"):
            link = card.one(".bigor-manga h3 a, h3 a") or card.one("a[href]")
            url = absolute(BASE, link.get("href")) if link is not None else ""
            if not url or url in seen or "/manga/" not in url:
                continue
            seen.add(url)
            out.append({"url": url, "title": link.text or link.get("title"), "cover": picture(card.one("img"), BASE)})
    return out


def _results(doc):
    items = _cards(doc)
    return {"items": items, "more": bool(items) and doc.one("ul.pagination li.next a") is not None}


def search(query, page=1):
    return _results(parse(_get(f"{BASE}/search", params={"q": query, "page": page}).text))


def browse(list_id, page=1):
    if list_id == "popular":
        return _results(parse(_get(f"{BASE}/hot-manga", params={"page": page}).text))
    return _results(parse(_get(f"{BASE}/page/{page}").text))  # (the home page's latest updates)


def _field(doc, heading):
    """A detail from the series' table ("Type", "Release"...), or ""."""
    for item in doc.select(".post-content_item"):
        head = item.one(".summary-heading")
        if head is not None and head.text.rstrip(":").strip().lower() == heading.lower():
            value = item.one(".summary-content")
            return value.text if value is not None else ""
    return ""


def _date(text):
    """"01 Nov 26" as 2026-11-01 (the text as it is when it's something else, like "2 hours ago")."""
    try:
        return datetime.strptime(text.strip(), "%d %b %y").strftime("%Y-%m-%d")
    except ValueError:
        return text.strip()


def series(url):
    url = BASE + urlsplit(url.strip()).path
    doc = parse(_get(url).text)
    title = doc.one(".post-title h1, h1")
    summary = doc.one(".panel-story-description .dsct, .dsct")
    kind = _field(doc, "Type").lower()
    chapters = []
    for row in doc.select("#chapterlist li"):
        link = row.one("a.chapter-name") or row.one("a[href]")
        if link is None:
            continue
        name = link.text
        date = row.one(".chapter-time")
        chapters.append({"url": absolute(BASE, link.get("href")), "name": name, "number": number(name),
                         "date": _date(date.text) if date is not None else ""})
    chapters.reverse()  # (newest first on the page)
    return {"url": url, "title": title.text if title is not None else "Untitled",
            "cover": picture(doc.one(".summary_image img"), BASE), "summary": summary.text if summary is not None else "",
            "kind": "manga" if kind == "manga" else "webtoon" if kind in ("manhwa", "manhua", "webtoon") else "",
            "year": _field(doc, "Release") or None, "chapters": chapters}


def pages(chapter_url):
    doc = parse(_get(chapter_url).text)
    found = []
    for img in doc.select(".read-content .page-break img") or doc.select(".read-content img"):
        src = picture(img, chapter_url)
        if src and src not in found:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
