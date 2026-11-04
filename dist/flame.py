"""Flame Comics: chapters from flamecomics.xyz, from the data its pages carry (a Next.js site).

A Recap Studio source plugin from RecapStudio-Sources (plugins/flame.py, written for this site). The site's search is
its browse page's filter on the list of all its series, so search here does the same with that list. Its novels are
left out: they have no pictures.
"""
import json
import re
import time
from datetime import datetime, timezone
from html import unescape
from urllib.parse import quote, urlparse

import requests

RECAP_PLUGIN = 1
NAME = "Flame Comics"
VERSION = "1.0.0"
SITES = ["flamecomics.xyz", "flamecomics.com"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}]
HEADERS = {"Referer": "https://flamecomics.xyz/"}
DELAY = 0
TEST_QUERY = "omniscient"

BASE = "https://flamecomics.xyz"
CDN = "https://cdn.flamecomics.xyz/uploads/images/series"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
PER_PAGE = 30
KEEP = 600  # seconds the site's list of series is kept before it's asked for again
KINDS = {"manhwa": "webtoon", "manhua": "webtoon", "manga": "manga", "comic": "comic"}
SKIP = re.compile(r"read[\s_.-]*on[\s_.-]*flame|chevron\.png", re.I)  # (its "read on Flame" banners, not the story)
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})
_build = ""  # the site's current build: a page's data is then at /_next/data/<build>/<page>.json, without the page
_kept = {}   # the browse and latest lists: {path: (when, series)}


