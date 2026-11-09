"""Hachiraw: chapters from hachiraw.win (Japanese raws; it calls itself adult and has adult categories).

Search is the site's /search?query= page, which shows its first 20 matches only (no page of it goes further, so the
search offers no more); the lists are its front page of latest updates (/page/<n>/) and two of its genre categories;
a series' chapters are all on its page, newest first, without dates; a chapter's pictures are lazy-loaded <img>s
(data-src) on its page, on a third-party host. The site tells browsers to send no Referer, so none is set for its
pictures.
"""
import re
import time
from urllib.parse import urlencode

import requests

RECAP_PLUGIN = 1
NAME = "Hachiraw"
VERSION = "1.0.0"
SITES = ["hachiraw.win"]
LANGUAGE = "ja"
NSFW = True
LISTS = [{"id": "latest", "name": "Latest updates"}, {"id": "action", "name": "Action"},
         {"id": "fantasy", "name": "Fantasy"}]
HEADERS = {}
DELAY = 0
TEST_QUERY = "one"

# @include dom

BASE = "https://hachiraw.win"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
CATEGORIES = {"action": "6", "fantasy": "12"}
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


def _cards(doc):
    items, seen = [], set()
    for card in doc.select("article.post"):
        link = card.one(".entry-title a") or card.one("a[href*='/manga-']")
        if link is None:
            continue
        url = absolute(BASE, link.get("href"))
        if url in seen:
            continue
        seen.add(url)
        img = card.one(".featured-thumb img") or card.one("img")
        items.append({"url": url, "title": link.text or (img.get("alt") if img is not None else ""),
                      "cover": picture(img, BASE), "kind": "manga"})
    return items


def search(query, page=1):
    if page > 1:  # (the site's search has no second page: every page parameter gives the same first 20)
        return {"items": [], "more": False}
    items = _cards(parse(_get(f"{BASE}/search?{urlencode({'query': query.strip()})}").text))
    return {"items": items, "more": False}


def browse(list_id, page=1):
    if list_id == "latest":
        folder = ""
    elif list_id in CATEGORIES:
        folder = f"/category/{CATEGORIES[list_id]}"
    else:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    doc = parse(_get(f"{BASE}{folder}/" + (f"page/{page}/" if page > 1 else "")).text)
    items = _cards(doc)
    return {"items": items, "more": bool(items) and doc.one(".nav-links a.next") is not None}


def _series_url(url):
    """The series' link (/manga-<slug>/), from its own or one of its chapters' (/chapter/<series>/<chapter>/)."""
    url = url.strip()
    m = re.match(r"https?://[^/]+/(manga-[^/?#]+)", url)
    if m:
        return f"{BASE}/{m.group(1)}/"
    if "/chapter/" in url:  # (a chapter's page links to its series)
        for a in parse(_get(url).text).select("a[href*='/manga-']"):
            m = re.match(r"(?:https?://[^/]+)?/(manga-[^/?#]+)", a.get("href"))
            if m:
                return f"{BASE}/{m.group(1)}/"
    raise ValueError(f"That isn't a series' link on {NAME}: {url}")


def series(url):
    url = _series_url(url)
    doc = parse(_get(url).text)
    title = doc.one("h1.entry-title")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    content = doc.one(".entry-content")
    summary = []
    if content is not None:  # (the author and category lines, then the summary's paragraphs, then the chapter table)
        for p in content.select("p"):
            text = p.text
            if text and not text.lower().startswith(("author:", "category:", "categor")) and p.one("a[href*='/chapter/']") is None:
                summary.append(text)

    chapters, seen = [], set()
    for a in doc.select(".chaplist a[href*='/chapter/']"):
        href = absolute(BASE, a.get("href"))
        if href in seen:
            continue
        seen.add(href)
        name = a.text or "Chapter"
        chapters.append({"url": href, "name": name, "number": number(name), "date": ""})
    chapters.reverse()  # (newest first on the page)
    return {"url": url, "title": title.text,
            "cover": picture(content.one(".wp-block-image img, img") if content is not None else None, BASE),
            "summary": " ".join(summary), "kind": "manga", "chapters": chapters}


def pages(chapter_url):
    doc = parse(_get(chapter_url.strip()).text)
    content = doc.one(".entry-content") or doc
    found = []
    for img in content.select(".wp-block-image img, img.aligncenter"):
        src = picture(img, BASE)
        if src and "/themes/" not in src and src not in found:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
