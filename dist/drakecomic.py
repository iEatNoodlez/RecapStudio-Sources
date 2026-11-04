"""Drake Scans: chapters from drakecomic.net, a site of its own (built on NovelDex, no longer on MangaThemesia).

A Recap Studio source plugin from RecapStudio-Sources. Search and lists are the site's own JSON (/api/series); a
series' chapters and a chapter's pictures are in the data its pages are drawn from (asked for with "RSC: 1", the way
the site's own page changes ask, which leaves out the page's HTML). Chapters still locked behind the site's coins are
left out until they're free.
"""
RECAP_PLUGIN = 1
NAME = "Drake Scans"
VERSION = "1.0.0"
SITES = ["drakecomic.net"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}]
HEADERS = {"Referer": "https://drakecomic.net/"}
DELAY = 0
TEST_QUERY = "necromancer"

import json
import re
import time
from urllib.parse import quote_plus

import requests

BASE = "https://drakecomic.net"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})
KINDS = {"MANHWA": "webtoon", "MANHUA": "webtoon", "WEBTOON": "webtoon", "MANGA": "manga", "COMIC": "comic"}
PER_PAGE = 24


def _get(url, **kw):
    for attempt in range(2):
        r = _session.get(url, timeout=45, **kw)
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


def _props(url, start):
    """The data a page is drawn from: the object that starts with `start` ('{"series":{', say)."""
    text = _get(url, headers={"RSC": "1"}).text
    at = text.find(start)
    if at < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(text, at)[0]
    except ValueError:
        raise RuntimeError(f"{NAME} changed its pages: this plugin can't read them any more")


def _list(params, page):
    found = _get(f"{BASE}/api/series?{params}&page={page}&limit={PER_PAGE}").json()
    items = [{"url": f"{BASE}/series/comic/{x['urlSlug']}", "title": x["title"],
              "cover": BASE + x["coverImage"] if (x.get("coverImage") or "").startswith("/") else x.get("coverImage") or "",
              "kind": KINDS.get(x.get("type"), "")}
             for x in found.get("data") or [] if x.get("urlSlug") and x.get("type") in KINDS]  # (not its novels)
    return {"items": items, "more": bool((found.get("meta") or {}).get("hasMore"))}


def search(query, page=1):
    return _list(f"q={quote_plus(query)}", page)


def browse(list_id, page=1):
    return _list("sort=popular" if list_id == "popular" else "sort=latest", page)


def _series_url(url):
    m = re.match(r"(?:https?://[^/]+)?(/series/comic/[^/?#]+)", url)
    if not m:
        raise ValueError(f"{NAME}: that isn't a link to one of its series ({url})")
    return BASE + m.group(1)


def _number_text(n):
    return str(int(n)) if float(n).is_integer() else str(n)


def series(url):
    url = _series_url(url)
    props = _props(url, '{"series":{')
    if not props:
        raise RuntimeError(f"{NAME} showed no series at {url}")
    info, rows, page = props["series"], list(props.get("chapters") or []), 1
    while page < int(props.get("totalPages") or 1) and page < 100:  # (100 chapters to a page)
        page += 1
        props = _props(f"{url}?page={page}", '{"series":{') or {}
        rows += props.get("chapters") or []
    chapters, seen = [], set()
    for c in rows:
        if c.get("number") is None or c["id"] in seen or (c.get("isLocked") and not c.get("hasAccess")):
            continue  # (bought with the site's coins until it's free)
        seen.add(c["id"])
        num, title = float(c["number"]), (c.get("title") or "").strip()
        if not title or title == _number_text(num):
            name = f"Chapter {_number_text(num)}"
        elif re.match(r"(chapter|ch\.?|episode)\s*\d", title, re.I):
            name = title
        else:
            name = f"Chapter {_number_text(num)} - {title}"
        chapters.append({"url": f"{url}/chapter/{_number_text(num)}", "name": name, "number": num,
                         "date": (c.get("publishedAt") or "")[:10]})
    chapters.sort(key=lambda c: c["number"])
    cover = info.get("coverImage") or ""
    return {"url": url, "title": info.get("title") or "Untitled", "cover": BASE + cover if cover.startswith("/") else cover,
            "summary": (info.get("description") or "").strip(), "kind": KINDS.get(info.get("type"), ""),
            "chapters": chapters}


def pages(chapter_url):
    props = _props(chapter_url, '{"kind":"comic","chapter":{')
    chapter = (props or {}).get("chapter") or {}
    found = []
    for p in chapter.get("pages") or []:
        if p.get("kind", "CONTENT") != "CONTENT":
            continue
        if p.get("isEncrypted") or p.get("tiles") or p.get("strips") or p.get("fragments") or p.get("isRedacted"):
            raise RuntimeError(f"{NAME} scrambles this chapter's pictures (or hides them until it's bought), "
                               "which this plugin can't undo")
        src = p.get("imageUrl") or ""
        if src:
            found.append(BASE + src if src.startswith("/") else src)
    if not found:
        if props is None or chapter.get("isLocked"):
            raise RuntimeError(f"{NAME} keeps this chapter locked (it's bought with the site's coins)")
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
