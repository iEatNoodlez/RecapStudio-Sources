"""RawDEX: chapters from rawdex.net (raws, mostly Korean manhwa with some Japanese: set to Korean; much of it adult).

A Madara (WordPress) site whose child theme, rawdex-nova, redraws every page, so the Madara engine's selectors don't
fit it. Search is /?s=<word> (paged with &spage=; the engine's post_type=wp-manga finds nothing here); the lists
are the /manga/ archive in the site's views and latest orders and its Top Manhwa page; a series' chapters are all
on its page, newest first; a chapter's pictures are on its page with ?style=list, as on any Madara site.
"""
import re
import time
from urllib.parse import quote_plus

import requests

RECAP_PLUGIN = 1
NAME = "RawDEX"
VERSION = "1.0.0"
SITES = ["rawdex.net"]
LANGUAGE = "ko"
NSFW = True
LISTS = [{"id": "popular", "name": "Most viewed"}, {"id": "latest", "name": "Latest updates"},
         {"id": "week", "name": "Top this week"}]
HEADERS = {"Referer": "https://rawdex.net/"}
DELAY = 0
TEST_QUERY = "love"

# @include dom

BASE = "https://rawdex.net"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
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


def _page_of(href):
    """The page a pagination link goes to (/page/3/ or &spage=3), or 0."""
    m = re.search(r"/page/(\d+)/", href) or re.search(r"[?&]s?page=(\d+)", href)
    return int(m.group(1)) if m else 0


def _results(url, page):
    """Search and list results (the theme's library and home cards): {"items", "more"}."""
    doc = parse(_get(url).text)
    items, seen = [], set()
    for card in doc.select(".rdx-library-card, .rdx-home-card"):
        link = card.one("h2 a") or card.one("a[href*='/manga/']")
        if link is None:
            continue
        href = absolute(BASE, link.get("href")).rstrip("/") + "/"
        if href in seen:
            continue
        seen.add(href)
        img = card.one("img")
        items.append({"url": href, "title": link.text or link.get("title") or (img.get("alt") if img is not None else ""),
                      "cover": picture(img, BASE), "kind": "webtoon"})
    more = any(_page_of(a.get("href")) > page for a in doc.select("a.page-numbers"))
    return {"items": items, "more": bool(items) and more}


def search(query, page=1):
    tail = f"&spage={page}" if page > 1 else ""
    return _results(f"{BASE}/?s={quote_plus(query.strip())}{tail}", page)


def browse(list_id, page=1):
    folder = f"page/{page}/" if page > 1 else ""
    if list_id == "week":
        return _results(f"{BASE}/top-manhwa/week/{folder}", page)
    if list_id in ("popular", "latest"):
        order = "views" if list_id == "popular" else "latest"
        return _results(f"{BASE}/manga/{folder}?m_orderby={order}", page)
    raise ValueError(f"{NAME} has no list {list_id!r}")


def _series_url(url):
    """The series' link from its own or one of its chapters' (/manga/<slug>/<chapter>/)."""
    m = re.match(r"https?://[^/]+/manga/([^/?#]+)", url.strip())
    if not m:
        raise ValueError(f"That isn't a series' link on {NAME}: {url}")
    return f"{BASE}/manga/{m.group(1)}/"


def series(url):
    url = _series_url(url)
    doc = parse(_get(url).text)
    title = doc.one(".rdx-manga-heading h1, h1")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    cover = doc.one("meta[property='og:image']")
    summary = doc.one(".rdx-manga-summary")

    chapters, seen = [], set()
    for row in doc.select("a.rdx-chapter-row"):
        href = absolute(BASE, row.get("href"))
        if not href or href in seen:
            continue
        seen.add(href)
        label = row.one(".rdx-chapter-row__label")
        name = (label.text if label is not None else row.text) or "Chapter"
        when = row.one(".rdx-chapter-row__date")
        chapters.append({"url": href, "name": name, "number": number(name),
                         "date": when.text if when is not None else ""})
    # The page lists them newest first unless the member switched its order: put the first chapter first.
    numbered = [c["number"] for c in chapters if c["number"] is not None]
    if len(numbered) >= 2 and numbered[0] > numbered[-1] or len(numbered) < 2:
        chapters.reverse()
    return {"url": url, "title": title.text,
            "cover": absolute(BASE, cover.get("content")) if cover is not None else "",
            "summary": summary.text if summary is not None else "", "kind": "webtoon", "chapters": chapters}


def pages(chapter_url):
    url = chapter_url.strip() + ("&" if "?" in chapter_url else "?") + "style=list"
    doc = parse(_get(url).text)
    found = []
    for img in doc.select(".reading-content img, .page-break img, img.wp-manga-chapter-img"):
        src = picture(img, chapter_url)
        if src and src not in found:
            found.append(src)
    if not found:
        if doc.one("#chapter-protector-data, [id*=chapter-protector], .chapter-warning"):
            raise RuntimeError(f"{NAME} hides this chapter's pictures behind its own lock, which this plugin can't open")
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
