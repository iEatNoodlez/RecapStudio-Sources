"""MangaKatana: chapters from mangakatana.com.

Search, the Hot list (the site's "Hot Manga" box: one page), Latest updates and New manga; a series' page lists all
its chapters; a chapter's pictures are a list in the page's script (the one its reader puts into the pictures'
data-src).
"""
import re
import time
from datetime import datetime
from urllib.parse import quote_plus

import requests

RECAP_PLUGIN = 1
NAME = "MangaKatana"
VERSION = "1.0.0"
SITES = ["mangakatana.com"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "hot", "name": "Hot"}, {"id": "latest", "name": "Latest updates"}, {"id": "new", "name": "New manga"}]
HEADERS = {"Referer": "https://mangakatana.com/"}
DELAY = 0
TEST_QUERY = "solo leveling"

# @include dom

BASE = "https://mangakatana.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
FOLDER = {"latest": "latest", "new": "new-manga"}
WEBTOON = {"webtoon", "manhwa", "manhua"}  # (genres the site tags vertical comics with)
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})


def _get(url):
    for attempt in range(3):
        r = _session.get(url, timeout=45)
        if r.status_code == 429 and attempt == 0:
            time.sleep(min(30, int(r.headers.get("Retry-After") or 10)))
            continue
        if r.status_code in (403, 503) and ("cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        if r.status_code == 404:
            raise ValueError(f"{NAME} has no such page: {url}")
        r.raise_for_status()
        if not r.content.strip() and attempt < 2:  # (it sometimes answers with an empty page: asked again, it doesn't)
            time.sleep(2)
            continue
        if not r.content.strip():
            raise RuntimeError(f"{NAME} sent an empty page for {url}: try again in a minute")
        return r
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _kind(genres):
    names = {g.text.strip().lower() for g in genres}
    return "webtoon" if names & WEBTOON else "manga" if names else ""


def _cards(doc, where="#book_list > .item"):
    """Search and list results: [{"url", "title", "cover", "summary", "kind"}]."""
    out, seen = [], set()
    for card in doc.select(where):
        link = card.one("h3.title a, .title a")
        url = absolute(BASE, link.get("href")) if link is not None else ""
        if not url or url in seen or "/manga/" not in url:
            continue
        seen.add(url)
        img = card.one("img")
        cover = absolute(BASE, img.get("lazy-src")) if img is not None and img.get("lazy-src") else picture(img, BASE)
        summary = card.one(".summary")
        out.append({"url": url, "title": link.text, "cover": cover, "summary": summary.text if summary is not None else "",
                    "kind": _kind(card.select(".genres a"))})
    return out


def _more(doc):
    return doc.one("a.next.page-numbers") is not None


def _listing(url):
    r = _get(url)
    doc = parse(r.text)
    if "/manga/" in r.url and doc.one("#single_book") is not None:
        # One match: the site goes straight to that series' page.
        s = _about(doc)
        return {"items": [{"url": r.url.split("?")[0], "title": s["title"], "cover": s["cover"],
                           "summary": s["summary"], "kind": s["kind"]}], "more": False}
    items = _cards(doc)
    return {"items": items, "more": bool(items) and _more(doc)}


def search(query, page=1):
    folder = f"/page/{page}" if page > 1 else "/"
    return _listing(f"{BASE}{folder}?search={quote_plus(query.strip())}&search_by=book_name")


def browse(list_id, page=1):
    if list_id == "hot":  # (a box beside the directory: one page only)
        if page > 1:
            return {"items": [], "more": False}
        return {"items": _cards(parse(_get(f"{BASE}/manga").text), "#hot_book .item"), "more": False}
    if list_id not in FOLDER:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    folder = f"/page/{page}" if page > 1 else ""
    return _listing(f"{BASE}/{FOLDER[list_id]}{folder}")


def _date(text):
    """"May-31-2023" as "2023-05-31" (left as it is when it's something else)."""
    text = (text or "").strip()
    try:
        return datetime.strptime(text, "%b-%d-%Y").date().isoformat()
    except ValueError:
        return text


def _about(doc):
    """A series' page: its title, cover, summary and kind."""
    title = doc.one("#single_book h1.heading, h1.heading")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}")
    summary = doc.one("#single_book .summary p, .summary p")
    return {"title": title.text, "cover": picture(doc.one("#single_book .cover img"), BASE),
            "summary": summary.text if summary is not None else "",
            "kind": _kind(doc.select("#single_book .genres a"))}


def series(url):
    # A series is /manga/<name>.<id>; a chapter's link (/manga/<name>.<id>/c12) is taken as its series.
    m = re.match(r"(https?://[^/]+/manga/[^/?#]+)", url.strip())
    if not m:
        raise ValueError(f"That isn't a series' link on {NAME}: {url}")
    url = m.group(1)
    doc = parse(_get(url).text)
    out = {"url": url, **_about(doc)}
    chapters, seen = [], set()
    for row in doc.select(".chapters tr"):
        link = row.one(".chapter a")
        if link is None:
            continue
        href = absolute(BASE, link.get("href"))
        if href in seen:
            continue
        seen.add(href)
        when = row.one(".update_time")
        chapters.append({"url": href, "name": link.text, "number": number(link.text),
                         "date": _date(when.text) if when is not None else ""})
    chapters.reverse()  # (newest first on the page)
    out["chapters"] = chapters
    return out


def pages(chapter_url):
    text = _get(chapter_url).text
    # The reader fills each picture's data-src from one of the script's lists (its name changes): find which.
    name = re.search(r"""data-src['"]\s*,\s*(\w+)""", text)
    found = []
    if name:
        listed = re.search(r"var\s+" + re.escape(name.group(1)) + r"\s*=\s*\[(.*?)\]", text, re.S)
        if listed:
            found = [absolute(chapter_url, u) for u in re.findall(r"""['"]([^'"]+)['"]""", listed.group(1))]
    if not found:  # (an older page: the pictures in the HTML)
        found = [picture(img, chapter_url) for img in parse(text).select("#imgs .wrap_img img")]
        found = [u for u in found if u and not u.endswith("#")]
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return list(dict.fromkeys(found))
