"""WeebCentral: chapters from weebcentral.com (what MangaSee and MangaLife became).

The site's pages are put together from HTMX fragments, each a request of its own: search results and lists come
from /search/data, a series' whole chapter list from /series/<id>/full-chapter-list, and a chapter's pictures from
/chapters/<id>/images (all of them on one page with reading_style=long_strip).
"""
import time
from urllib.parse import urlencode

import requests

RECAP_PLUGIN = 1
NAME = "WeebCentral"
VERSION = "1.0.0"
SITES = ["weebcentral.com"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest updates"},
         {"id": "new", "name": "Recently added"}]
HEADERS = {"Referer": "https://weebcentral.com/"}
DELAY = 0
TEST_QUERY = "solo leveling"

# @include dom

BASE = "https://weebcentral.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
PER_PAGE = 32
SORT = {"popular": "Popularity", "latest": "Latest Updates", "new": "Recently Added"}
KINDS = {"manga": "manga", "manhwa": "webtoon", "manhua": "webtoon"}  # (OEL comics are either: left unsaid)
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})


def _get(url, fragment=False):
    headers = {"HX-Request": "true"} if fragment else {}  # (what the site's own page sends for its fragments)
    for attempt in range(2):
        r = _session.get(url, headers=headers, timeout=45)
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


def _fields(card):
    """A result's "Year:", "Type:"... lines as {"year": "2018", "type": "Manhwa"}."""
    out = {}
    for label in card.select("strong"):
        key = label.text.rstrip(": ").lower()
        value = label.parent.one("span") if label.parent is not None else None
        if key and value is not None:
            out.setdefault(key, value.text.rstrip(","))
    return out


def _results(url):
    """Search and list results from /search/data: {"items", "more"}."""
    doc = parse(_get(url, fragment=True).text)
    items, seen = [], set()
    for card in doc.select("article.bg-base-300"):
        link = card.one("a.link[href*='/series/']") or card.one("a[href*='/series/']")
        if link is None:
            continue
        url = absolute(BASE, link.get("href"))
        if url in seen:
            continue
        seen.add(url)
        img = card.one("img")
        title = link.text or (img.get("alt").removesuffix(" cover") if img is not None else "")
        fields = _fields(card)
        year = fields.get("year", "")
        items.append({"url": url, "title": title, "cover": picture(img, BASE),
                      "kind": KINDS.get(fields.get("type", "").lower(), ""),
                      "year": int(year) if year.isdigit() else None})
    # (another page: the "View More Results..." button that asks for the next offset)
    more = doc.one("button[hx-get*='offset=']") is not None
    return {"items": items, "more": bool(items) and more}


def _search_url(page, **values):
    query = {"text": "", "sort": "Best Match", "order": "Descending", "official": "Any", "anime": "Any",
             "adult": "Any", "display_mode": "Full Display", "limit": PER_PAGE, "offset": (page - 1) * PER_PAGE}
    query.update(values)
    return f"{BASE}/search/data?{urlencode(query)}"


def search(query, page=1):
    return _results(_search_url(page, text=query.strip()))


def browse(list_id, page=1):
    if list_id not in SORT:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    return _results(_search_url(page, sort=SORT[list_id]))


def _series_id(url):
    """The series' id from its link (/series/<id>/<title>), or from a chapter's page (/chapters/<id>)."""
    parts = [p for p in url.split("?")[0].split("#")[0].split("/") if p]
    if "series" in parts and parts.index("series") + 1 < len(parts):
        return parts[parts.index("series") + 1]
    if "chapters" in parts:  # (a chapter's link: its page links to its series)
        for a in parse(_get(url).text).select("a[href*='/series/']"):
            found = [p for p in a.get("href").split("?")[0].split("/") if p]
            if "series" in found and found.index("series") + 1 < len(found):
                return found[found.index("series") + 1]
    raise ValueError(f"That isn't a series' link on {NAME}: {url}")


def _detail(doc, label):
    """The value after a "Type: " / "Released: " label on a series' page."""
    for strong in doc.select("li strong"):
        if strong.text.rstrip(": ").lower() == label and strong.parent is not None:
            value = strong.parent.one("a, span")
            return value.text if value is not None else ""
    return ""


def series(url):
    sid = _series_id(url)
    page_url = url if "/series/" in url else f"{BASE}/series/{sid}"
    doc = parse(_get(page_url).text)
    title = doc.one("main h1, h1")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    cover = doc.one("meta[property='og:image']")
    summary = None
    for strong in doc.select("li strong"):
        if strong.text.lower() == "description" and strong.parent is not None:
            summary = strong.parent.one("p")
            break
    year = _detail(doc, "released")

    # The whole chapter list is a fragment of its own (the page shows only the latest few), newest first.
    rows = parse(_get(f"{BASE}/series/{sid}/full-chapter-list", fragment=True).text).select("a[href*='/chapters/']")
    chapters, seen = [], set()
    for row in rows:
        link = absolute(BASE, row.get("href"))
        if link in seen:
            continue
        seen.add(link)
        label = row.one("span.grow span")
        name = (label.text if label is not None else row.text) or "Chapter"
        when = row.one("time")
        chapters.append({"url": link, "name": name, "number": number(name),
                         "date": (when.get("datetime") or when.text)[:10] if when is not None else ""})
    chapters.reverse()
    return {"url": f"{BASE}/series/{sid}", "title": title.text,
            "cover": absolute(BASE, cover.get("content")) if cover is not None else picture(doc.one("picture img"), BASE),
            "summary": summary.text if summary is not None else "",
            "kind": KINDS.get(_detail(doc, "type").lower(), ""), "year": int(year) if year.isdigit() else None,
            "chapters": chapters}


def pages(chapter_url):
    cid = [p for p in chapter_url.split("?")[0].split("#")[0].split("/") if p][-1]
    if "/chapters/" not in chapter_url:
        raise ValueError(f"That isn't a chapter's link on {NAME}: {chapter_url}")
    query = urlencode({"is_prev": "False", "current_page": 1, "reading_style": "long_strip"})
    doc = parse(_get(f"{BASE}/chapters/{cid}/images?{query}", fragment=True).text)
    found = []
    for img in doc.select("img[alt^='Page']"):
        src = picture(img, BASE)
        if src and src not in found and "/static/" not in src:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
