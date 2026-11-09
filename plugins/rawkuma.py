"""Rawkuma: chapters from rawkuma.net (Japanese raws).

A WordPress site on its own theme (not MangaThemesia: /?s= is a 404). Search and the lists are the Library page's
own request, a POST to admin-ajax.php?action=advanced_search with a nonce the site hands out at
admin-ajax.php?action=get_nonce (24 a page, with the site's popular, updated and rating orders); a series' chapters
are all on its page, newest first; a chapter's pictures are plain <img>s on its page.
"""
import re
import time

import requests

RECAP_PLUGIN = 1
NAME = "Rawkuma"
VERSION = "1.0.0"
SITES = ["rawkuma.net", "rawkuma.com"]
LANGUAGE = "ja"
NSFW = False
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest updates"},
         {"id": "rating", "name": "Top rated"}]
HEADERS = {"Referer": "https://rawkuma.net/"}
DELAY = 0
TEST_QUERY = "one piece"

# @include dom

BASE = "https://rawkuma.net"
AJAX = BASE + "/wp-admin/admin-ajax.php"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
PER_PAGE = 24
ORDER = {"popular": "popular", "latest": "updated", "rating": "rating"}
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})
_nonce = None


def _request(method, url, **kw):
    for attempt in range(2):
        r = _session.request(method, url, timeout=45, **kw)
        if r.status_code == 429 and attempt == 0:
            time.sleep(min(30, int(r.headers.get("Retry-After") or 10)))
            continue
        if r.status_code in (403, 503) and ("cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        if r.status_code == 404:
            raise ValueError(f"{NAME} has no such page: {url}")
        return r
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _get(url):
    r = _request("GET", url)
    r.raise_for_status()
    return r


def _get_nonce():
    """The search nonce the site gives every visitor (its Library page asks for it the same way)."""
    doc = parse(_request("GET", f"{AJAX}?type=search_form&action=get_nonce",
                         headers={"HX-Request": "true", "Referer": BASE + "/library/"}).text)
    field = doc.one("input[name=search_nonce]")
    if field is None or not field.get("value"):
        raise RuntimeError(f"{NAME} didn't hand out its search nonce: its search may have changed")
    return field.get("value")


def _library(query, orderby, page):
    """The Library's results for a search term and order: {"items", "more"}."""
    global _nonce
    for attempt in range(2):
        if _nonce is None:
            _nonce = _get_nonce()
        data = {"nonce": _nonce, "query": query, "orderby": orderby, "order": "desc", "page": page,
                "genre": "[]", "genre_exclude": "[]", "author": "[]", "artist": "[]", "type": "[]", "status": "[]",
                "inclusion": "OR", "exclusion": "OR", "project": "0"}
        r = _request("POST", f"{AJAX}?action=advanced_search", data=data, headers={"Referer": BASE + "/library/"})
        if r.status_code == 403 and attempt == 0:  # (the nonce ran out: get a new one)
            _nonce = None
            continue
        r.raise_for_status()
        break
    items, seen = [], set()
    for heading in parse(r.text).select("a[href*='/manga/'] > h1, a[href*='/manga/'] > h2"):
        link = heading.parent
        url = absolute(BASE, link.get("href"))
        if not url or url in seen:
            continue
        seen.add(url)
        card = link
        while card.parent is not None and card.one("img") is None:
            card = card.parent
        cover = next((img for img in card.select("img") if "/themes/" not in img.get("src")), None)
        items.append({"url": url, "title": heading.text or (cover.get("alt") if cover is not None else ""),
                      "cover": picture(cover, BASE), "kind": "manga"})
    return {"items": items, "more": len(items) >= PER_PAGE}


def search(query, page=1):
    return _library(query.strip(), "popular", page)


def browse(list_id, page=1):
    if list_id not in ORDER:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    return _library("", ORDER[list_id], page)


def _series_url(url):
    """The series' link (/manga/<slug>/) from its own or one of its chapters' (/manga/<slug>/chapter-<n>.<id>/)."""
    m = re.match(r"https?://[^/]+/manga/([^/?#]+)", url.strip())
    if not m:
        raise ValueError(f"That isn't a series' link on {NAME}: {url}")
    return f"{BASE}/manga/{m.group(1)}/"


def series(url):
    url = _series_url(url)
    doc = parse(_get(url).text)
    title = doc.one("h1[itemprop=name]")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    summary = next((n.text for n in doc.select("[itemprop=description]") if n.text), "")
    cover = doc.one("[itemprop=image]")
    cover = (cover.get("content") or cover.get("src") or picture(cover.one("img"), BASE)) if cover is not None else ""
    if not cover:
        cover = picture(doc.one("img.wp-post-image"), BASE)

    rows = doc.select("#chapter-list a[href*='/chapter-']") or doc.select(f"a[href*='{url}chapter-']")
    chapters, seen = [], set()
    for row in rows:
        href = absolute(BASE, row.get("href"))
        if not href or href in seen:
            continue
        seen.add(href)
        label = row.one(".font-medium span, span")
        name = (label.text if label is not None and label.text else row.text) or "Chapter"
        when = row.one("time")
        chapters.append({"url": href, "name": name, "number": number(name),
                         "date": (when.get("datetime") or when.text)[:10] if when is not None else ""})
    chapters.reverse()  # (newest first on the page)
    return {"url": url, "title": title.text, "cover": absolute(BASE, cover), "summary": summary, "kind": "manga",
            "chapters": chapters}


def pages(chapter_url):
    doc = parse(_get(chapter_url.strip()).text)
    found = []
    imgs = doc.select("section[data-image-data] img")
    if not imgs:  # (the reader's section lost its mark: every picture that isn't the site's own)
        imgs = [img for img in (doc.one("main") or doc).select("img")
                if not any(s in img.get("src") for s in ("/wp-content/", "/themes/", "gravatar"))]
    for img in imgs:
        src = picture(img, BASE)
        if src and src not in found:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
