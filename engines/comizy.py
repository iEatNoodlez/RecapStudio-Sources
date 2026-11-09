# engine: Comizy
# version: 1.0.0
# title: the Comizy reader (Comizy, TOONTOP: a Next.js site with a JSON API at api.<site>)
# lists: [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}, {"id": "new", "name": "Recently added"}]
#
# Search and the lists come from the site's API (/titles/search, sorted by views, latest chapter or newest series);
# a series' details from /titles/by-slug/<name> and its whole chapter list from /titles/<id>/chapters; a chapter's
# pictures from /titles/by-slug/<name>/chapters/<chapter> (the same JSON the site's own pages are rendered from).
# The pictures need the site as Referer (HEADERS has it).
# OPTIONS (sites.json): "api", the API's address when it isn't api.<site>; "kind", the kind of every series when the
# API doesn't say ("webtoon" for an all-manhwa site).
import re
import time
from urllib.parse import unquote, urlparse

import requests

# @include dom

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
API = (OPTIONS.get("api") or BASE.replace("://www.", "://").replace("://", "://api.", 1)).rstrip("/")
SORT = {"popular": "views", "latest": "latest", "new": "newest"}
KINDS = {"manhwa": "webtoon", "manhua": "webtoon", "webtoon": "webtoon", "manga": "manga", "comic": "comic"}
NOT_SERIES = {"search", "latest", "popular", "home", "genres", "genre", "browse", "ranking", "lists", "filters", "auth",
              "about", "dmca", "contact", "terms", "static", "api", "_next"}
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/", "Accept": "application/json"})


def _api(path, **params):
    """The API's answer (its "data") at API/<path>."""
    for attempt in range(2):
        r = _session.get(f"{API}/{path}", params=params, timeout=45)
        if r.status_code == 429 and attempt == 0:
            wait = r.headers.get("Retry-After") or "10"
            time.sleep(min(30, int(wait) if wait.isdigit() else 10))
            continue
        if r.status_code in (403, 503) and ("cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        try:
            data = r.json()
        except ValueError:
            r.raise_for_status()
            raise RuntimeError(f"{NAME}'s API gave something other than its usual answer ({r.url}): "
                               "the site may have changed or be down") from None
        if r.status_code == 404 or data.get("code") == "NOT_FOUND":
            raise ValueError(f"{NAME} has no such page: {data.get('message') or r.url}")
        if r.status_code >= 400 or data.get("success") is False:
            raise RuntimeError(f"{NAME}'s API answered: {data.get('message') or r.status_code}")
        return data.get("data") or {}
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _plain(text):
    """A summary as plain text (they carry a little Markdown: links, bold)."""
    text = (text or "").replace("\r", "")
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(\*\*|__|~~)(.+?)\1", r"\2", text, flags=re.S)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _kind(item):
    name = ((item.get("type") or {}).get("name") or "").lower()
    return KINDS.get(name, "") or OPTIONS.get("kind", "")


def _card(item):
    year = str(item.get("release_date") or "")
    return {"url": BASE + (item.get("url") or f"/{item.get('slug', '')}"),
            "title": (item.get("name") or item.get("slug") or "").strip(), "cover": item.get("cover") or "",
            "summary": _plain(item.get("summary")), "kind": _kind(item),
            "year": int(year) if len(year) == 4 and year.isdigit() else None}


def _list(page, **params):
    """One page of the site's series (/titles/search), as search and list results."""
    data = _api("titles/search", page=page, **params)
    items = [_card(i) for i in data.get("items") or [] if i.get("url") or i.get("slug")]
    return {"items": items, "more": bool(items) and bool((data.get("pagination") or {}).get("has_next"))}


def search(query, page=1):
    return _list(page, q=query.strip())


def browse(list_id, page=1):
    if list_id not in SORT:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    return _list(page, sort=SORT[list_id])


def _slugs(url):
    """The series' (and chapter's) names in a link: /<series>[/<chapter>], or the API's /titles/<id>-<series>."""
    parts = [unquote(p) for p in urlparse(url).path.split("/") if p]
    if parts and parts[0] == "titles":
        parts = [p.split("-", 1)[1] if "-" in p else p for p in parts[1:]]
    if not parts or parts[0] in NOT_SERIES:
        raise ValueError(f"{NAME}'s series links look like {BASE}/<name>, and {url} isn't one")
    return parts


def series(url):
    slug = _slugs(url)[0]
    try:
        title = _api(f"titles/by-slug/{slug}", include="details").get("title") or {}
    except ValueError:
        raise ValueError(f"{NAME} has no series at {url}") from None
    if not title.get("id"):
        raise ValueError(f"{NAME} has no series at {url}")
    # The whole list comes at once, newest first.
    rows = _api(f"titles/{title['id']}/chapters", limit=500).get("chapters") or []
    chapters, seen = [], set()
    for c in rows:
        link = c.get("url") or (f"/{title.get('slug') or slug}/{c['slug']}" if c.get("slug") else "")
        if not link or link in seen:
            continue
        seen.add(link)
        name = (c.get("name") or c.get("slug") or "").strip() or "Chapter"
        chapters.append({"url": BASE + link, "name": name, "number": number(name),
                         "date": (c.get("updated_at") or "")[:10]})
    chapters.reverse()
    return {**_card(title), "chapters": chapters}


def pages(chapter_url):
    parts = _slugs(chapter_url)
    if len(parts) < 2:
        raise ValueError(f"{NAME}'s chapter links look like {BASE}/<series>/<chapter>, and {chapter_url} isn't one")
    try:
        chapter = _api(f"titles/by-slug/{parts[0]}/chapters/{parts[1]}", include="details").get("chapter") or {}
    except ValueError:
        raise ValueError(f"{NAME} has no chapter at {chapter_url}") from None
    found = []
    for item in chapter.get("images") or [p.get("url") for p in chapter.get("pages") or [] if isinstance(p, dict)]:
        if isinstance(item, str) and item.strip() and item not in found:
            found.append(item.strip())
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