def _get(url, missing_ok=False):
    for attempt in range(2):
        r = _session.get(url, timeout=45)
        if r.status_code == 429 and attempt == 0:
            wait = r.headers.get("Retry-After", "")
            time.sleep(min(30, int(wait) if wait.isdigit() else 10))
            continue
        if r.status_code in (403, 503) and ("cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        if r.status_code == 404:
            if missing_ok:
                return None
            raise ValueError(f"{NAME} has no such page: {url}")
        r.raise_for_status()
        return r
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _props(path):
    """A page's data (Next.js's pageProps): as JSON alone once the site's build is known, else from the page."""
    global _build
    if _build:
        r = _get(f"{BASE}/_next/data/{_build}{path}.json", missing_ok=True)
        if r is not None and "json" in r.headers.get("Content-Type", ""):
            return r.json().get("pageProps") or {}
        # (a 404: the site has a new build, or there's no such page; the page itself says which)
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', _get(BASE + path).text, re.S)
    if not m:
        raise RuntimeError(f"{NAME}'s page didn't carry its usual data (the site may have changed): {BASE}{path}")
    data = json.loads(m.group(1))
    _build = data.get("buildId") or ""
    return (data.get("props") or {}).get("pageProps") or {}


def _list(path, key):
    """All the site's comics, from its browse or latest page (kept a few minutes): novels left out."""
    when, found = _kept.get(path, (0, None))
    if found is None or time.time() - when > KEEP:
        found = [x for x in _props(path).get(key) or [] if x.get("series_id")]
        _kept[path] = (time.time(), found)
    return found


def _words(text):
    """A title's words, for matching: lower case, apostrophes dropped ("Reader's" → "readers")."""
    return re.sub(r"[^a-z0-9]+", " ", re.sub(r"['’]", "", (text or "").lower())).split()


def _plain(text):
    """A description's HTML as plain text."""
    text = re.sub(r"<br\s*/?>|</p>", "\n", text or "", flags=re.I)
    return re.sub(r"[ \t]+", " ", unescape(re.sub(r"<[^>]+>", "", text))).strip()


def _float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _day(stamp):
    """A Unix time as its day ("2026-11-03"), or ""."""
    return datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d") if stamp else ""


def _cover(x):
    return f"{CDN}/{x['series_id']}/{quote(x['cover'])}?{x.get('last_edit') or ''}" if x.get("cover") else ""


def _item(x):
    return {"url": f"{BASE}/series/{x['series_id']}", "title": unescape(x.get("title") or ""), "cover": _cover(x),
            "summary": _plain(x.get("description")), "kind": KINDS.get((x.get("type") or "").lower(), ""),
            "year": x.get("year")}


def _page(found, page):
    start = (page - 1) * PER_PAGE
    return {"items": [_item(x) for x in found[start:start + PER_PAGE]], "more": len(found) > start + PER_PAGE}


def search(query, page=1):
    want = _words(query)
    found = [x for x in _list("/browse", "series") if all(w in " ".join(_words(x.get("title"))) for w in want)]
    return _page(sorted(found, key=lambda x: x.get("popularityRank") or 9999), page)


def browse(list_id, page=1):
    if list_id == "popular":
        found = sorted(_list("/browse", "series"), key=lambda x: x.get("popularityRank") or 9999)
    elif list_id == "latest":
        found = _list("/latest", "allSeries")  # (the series with the newest chapter first)
    else:
        raise ValueError(f"{NAME} has no list called {list_id!r}")
    return _page(found, page)


def _series_id(url):
    """The series' number in a link (/series/<n>, or a chapter's /series/<n>/<token>). An old flamecomics.com link
    has its name instead (/series/<name>/), looked up in the site's list."""
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "series":
        if parts[1].isdigit():
            return parts[1]
        slug = re.sub(r"^\d+-", "", parts[1].lower())
        for x in _list("/browse", "series"):
            if "-".join(_words(x.get("title"))) == slug:
                return str(x["series_id"])
        raise ValueError(f"{NAME} has no series called {slug!r} any more: search for it by its title")
    raise ValueError(f"That link isn't a series on {NAME} (those look like {BASE}/series/<number>): {url}")


def series(url):
    sid = _series_id(url)
    found = _props(f"/series/{sid}")
    info = found.get("series") or {}
    if not info:
        raise ValueError(f"{NAME} has no such series: {url}")
    chapters = []
    for c in sorted(found.get("chapters") or [], key=lambda c: _float(c.get("chapter")) or 0):
        n = _float(c.get("chapter"))
        if not c.get("token"):
            continue
        title = unescape(c.get("title") or "").strip()
        name = f"Chapter {n:g}" if n is not None else "Chapter"
        if title and not re.match(r"(?i)chapter\s*\d", title):
            name += f" - {title}"
        chapters.append({"url": f"{BASE}/series/{sid}/{c['token']}", "name": name, "number": n,
                         "date": _day(c.get("release_date"))})
    return {"url": f"{BASE}/series/{sid}", "title": unescape(info.get("title") or "Untitled"),
            "cover": _cover({**info, "series_id": sid}), "summary": _plain(info.get("description")),
            "kind": KINDS.get((info.get("type") or "").lower(), ""), "year": info.get("year"), "chapters": chapters}


def pages(chapter_url):
    parts = urlparse(chapter_url).path.strip("/").split("/")
    if len(parts) < 3 or parts[0] != "series" or not parts[1].isdigit():
        raise ValueError(f"That link isn't a chapter on {NAME} (those look like {BASE}/series/<number>/<code>): "
                         f"{chapter_url}")
    chapter = _props(f"/series/{parts[1]}/{parts[2]}").get("chapter") or {}
    images = chapter.get("images") or {}
    if isinstance(images, dict):  # ({"0": {...}, "1": {...}}: in reading order by its numbers)
        images = [images[k] for k in sorted(images, key=lambda k: int(k) if str(k).isdigit() else 0)]
    folder = f"{CDN}/{chapter.get('series_id') or parts[1]}/{chapter.get('token') or parts[2]}"
    stamp = chapter.get("edit_time") or chapter.get("unix_timestamp") or ""
    urls = [f"{folder}/{quote(x['name'])}?{stamp}" for x in images
            if isinstance(x, dict) and x.get("name") and not SKIP.search(x["name"])]
    if not urls:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return urls
