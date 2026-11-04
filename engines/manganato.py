# engine: Manganato
# version: 1.0.0
# title: the Manganato platform (MangaNato, MangaNelo, MangaKakalot)
# lists: [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}, {"id": "new", "name": "New"}, {"id": "completed", "name": "Completed"}]
#
# Search is the site's quick search (its JSON answer: up to 20 series, no further pages; the full search page is
# behind Cloudflare's browser check). Lists are its manga-list pages. A series' chapters come from the request its
# page makes for them (api/manga/<slug>/chapters), a chapter's pictures from the list in its page's script (the
# picture server plus each picture's path). Pictures need the site as Referer (HEADERS has it).
# A link on one of the site's old domains ("also" in sites.json) is opened on BASE: the paths stay the same.
# OPTIONS (sites.json): none so far.
import html
import json
import re
import time
import unicodedata
from urllib.parse import urlsplit

import requests

# @include dom

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})
XHR = {"X-Requested-With": "XMLHttpRequest"}
LIST_PATH = {"popular": "hot-manga", "latest": "latest-manga", "new": "new-manga", "completed": "completed-manga"}
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


def _json(url, **kw):
    r = _get(url, **kw)
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"{NAME} didn't answer with its usual data ({url})")


def _here(url):
    """A link on one of the site's old domains, on its current one (the paths haven't changed)."""
    parts = urlsplit(url.strip())
    return BASE + parts.path + (f"?{parts.query}" if parts.query else "")


def _alias(query):
    """The search word as the site's own search box writes it: lower case, accents dropped, spaces and punctuation
    as "_"."""
    text = unicodedata.normalize("NFKD", query.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[\s!@%^*()+=<>?/,.:;'\"&#\[\]~$_-]+", "_", text).strip("_")


def search(query, page=1):
    word = _alias(query)
    if page > 1 or not word:  # (the quick search has one page)
        return {"items": [], "more": False}
    found = _json(f"{BASE}/home/search/json", params={"searchword": word}, headers=XHR)
    items = []
    for x in found if isinstance(found, list) else []:
        url = x.get("url") or (f"{BASE}/manga/{x['slug']}" if x.get("slug") else "")
        title = html.unescape(re.sub(r"<[^>]+>", "", x.get("name") or ""))
        if url and title:
            items.append({"url": _here(url), "title": title, "cover": x.get("thumb") or ""})
    return {"items": items, "more": False}


def browse(list_id, page=1):
    doc = parse(_get(f"{BASE}/manga-list/{LIST_PATH[list_id]}", params={"page": page}).text)
    items, seen = [], set()
    for card in doc.select(".list-comic-item-wrap"):
        link = card.one("h3 a") or card.one("a.list-story-item")
        url = absolute(BASE, link.get("href")) if link is not None else ""
        if not url or url in seen:
            continue
        seen.add(url)
        items.append({"url": url, "title": link.text or link.get("title"), "cover": picture(card.one("img"), BASE)})
    last = doc.one("a.page_last")
    m = re.search(r"page=(\d+)", last.get("href")) if last is not None else None
    return {"items": items, "more": bool(items) and m is not None and page < int(m.group(1))}


def series(url):
    url = _here(url)
    doc = parse(_get(url).text)
    title = doc.one("ul.manga-info-text h1") or doc.one("h1")
    genres = {a.get("href").rstrip("/").rsplit("/", 1)[-1] for a in doc.select("li.genres a")}
    kind = "webtoon" if genres & WEBTOON else "manga" if "manga" in genres else ""
    box = doc.one("#contentBox")
    summary = (box.own_text or box.text) if box is not None else ""

    # The chapters: the page fills its list from the site's chapter request after it loads.
    holder = doc.one("#chapter-list-container")
    slug = (holder.get("data-comic-slug") if holder is not None else "") or urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    api = (holder.get("data-api-url") if holder is not None else "") or f"{BASE}/api/manga/__SLUG__/chapters"
    link = (holder.get("data-chapter-url-template") if holder is not None else "") or f"{BASE}/manga/__MANGA__/__CHAPTER__"
    found = _json(api.replace("__SLUG__", slug), params={"limit": -1}, headers={**XHR, "Referer": url})
    if not found.get("success", True):
        raise RuntimeError(f"{NAME} didn't give this series' chapters")
    chapters = []
    for row in reversed((found.get("data") or {}).get("chapters") or []):  # (newest first)
        if not row.get("chapter_slug"):
            continue
        name = (row.get("chapter_name") or "").strip()
        num = number(name)
        chapters.append({"url": link.replace("__MANGA__", slug).replace("__CHAPTER__", row["chapter_slug"]),
                         "name": name, "number": num if num is not None else row.get("chapter_num"),
                         "date": (row.get("updated_at") or "")[:10]})
    return {"url": url, "title": title.text if title is not None else "Untitled",
            "cover": picture(doc.one(".manga-info-pic img"), BASE), "summary": summary, "kind": kind,
            "chapters": chapters}


def _script_list(text, name):
    """A list the page's script sets (var cdns = [...]), or []."""
    m = re.search(rf"var\s+{name}\s*=\s*(\[.*?\])\s*;", text, re.S)
    try:
        return json.loads(m.group(1)) if m else []
    except ValueError:
        return []


def pages(chapter_url):
    url = _here(chapter_url)
    text = _get(url).text
    servers, files = _script_list(text, "cdns"), _script_list(text, "chapterImages")
    if servers and files:
        server = servers[0].strip().rstrip("/")
        found = [f"{server}/{f.strip().lstrip('/')}" for f in files if f.strip()]
    else:  # (an older page: the pictures are in it)
        found = [picture(img, url) for img in parse(text).select(".container-chapter-reader img")]
    found = list(dict.fromkeys(f for f in found if f))
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
