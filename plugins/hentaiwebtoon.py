"""Hentai Webtoon: chapters from hentaiwebtoon.com, a small adult manhwa site with a layout of its own.

A Recap Studio source plugin from RecapStudio-Sources. The site has a dozen series, all on its /manga/ page; its
search only filters those cards in the browser, so search() does the same with the words typed. A series' chapters
are all on its page (/manga/<slug>/, newest first) and a chapter's pictures are plain <img>s on its page
(/manga/<slug>/chapter-<n>/), served by the site itself.
"""
RECAP_PLUGIN = 1
NAME = "Hentai Webtoon"
VERSION = "1.0.0"
SITES = ["hentaiwebtoon.com"]
LANGUAGE = "en"
NSFW = True
LISTS = [{"id": "all", "name": "All series"}, {"id": "manhwa", "name": "Manhwa"}]
HEADERS = {"Referer": "https://hentaiwebtoon.com/"}
DELAY = 0
TEST_QUERY = "circles"

import time
from urllib.parse import urlsplit

import requests

# @include dom

BASE = "https://hentaiwebtoon.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
PATHS = {"all": "/manga/", "manhwa": "/manga-genre/manhwa/"}
KINDS = {"manga": "manga", "manhwa": "webtoon", "manhua": "webtoon", "webtoon": "webtoon"}
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
        if "charset" not in r.headers.get("content-type", "").lower():
            r.encoding = "utf-8"  # (the pages say so in a <meta>, but the server doesn't)
        return r
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _cards(path):
    """A page's series cards: [{"url", "title", "cover", "terms"}] (terms: what the site's own search matches)."""
    items, seen = [], set()
    for card in parse(_get(BASE + path).text).select("article.webtoon-card"):
        link = card.one("h2 a") or card.one("a[href*='/manga/']")
        if link is None:
            continue
        url = absolute(BASE, link.get("href"))
        if url in seen:
            continue
        seen.add(url)
        title = link.text or link.get("aria-label").removeprefix("Open ")
        items.append({"url": url, "title": title, "cover": picture(card.one("img"), BASE), "kind": "webtoon",
                      "terms": " ".join((card.get("data-title-terms") + " " + title).lower().split())})
    return items


def search(query, page=1):
    words = query.lower().split()
    items = [item for item in _cards("/manga/") if all(word in item["terms"] for word in words)] if page == 1 else []
    return {"items": [{k: v for k, v in item.items() if k != "terms"} for item in items], "more": False}


def browse(list_id, page=1):
    if list_id not in PATHS:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    items = _cards(PATHS[list_id]) if page == 1 else []  # (one page: the site has no more)
    return {"items": [{k: v for k, v in item.items() if k != "terms"} for item in items], "more": False}


def _detail(doc, label):
    """A row of the series' details ("Type", "Status"...), or ""."""
    for dt in doc.select("dl dt"):
        if dt.text.strip().lower() == label.lower() and dt.parent is not None:
            dd = dt.parent.one("dd")
            return dd.text if dd is not None else ""
    return ""


def series(url):
    path = urlsplit(url.strip()).path
    if not path.startswith("/manga/") or len([p for p in path.split("/") if p]) != 2:
        raise ValueError(f"That isn't a series' link on {NAME}: {url}")
    url = BASE + path.rstrip("/") + "/"
    doc = parse(_get(url).text)
    title = doc.one("h1")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    cover = doc.one("meta[property='og:image']")
    summary = doc.one(".series-synopsis p")
    rows = []
    for li in doc.select("#chapter-rows li, ol.chapter-rows li"):
        link = li.one("a[href]")
        if link is None:
            continue
        name = link.text or f"Chapter {li.get('data-episode-number')}"
        sequence = li.get("data-sequence") or li.get("data-episode-number")
        try:
            order = float(sequence)
        except ValueError:
            order = number(name) or 0.0
        rows.append((order, {"url": absolute(BASE, link.get("href")), "name": name,
                             "number": number(li.get("data-episode-number")) or number(name), "date": ""}))
    rows.sort(key=lambda row: row[0])  # (newest first on the page; the site's own script sorts them too)
    chapters, seen = [], set()
    for _, chapter in rows:
        if chapter["url"] not in seen:
            seen.add(chapter["url"])
            chapters.append(chapter)
    return {"url": url, "title": title.text,
            "cover": absolute(BASE, cover.get("content")) if cover is not None else picture(doc.one("img.series-poster-image"), BASE),
            "summary": summary.text if summary is not None else "",
            "kind": KINDS.get(_detail(doc, "Type").lower(), ""), "chapters": chapters}


def pages(chapter_url):
    doc = parse(_get(chapter_url).text)
    found = []
    for img in doc.select(".scroll-pages img") or doc.select("[data-scroll-reader] img"):
        src = picture(img, chapter_url)
        if src and src not in found:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
