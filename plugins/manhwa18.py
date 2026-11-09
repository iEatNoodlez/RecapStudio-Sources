"""Manhwa18: chapters from manhwa18.com, an adult manhwa site with a layout of its own (the MyManga template).

A Recap Studio source plugin from RecapStudio-Sources. Search (/tim-kiem?q=), the lists (/manga-list?sort=), a
series' chapters and a chapter's lazy-loaded pictures (data-src) are all on the site's pages. Pictures are on
min.manhwa18.net and need the site as Referer (HEADERS has it).
"""
RECAP_PLUGIN = 1
NAME = "Manhwa18"
VERSION = "1.0.0"
SITES = ["manhwa18.com"]
LANGUAGE = "en"
NSFW = True
LISTS = [{"id": "popular", "name": "Most viewed"}, {"id": "latest", "name": "Latest updates"},
         {"id": "new", "name": "New series"}]
HEADERS = {"Referer": "https://manhwa18.com/"}
DELAY = 0
TEST_QUERY = "love"

import re
import time
from urllib.parse import urlsplit

import requests

# @include dom

BASE = "https://manhwa18.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
SORT = {"popular": "top", "latest": "update", "new": "new"}  # (the list page's sort menu)
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


def _background(node):
    """The picture of a lazy background block (data-bg, or a style's url(...))."""
    if node is None:
        return ""
    if node.get("data-bg"):
        return absolute(BASE, node.get("data-bg"))
    m = re.search(r"url\(\s*['\"]?([^'\")]+)", node.get("style"))
    return absolute(BASE, m.group(1)) if m else ""


def _results(doc):
    """Search and list results: the page's series cards, and whether a next page is linked."""
    items, seen = [], set()
    for card in doc.select(".thumb-item-flow"):
        if "see-more" in card.classes:
            continue
        link = card.one(".series-title a") or card.one("a[href*='/manga/']")
        if link is None:
            continue
        url = absolute(BASE, link.get("href"))
        if "/manga/" not in url or url in seen:
            continue
        seen.add(url)
        items.append({"url": url, "title": link.text or link.get("title"), "cover": _background(card.one(".lazy-bg")),
                      "kind": "webtoon"})
    more = any("disabled" not in a.classes for a in doc.select("a.paging_item.next"))
    return {"items": items, "more": bool(items) and more}


def search(query, page=1):
    return _results(parse(_get(f"{BASE}/tim-kiem", params={"q": query, "page": page}).text))


def browse(list_id, page=1):
    if list_id not in SORT:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    return _results(parse(_get(f"{BASE}/manga-list", params={"sort": SORT[list_id], "page": page}).text))


def _date(text):
    """"437 view - 08/11/2026" as 2026-11-08, or ""."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text or "")
    return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else ""


def series(url):
    url = BASE + urlsplit(url.strip()).path
    doc = parse(_get(url).text)
    title = doc.one(".series-name a, .series-name") or doc.one("meta[property='og:title']")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    cover = doc.one("meta[property='og:image']")
    summary = doc.one(".series-summary .summary-content")
    chapters = []
    for link in doc.select("ul.list-chapters a[href]"):
        name = link.one(".chapter-name")
        name = (name.text if name is not None else "") or link.get("title") or link.text
        when = link.one(".chapter-time")
        chapters.append({"url": absolute(BASE, link.get("href")), "name": name, "number": number(name),
                         "date": _date(when.text) if when is not None else ""})
    chapters.reverse()  # (newest first on the page)
    return {"url": url, "title": title.text if title.tag != "meta" else title.get("content"),
            "cover": absolute(BASE, cover.get("content")) if cover is not None else _background(doc.one(".series-cover .img-in-ratio")),
            "summary": summary.text if summary is not None else "", "kind": "webtoon", "chapters": chapters}


def pages(chapter_url):
    doc = parse(_get(chapter_url).text)
    found = []
    for img in doc.select("#chapter-content img"):
        src = picture(img, chapter_url)
        if src and src not in found:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
