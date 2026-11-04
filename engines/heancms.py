# engine: HeanCMS
# version: 1.0.0
# title: HeanCMS (a comic site system with its own JSON API, at api.<site>)
# lists: [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}]
#
# Everything comes from the site's API: search and the Popular and Latest lists (/query), a series' details
# (/series/<slug>) and its chapters (/chapter/query), and a chapter's pictures (/chapter/<series>/<chapter>).
# Chapters the site still sells for its coins are left out until they're free (they then show up as new chapters).
# Novels (text, no pictures) are left out of search and lists.
# OPTIONS (sites.json): "api", the API's address when it isn't api.<site>; "kind", what kind of comic it has
# ("webtoon" for manhwa sites).
import time
from urllib.parse import quote, unquote, urlparse

import requests

# @include dom

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
API = (OPTIONS.get("api") or BASE.replace("://www.", "://").replace("://", "://api.", 1)).rstrip("/")
KIND = OPTIONS.get("kind", "")
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/", "Origin": BASE,
                         "Accept": "application/json, text/plain, */*"})
ORDER = {"popular": "total_views", "latest": "latest"}
PER_PAGE = 24


def _get(path, **params):
    """The API's answer (JSON) at API/<path>."""
    url = f"{API}/{path}"
    for attempt in range(2):
        r = _session.get(url, params=params, timeout=45)
        if r.status_code == 429 and attempt == 0:
            wait = r.headers.get("Retry-After") or "10"
            time.sleep(min(30, int(wait) if wait.isdigit() else 10))
            continue
        if r.status_code in (403, 503) and ("cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        if r.status_code == 404:
            raise ValueError(f"{NAME} has no such page: {r.url}")
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            raise RuntimeError(f"{NAME}'s API gave something other than its usual answer ({r.url}): "
                               "the site may have changed or be down") from None
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _media(link):
    """A picture's full link (older versions of the API give ones relative to its own server)."""
    link = (link or "").strip()
    return link if not link or link.startswith(("http://", "https://")) else f"{API}/{link.lstrip('/')}"


def _plain(text):
    """A description as plain text (some are HTML)."""
    return parse(text).text if text and "<" in text else (text or "").strip()


def _novel(x):
    return str(x.get("series_type") or x.get("chapter_type") or "").lower() == "novel"


def _slugs(url):
    """The series' (and chapter's) slugs in a link: .../series/<series>[/<chapter>]."""
    parts = [unquote(p) for p in urlparse(url).path.split("/") if p]
    if "series" in parts:
        parts = parts[parts.index("series") + 1:]
    if not parts:
        raise ValueError(f"{NAME}'s series links look like {BASE}/series/<name>, and {url} isn't one")
    return parts


def _list(page, query="", order="total_views"):
    """One page of the site's series (/query), as search and list results."""
    data = _get("query", query_string=query, series_status="All", order="desc", orderBy=order, series_type="Comic",
                page=page, perPage=PER_PAGE, tags_ids="[]")
    items = [{"url": f"{BASE}/series/{s['series_slug']}", "title": s.get("title") or s["series_slug"],
              "cover": _media(s.get("thumbnail")), "summary": _plain(s.get("description")), "kind": KIND}
             for s in data.get("data") or [] if s.get("series_slug") and not _novel(s)]
    meta = data.get("meta") or {}
    return {"items": items, "more": (meta.get("current_page") or page) < (meta.get("last_page") or 0)}


def search(query, page=1):
    return _list(page, query=query)


def browse(list_id, page=1):
    return _list(page, order=ORDER[list_id])


def series(url):
    slug = _slugs(url)[0]
    try:
        s = _get(f"series/{quote(slug, safe='')}")
    except ValueError:
        raise ValueError(f"{NAME} has no series at {url}") from None
    if _novel(s):
        raise RuntimeError(f"{NAME}: {s.get('title') or slug} is a novel (text, no pictures), which can't be recapped")
    chapters, page = [], 1
    while True:
        data = _get("chapter/query", series_id=s["id"], perPage=1000, page=page)
        for c in data.get("data") or []:
            if c.get("price") or not c.get("chapter_slug"):  # (still sold for the site's coins)
                continue
            name = c.get("chapter_name") or c["chapter_slug"]
            title = (c.get("chapter_title") or "").strip()
            folder = (c.get("series") or {}).get("series_slug") or s.get("series_slug") or slug
            chapters.append({"url": f"{BASE}/series/{folder}/{c['chapter_slug']}",
                             "name": f"{name} - {title}" if title and title not in name else name,
                             "number": number(name), "date": (c.get("created_at") or "")[:10]})
        meta = data.get("meta") or {}
        if not data.get("data") or page >= (meta.get("last_page") or 1):
            break
        page += 1
    year = str(s.get("release_year") or "")
    return {"url": f"{BASE}/series/{s.get('series_slug') or slug}", "title": s.get("title") or slug,
            "cover": _media(s.get("thumbnail")), "summary": _plain(s.get("description")), "kind": KIND,
            "year": int(year) if year.isdigit() else None,
            "chapters": list(reversed(chapters))}  # (newest first in the API)


def pages(chapter_url):
    parts = _slugs(chapter_url)
    if len(parts) < 2:
        raise ValueError(f"{NAME}'s chapter links look like {BASE}/series/<series>/<chapter>, and {chapter_url} isn't one")
    data = _get(f"chapter/{quote(parts[0], safe='')}/{quote(parts[1], safe='')}")
    if data.get("paywall"):
        raise RuntimeError(f"{NAME} still sells this chapter for its coins: it can be got here once the site makes it free")
    chapter = data.get("chapter") or {}
    if _novel(chapter):
        raise RuntimeError(f"{NAME}: this chapter is text (a novel), not pictures")
    if isinstance(data.get("data"), list):  # (older versions of the API)
        found = data["data"]
    else:
        files = chapter.get("chapter_data") or {}
        found = files.get("images") or [f.get("url") for f in files.get("files") or [] if isinstance(f, dict)]
    out = []
    for link in found or []:
        link = _media(link if isinstance(link, str) else (link or {}).get("url"))
        if link and link not in out:
            out.append(link)
    if not out:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return out
