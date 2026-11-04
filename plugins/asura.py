"""Asura Scans: chapters from asurascans.com (old asuracomic.net links open too), from the site's own JSON API.

A Recap Studio source plugin from RecapStudio-Sources (plugins/asura.py, written for this site). Its new chapters are
early access for their first hours (they cost coins): those are left out of the chapter list until they're free.
"""
import re
import time
from datetime import datetime, timezone
from html import unescape
from urllib.parse import urljoin, urlparse

import requests

RECAP_PLUGIN = 1
NAME = "Asura Scans"
VERSION = "1.0.0"
SITES = ["asurascans.com", "asuracomic.net"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}]
HEADERS = {"Referer": "https://asurascans.com/"}
DELAY = 0
TEST_QUERY = "solo leveling"

BASE = "https://asurascans.com"
API = "https://api.asurascans.com/api"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
PER_PAGE = 20
SORT = {"popular": "popular", "latest": "latest"}
KINDS = {"manhwa": "webtoon", "manhua": "webtoon", "manga": "manga", "comic": "comic"}
SKIP = re.compile(r"ENDING-PAGE|EndDesignPSD|panda_gif_large", re.I)  # (the group's credit pages, not the story)
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/", "Origin": BASE,
                         "Accept": "application/json"})


def _get(url, link="", **kw):
    """The API's answer (JSON) for one of its addresses; link is the member's own link, for the errors."""
    for attempt in range(2):
        r = _session.get(url, timeout=45, **kw)
        if r.status_code == 429 and attempt == 0:
            wait = r.headers.get("Retry-After", "")
            time.sleep(min(30, int(wait) if wait.isdigit() else 10))
            continue
        if r.status_code in (403, 503) and ("cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        if r.status_code == 404:
            raise ValueError(f"{NAME} has no such series or chapter: {link or url}")
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            raise RuntimeError(f"{NAME} didn't answer with its usual data (the site may have changed): {link or url}")
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _when(text):
    """An API time ("2026-11-04T04:12:13.44Z") as a datetime in UTC, or None."""
    try:
        when = datetime.fromisoformat(text) if text else None
    except ValueError:
        return None
    return when.replace(tzinfo=timezone.utc) if when and when.tzinfo is None else when


def _plain(text):
    """A description's HTML as plain text."""
    text = re.sub(r"<br\s*/?>|</p>", "\n", text or "", flags=re.I)
    return re.sub(r"[ \t]+", " ", unescape(re.sub(r"<[^>]+>", "", text))).strip()


def _num(n):
    """A chapter's number as the site writes it in links: 12, 12.5."""
    return str(int(n)) if float(n).is_integer() else str(n)


def _item(x):
    """A series in the API's lists, as a search or list result."""
    path = x.get("public_url") or f"/comics/{x['slug']}"
    return {"url": urljoin(BASE, path), "title": x.get("title") or x["slug"],
            "cover": x.get("cover") or x.get("cover_url") or "", "summary": _plain(x.get("description")),
            "kind": KINDS.get(x.get("type"), "webtoon"), "year": x.get("release_year")}


def _results(found, page):
    items = [_item(x) for x in found.get("data") or [] if x.get("slug")]
    meta = found.get("meta") or {}
    more = meta["has_more"] if "has_more" in meta else page * PER_PAGE < (meta.get("total") or 0)
    return {"items": items, "more": bool(items) and bool(more)}


def search(query, page=1):
    params = {"q": query, "limit": PER_PAGE, "offset": (page - 1) * PER_PAGE}
    return _results(_get(f"{API}/search", params=params), page)


def browse(list_id, page=1):
    if list_id not in SORT:
        raise ValueError(f"{NAME} has no list called {list_id!r}")
    params = {"sort": SORT[list_id], "order": "desc", "limit": PER_PAGE, "offset": (page - 1) * PER_PAGE}
    return _results(_get(f"{API}/series", params=params), page)


def _parts(url):
    """A link's folders: asurascans.com/comics/<slug>/chapter/<n> (asuracomic.net's were /series/<slug>/...)."""
    parts = urlparse(url).path.strip("/").split("/")
    if len(parts) < 2 or parts[0] not in ("comics", "series"):
        raise ValueError(f"That link isn't a series on {NAME} (those look like {BASE}/comics/<name>): {url}")
    return parts


def _locked(c):
    """Still early access: it costs coins until early_access_until."""
    until = _when(c.get("early_access_until"))
    return bool(c.get("is_premium")) or (until is not None and until > datetime.now(timezone.utc))


def series(url):
    slug = _parts(url)[1]  # (an old link's slug ends with an older code: the API still takes it)
    info = _get(f"{API}/series/{slug}", link=url).get("series") or {}
    if not info.get("slug"):
        raise ValueError(f"{NAME} has no such series: {url}")
    page = urljoin(BASE, info.get("public_url") or f"/comics/{info['slug']}")
    rows = _get(f"{API}/series/{info['slug']}/chapters", link=url).get("data") or []
    chapters = []
    for c in sorted(rows, key=lambda c: (c.get("number") or 0, c.get("published_at") or "")):
        if c.get("number") is None or _locked(c):
            continue
        title = (c.get("title") or "").strip()
        name = f"Chapter {_num(c['number'])}"
        if title and not re.match(r"(?i)chapter\s*\d", title):
            name += f" - {title}"
        chapters.append({"url": f"{page}/chapter/{_num(c['number'])}", "name": name, "number": float(c["number"]),
                         "date": (c.get("published_at") or "")[:10]})
    return {"url": page, "title": info.get("title") or info["slug"], "cover": info.get("cover") or "",
            "summary": _plain(info.get("description")), "kind": KINDS.get(info.get("type"), "webtoon"),
            "year": info.get("release_year"), "chapters": chapters}


def pages(chapter_url):
    parts = _parts(chapter_url)
    if len(parts) < 4 or parts[2] != "chapter":
        raise ValueError(f"That link isn't a chapter on {NAME}: {chapter_url}")
    found = _get(f"{API}/series/{parts[1]}/chapters/{parts[3]}", link=chapter_url).get("data") or {}
    title = (found.get("series") or {}).get("title") or "this series"
    if found.get("access_gate"):
        raise RuntimeError(f"{NAME} is holding its chapters back from this PC for now (its \"account under review\" "
                           "check, after unusual activity): wait a few hours and try again")
    if found.get("is_locked"):
        free = _when(found.get("unlock_time"))
        later = f" until {free.day} {free:%b %Y, %H:%M} UTC, when it's free" if free else ""
        raise RuntimeError(f"Chapter {parts[3]} of {title} is locked on {NAME}: it costs coins{later}")
    urls = [urljoin(BASE, p["url"]) for p in (found.get("chapter") or {}).get("pages") or []
            if p.get("url") and not SKIP.search(p["url"])]
    if not urls:
        raise RuntimeError(f"{NAME} showed no pictures for chapter {parts[3]} of {title}")
    return urls
