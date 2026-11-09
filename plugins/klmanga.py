"""KLManga: chapters from jestful.net (Japanese raws; the site klmanga.net moved to).

Search and the lists are the site's /manga-list.html page (name=<word>; sort=views or last_update; &page=); a
series' chapters are all on its page, newest first; a chapter's pictures are lazy-loaded <img>s (data-src) in its
reader, on the site's own picture host.
"""
import re
import time
from urllib.parse import urlencode, urlparse

import requests

RECAP_PLUGIN = 1
NAME = "KLManga"
VERSION = "1.0.0"
SITES = ["jestful.net", "klmanga.net"]
LANGUAGE = "ja"
NSFW = False
LISTS = [{"id": "popular", "name": "Most viewed"}, {"id": "latest", "name": "Latest updates"}]
HEADERS = {"Referer": "https://jestful.net/"}
DELAY = 0
TEST_QUERY = "one"

# @include dom

BASE = "https://jestful.net"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
SORT = {"popular": "views", "latest": "last_update"}
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


def _on_site(url):
    """A link on the site as one on jestful.net (old klmanga.net links too)."""
    parts = urlparse(url.strip())
    return BASE + (parts.path or "/")


def _results(values):
    """Search and list results from /manga-list.html: {"items", "more"}."""
    doc = parse(_get(f"{BASE}/manga-list.html?{urlencode(values)}").text)
    items, seen = [], set()
    for card in doc.select("article.manga-update-card"):
        link = card.one(".update-title a") or card.one("a[href*='.html']")
        if link is None:
            continue
        url = absolute(BASE, link.get("href"))
        if url in seen:
            continue
        seen.add(url)
        img = card.one("img.update-cover-img") or card.one("img")
        items.append({"url": url, "title": link.get("title") or link.text or (img.get("alt") if img is not None else ""),
                      "cover": picture(img, BASE), "kind": "manga"})
    more = doc.one(".modern-pagination a[title^=Next]") is not None  # (the "Next Page" button)
    return {"items": items, "more": bool(items) and more}


def search(query, page=1):
    values = {"name": query.strip()}
    if page > 1:
        values["page"] = page
    return _results(values)


def browse(list_id, page=1):
    if list_id not in SORT:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    values = {"sort": SORT[list_id], "sort_type": "DESC"}
    if page > 1:
        values["page"] = page
    return _results(values)


def _series_url(url):
    """The series' link (/hwms-<slug>.html) from its own or one of its chapters' (/bsaq-<slug>-chapter-<n>.html)."""
    url = _on_site(url)
    m = re.match(r"(https://[^/]+/hwms-[^/?#]+\.html)", url)
    if m:
        return m.group(1)
    m = re.match(r"https://[^/]+/bsaq-(.+)-chapter-[\d.]+\.html", url)
    if m:
        return f"{BASE}/hwms-{m.group(1)}.html"
    raise ValueError(f"That isn't a series' link on {NAME}: {url}")


def _meta(doc, label):
    """The value of a "Released: 2022" / "Status: Ongoing" row on a series' page."""
    for row in doc.select(".meta-row"):
        text = row.text
        if text.lower().startswith(label + ":"):
            return text.split(":", 1)[1].strip()
    return ""


def series(url):
    url = _series_url(url)
    doc = parse(_get(url).text)
    title = doc.one("h1.manga-detail-title, h1")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    cover = doc.one("meta[property='og:image']")
    summary = doc.one(".synopsis-text")
    year = _meta(doc, "released")

    chapters, seen = [], set()
    for row in doc.select("a.chapter-row"):
        href = absolute(BASE, row.get("href"))
        if not href or href in seen:
            continue
        seen.add(href)
        label = row.one(".chapter-name-text")
        name = (label.text if label is not None else row.text) or "Chapter"
        when = row.one(".chapter-meta-col span[title], span[title]")
        chapters.append({"url": href, "name": name, "number": number(name),
                         "date": when.get("title")[:10] if when is not None else ""})
    chapters.reverse()  # (newest first on the page)
    return {"url": url, "title": title.text,
            "cover": absolute(BASE, cover.get("content")) if cover is not None else "",
            "summary": summary.text if summary is not None else "", "kind": "manga",
            "year": int(year) if year.isdigit() else None, "chapters": chapters}


def pages(chapter_url):
    doc = parse(_get(_on_site(chapter_url)).text)
    found = []
    for img in doc.select(".reader-image-wrap img, img.reader-img"):
        src = picture(img, BASE)
        if src and "favicon" not in src and src not in found:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
