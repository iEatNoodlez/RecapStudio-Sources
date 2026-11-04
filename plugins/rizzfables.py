"""Rizz Fables: chapters from rizzfables.com, a site of its own that copies MangaThemesia's pages (not WordPress).

A Recap Studio source plugin from RecapStudio-Sources. Its search and lists are the site's own requests (JSON, every
result at once); a series' page and a chapter's pictures are read like MangaThemesia's (engines/themesia.py). The site
puts a code in front of every series' and chapter's link (/series/r2311170-<title>) and sends a wrong one to an ad:
the code is read from its home page, and read again when it changes.
"""
RECAP_PLUGIN = 1
NAME = "Rizz Fables"
VERSION = "1.0.0"
SITES = ["rizzfables.com"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}]
HEADERS = {"Referer": "https://rizzfables.com/"}
DELAY = 0
TEST_QUERY = "sect leader"

import json
import re
import time
from urllib.parse import urlparse

import requests

# @include dom

BASE = "https://rizzfables.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})
ORDER = {"popular": "popular", "latest": "update"}
KINDS = {"manhwa": "webtoon", "manhua": "webtoon", "webtoon": "webtoon", "manga": "manga", "comic": "comic"}
SKIP = re.compile(r"ramrvsrc\d+\.webp$|66c47bc2c15dd\.webp$")  # (the site's own banners between pictures)
_code = []


class _Moved(Exception):
    """The site sent a link to an ad: its links' code has changed."""


def _get(url, method="GET", **kw):
    asked, waited = url, False
    for hop in range(6):  # (its own redirects are followed here; one to another site is an ad, not followed)
        r = _session.request(method, url, timeout=45, allow_redirects=False, **kw)
        if r.status_code == 429 and not waited:
            waited = True
            time.sleep(min(30, int(r.headers.get("Retry-After") or 10)))
            continue
        if r.status_code == 429:
            raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")
        if r.status_code in (403, 503) and ("cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        if r.status_code in (301, 302, 303, 307, 308):
            url = absolute(url, r.headers.get("Location"))
            if urlparse(url).hostname not in ("rizzfables.com", "www.rizzfables.com"):
                raise _Moved(asked)
            continue
        if r.status_code == 404:
            raise ValueError(f"{NAME} has no such page: {url}")
        r.raise_for_status()
        return r
    raise RuntimeError(f"{NAME} keeps sending this page somewhere else: {asked}")


def _prefix(again=False):
    """The code in front of every link ("r2311170-"), from the links on the home page."""
    if again or not _code:
        m = re.search(r"/series/(r\d+-)", _get(BASE + "/").text)
        if not m:
            raise RuntimeError(f"{NAME} changed its pages: its series' links weren't found on its home page")
        _code[:] = [m.group(1)]
    return _code[0]


def _slug(title):
    """A series' link name from its title, as the site's own links have it ("Hunter's Journey!" → hunters-journey)."""
    return re.sub(r"[^a-z0-9]+", "-", re.sub(r"['’]", "", title.lower())).strip("-")


def _item(x):
    year = number(str(x.get("released") or ""))
    return {"url": f"{BASE}/series/{_prefix()}{_slug(x['title'])}", "title": x["title"],
            "cover": f"{BASE}/assets/images/{x['image_url']}" if x.get("image_url") else "",
            "summary": x.get("long_description") or "", "kind": KINDS.get(str(x.get("type") or "").lower(), ""),
            "year": int(year) if year else None}


def _post(path, data):
    r = _get(BASE + path, "POST", data=data, headers={"X-Requested-With": "XMLHttpRequest"})
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"{NAME} didn't answer as expected (its site may have changed)")


def search(query, page=1):
    if page > 1:
        return {"items": [], "more": False}  # (the site gives every result at once)
    found = _post("/Index/live_search", {"search_value": query})
    return {"items": [_item(x) for x in found or [] if x.get("title")], "more": False}


def browse(list_id, page=1):
    if page > 1:
        return {"items": [], "more": False}
    found = _post("/Index/filter_series", {"StatusValue": "all", "TypeValue": "all", "OrderValue": ORDER[list_id]})
    return {"items": [_item(x) for x in found or [] if x.get("title")], "more": False}


def _open(url):
    """A series' or chapter's page; a link with an old code is tried again with the current one."""
    try:
        return _get(url).text
    except _Moved:
        fixed = re.sub(r"/(series|chapter)/r\d+-", lambda m: f"/{m.group(1)}/{_prefix(again=True)}", url)
        try:
            return _get(fixed).text
        except _Moved:
            raise ValueError(f"{NAME} has no such page: {url}")


def _info(doc, label):
    for row in doc.select(".imptdt"):
        text = row.text  # ("Type manhua", "Released 2021")
        if text.lower().startswith(label.lower()):
            return text[len(label):].strip(" :")
    return ""


def series(url):
    text = _open(url)
    doc = parse(text)
    title = doc.one("h1.entry-title")
    # (the summary is in a script that writes it into the page)
    m = re.search(r'var description\s*=\s*("(?:[^"\\]|\\.)*")', text)
    try:
        summary = json.loads(m.group(1)) if m else ""
    except ValueError:
        summary = ""
    if not summary:
        box = doc.one(".entry-content[itemprop=description]")
        summary = box.text if box is not None else ""
    chapters = []
    for row in doc.select("#chapterlist li"):
        link = row.one("a[href]")
        if link is None or not link.get("href").startswith(("http", "/")):
            continue
        label = row.one(".chapternum")
        name = label.text if label is not None else link.text
        date = row.one(".chapterdate")
        num = number(name)
        if num is None and row.get("data-num"):
            num = number(row.get("data-num"))
        chapters.append({"url": absolute(BASE, link.get("href")), "name": name, "number": num,
                         "date": date.text if date is not None else ""})
    chapters.reverse()  # (newest first on the page)
    year = number(_info(doc, "Released"))
    return {"url": url, "title": title.text if title is not None else "Untitled",
            "cover": picture(doc.one(".thumb img"), BASE), "summary": summary,
            "kind": KINDS.get(_info(doc, "Type").lower(), ""), "year": int(year) if year else None,
            "chapters": chapters}


def pages(chapter_url):
    doc = parse(_open(chapter_url))
    found = []
    for img in doc.select("#readerarea img"):
        src = picture(img, chapter_url)
        if src and not SKIP.search(src) and src not in found:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
