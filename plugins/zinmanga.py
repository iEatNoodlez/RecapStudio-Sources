"""ZinManga: chapters from zinmanga.net, a site that looks like Madara but loads its chapters from its own comics API.

A Recap Studio source plugin from RecapStudio-Sources. Search and the lists are the site's pages (Madara's layout),
a series' chapters come from the request its page makes for them (api/comics/<slug>/chapters, 100 at a time) and a
chapter's pictures are in its page. Pictures need the site as Referer (HEADERS has it).
"""
RECAP_PLUGIN = 1
NAME = "ZinManga"
VERSION = "1.0.0"
SITES = ["zinmanga.net"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"},
         {"id": "trending", "name": "Trending"}]
HEADERS = {"Referer": "https://www.zinmanga.net/"}
DELAY = 0
TEST_QUERY = "solo"

import re
import time
from urllib.parse import urlsplit

import requests

# @include dom

BASE = "https://www.zinmanga.net"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})
ORDER = {"popular": "views", "latest": "latest", "trending": "trending"}
WEBTOON = {"manhwa", "manhua", "webtoons", "long-strip"}  # (genres that mean a vertical comic)


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


def _cards(doc, card_css):
    """Search and list results: [{"url", "title", "cover"}]."""
    out, seen = [], set()
    for card in doc.select(card_css):
        link = card.one(".post-title a, h3 a")
        url = absolute(BASE, link.get("href")) if link is not None else ""
        if not url or url in seen or "/manga/" not in url:
            continue
        seen.add(url)
        out.append({"url": url, "title": link.text, "cover": picture(card.one("img"), BASE)})
    return out


def _more(doc):
    return doc.one("a[aria-label=Next], a.nextpostslink, link[rel=next]") is not None


def search(query, page=1):
    folder = f"/page/{page}" if page > 1 else "/"
    doc = parse(_get(BASE + folder, params={"s": query, "post_type": "wp-manga"}).text)
    items = _cards(doc, ".c-tabs-item__content")
    return {"items": items, "more": bool(items) and _more(doc)}


def browse(list_id, page=1):
    folder = f"/page/{page}" if page > 1 else ""
    doc = parse(_get(f"{BASE}/manga{folder}", params={"orderby": ORDER[list_id]}).text)
    items = _cards(doc, ".page-content-listing .page-item-detail")
    return {"items": items, "more": bool(items) and _more(doc)}


def _summary(doc):
    """The story's summary: the "Main Plot" part of the site's description (the rest is the site's own blurb)."""
    box = doc.one(".description-summary .summary__content, .summary__content")
    if box is None:
        return ""
    after = False
    for child in box.children:
        if isinstance(child, str):
            continue
        if child.tag == "h3" and "plot" in child.text.lower():
            after = True
        elif after and child.tag == "p" and child.text:
            return child.text
    return box.text


def series(url):
    url = BASE + urlsplit(url.strip()).path  # (any of the site's hosts: the paths are the same)
    page = _get(url).text
    doc = parse(page)
    title = doc.one(".post-title h1, h1")
    genres = {a.get("href").rstrip("/").rsplit("/", 1)[-1] for a in doc.select(".genres-content a")}
    kind = "webtoon" if genres & WEBTOON else "manga" if "manga" in genres else ""
    year = doc.one("a[href*=manga-release]")

    # The chapters: the page asks for them after it loads, oldest first here, 100 at a time.
    m = re.search(r"getChaptersUrl\s*=\s*['\"]([^'\"]+)", page)
    m_slug = re.search(r"comicSlug\s*=\s*['\"]([^'\"]+)", page)
    slug = m_slug.group(1) if m_slug else url.rstrip("/").rsplit("/", 1)[-1]
    api = m.group(1) if m else f"{BASE}/api/comics/{slug}/chapters"
    chapters, at = [], 1
    while at <= 100:  # (10,000 chapters at most)
        r = _get(api, params={"page": at, "per_page": 100, "order": "asc"},
                 headers={"X-Requested-With": "XMLHttpRequest", "Referer": url})
        try:
            data = r.json().get("data") or {}
        except ValueError:
            raise RuntimeError(f"{NAME} didn't answer with its usual chapter list")
        for row in data.get("chapters") or []:
            if not row.get("chapter_slug"):
                continue
            name = (row.get("chapter_name") or "").strip()
            num = number(name)
            chapters.append({"url": f"{BASE}/manga/{slug}/{row['chapter_slug']}", "name": name,
                             "number": num if num is not None else row.get("chapter_num"),
                             "date": (row.get("updated_at") or "")[:10]})
        if not data.get("chapters") or int(data.get("current_page") or at) >= int(data.get("last_page") or at):
            break
        at += 1
    return {"url": url, "title": title.text if title is not None else "Untitled",
            "cover": picture(doc.one(".summary_image img"), BASE), "summary": _summary(doc), "kind": kind,
            "year": year.text if year is not None else None, "chapters": chapters}


def pages(chapter_url):
    doc = parse(_get(chapter_url).text)
    found = []
    for img in doc.select(".reading-content img, .page-break img"):
        src = picture(img, chapter_url)
        if src and src not in found:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
