"""Manga18.club: chapters from manga18.club, an adult manhwa site with a layout of its own.

A Recap Studio source plugin from RecapStudio-Sources. Search and the lists are /list-manga (search=, order_by=,
page=), a series' chapters are all on its page (/manhwa/<slug>, newest first; the page only hides the older ones)
and a chapter's pictures are in its page's script as a base64-coded list (slides_p_path). Pictures are on
cdn.manga18.club and need the site as Referer (HEADERS has it).
"""
RECAP_PLUGIN = 1
NAME = "Manga18.club"
VERSION = "1.0.0"
SITES = ["manga18.club"]
LANGUAGE = "en"
NSFW = True
LISTS = [{"id": "popular", "name": "Most viewed"}, {"id": "latest", "name": "Latest updates"}]
HEADERS = {"Referer": "https://manga18.club/"}
DELAY = 0
TEST_QUERY = "love"

import base64
import re
import time
from urllib.parse import parse_qs, urlsplit

import requests

# @include dom

BASE = "https://manga18.club"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
ORDER = {"popular": "views", "latest": "lastest"}  # (the site's own spelling)
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


def _results(doc, page):
    """Search and list results: the page's series cards, and whether the pagination links a later page."""
    items, seen = [], set()
    for card in doc.select(".story_item"):
        link = card.one(".mg_name a") or card.one("a[href*='/manhwa/']")
        if link is None:
            continue
        url = absolute(BASE, link.get("href"))
        if "/manhwa/" not in url or url in seen:
            continue
        seen.add(url)
        items.append({"url": url, "title": link.text or link.get("title"), "cover": picture(card.one("img"), BASE),
                      "kind": "webtoon"})
    # (the pagination links a page as ?page=N on a search, and as /list-manga/N on a sorted list)
    more = False
    for a in doc.select("ul.pagination a[href]"):
        parts = urlsplit(a.get("href"))
        linked = parse_qs(parts.query).get("page", [""])[0] or parts.path.rstrip("/").rsplit("/", 1)[-1]
        if linked.isdigit() and int(linked) > page:
            more = True
    return {"items": items, "more": bool(items) and more}


def search(query, page=1):
    return _results(parse(_get(f"{BASE}/list-manga", params={"search": query, "page": page}).text), page)


def browse(list_id, page=1):
    if list_id not in ORDER:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    return _results(parse(_get(f"{BASE}/list-manga", params={"order_by": ORDER[list_id], "page": page}).text), page)


def _date(text):
    """"08-11-2026" as 2026-11-08 (the text as it is when it's something else, like "2 hours ago")."""
    m = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", text or "")
    return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else (text or "").strip()


def _detail(doc, label):
    """A row of the series' details ("Status", "Categories"...), or ""."""
    for item in doc.select(".detail_listInfo .item"):
        head = item.one(".info_label")
        if head is not None and head.text.strip().lower() == label.lower():
            value = item.one(".info_value")
            return value.text if value is not None else ""
    return ""


def series(url):
    url = BASE + urlsplit(url.strip()).path.rstrip("/")
    doc = parse(_get(url).text)
    title = doc.one(".detail_name h1, h1")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    cover = doc.one(".detail_avatar img")
    summary = doc.one(".detail_reviewContent")
    categories = _detail(doc, "Categories").lower()
    chapters = []
    for row in doc.select(".detail_chapterContent li"):
        link = row.one("a.chapter_num") or row.one("a[href*='/chapter']")
        if link is None:
            continue
        name = link.text.strip()
        infos = row.select("p.chapter_info")
        chapters.append({"url": absolute(BASE, link.get("href")), "name": name, "number": number(name),
                         "date": _date(infos[0].text) if infos else ""})
    chapters.reverse()  # (newest first on the page)
    return {"url": url, "title": title.text, "cover": picture(cover, BASE),
            "summary": summary.text if summary is not None else "",
            "kind": "manga" if "manga" in categories and "manhwa" not in categories else "webtoon",
            "chapters": chapters}


def pages(chapter_url):
    text = _get(chapter_url).text
    m = re.search(r"slides_p_path\s*=\s*(\[[^\]]*\])", text)
    if m is None:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter (its picture list is missing from the page)")
    found = []
    for item in re.findall(r"""["']([A-Za-z0-9+/=_-]+)["']""", m.group(1)):  # (not JSON: a trailing comma)
        try:
            src = base64.b64decode(item + "=" * (-len(item) % 4)).decode("utf-8").strip()
        except (ValueError, UnicodeDecodeError):
            continue
        src = absolute(BASE, src)
        if src.startswith("http") and src not in found:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
