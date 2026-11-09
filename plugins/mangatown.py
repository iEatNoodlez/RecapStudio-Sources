"""MangaTown: chapters from www.mangatown.com.

Search is /search?name=…&page=…; the lists are the site's Hot Manga, Latest Releases and its directory sorted by
rating (/hot/, /latest/, /directory/?rating.za; 30 a page); a series' whole chapter list is on its page (newest
first); and a chapter's reader shows one picture a page, so pages() reads the chapter's page list and visits every
page, a few at a time, for its picture. The pictures want the site as Referer.
"""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode, urlparse

import requests

RECAP_PLUGIN = 1
NAME = "MangaTown"
VERSION = "1.0.0"
SITES = ["mangatown.com"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest updates"},
         {"id": "rating", "name": "Top rated"}]
HEADERS = {"Referer": "https://www.mangatown.com/"}
DELAY = 0
TEST_QUERY = "golden kamui"

# @include dom

BASE = "https://www.mangatown.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
PAGE_READERS = 3  # how many of a chapter's pages are fetched at a time
KINDS = {"manga": "manga", "manhwa": "webtoon", "manhua": "webtoon"}
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), 1)}
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})


def _get(url):
    for attempt in range(2):
        r = _session.get(url, timeout=45)
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


def _on_site(url):
    """A link on the site as one on www.mangatown.com (http ones, the mobile site's, ones without www)."""
    parts = urlparse(url.strip())
    return BASE + (parts.path or "/") + (f"?{parts.query}" if parts.query else "")


def _results(url):
    """The series on a page of cards (search, Hot, Latest, the directory): {"items", "more"}."""
    doc = parse(_get(url).text)
    items, seen = [], set()
    for card in doc.select("ul.manga_pic_list li"):
        link = card.one("p.title a, a.manga_cover")
        if link is None:
            continue
        href = absolute(BASE, link.get("href"))
        if href in seen:
            continue
        seen.add(href)
        img = card.one("a.manga_cover img")
        items.append({"url": href, "title": link.get("title") or link.text,
                      "cover": picture(img, BASE)})
    nxt = doc.one("a.next")
    more = nxt is not None and nxt.get("href").startswith("/")  # (javascript:void(0) on the last page)
    return {"items": items, "more": bool(items) and more}


def search(query, page=1):
    return _results(f"{BASE}/search?{urlencode({'name': query.strip(), 'page': page})}")


def browse(list_id, page=1):
    suffix = f"{page}.htm" if page > 1 else ""
    if list_id == "popular":
        return _results(f"{BASE}/hot/{suffix}")
    if list_id == "latest":
        return _results(f"{BASE}/latest/{suffix}")
    if list_id == "rating":
        return _results(f"{BASE}/directory/{suffix}?rating.za")
    raise ValueError(f"{NAME} has no list {list_id!r}")


def _date(text):
    """"Sep 02,2026" as "2026-09-02" (else as it is: "Today", "Yesterday")."""
    m = re.match(r"\s*([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),\s*(\d{4})", text or "")
    if m and m.group(1).lower() in MONTHS:
        return f"{m.group(3)}-{MONTHS[m.group(1).lower()]:02d}-{int(m.group(2)):02d}"
    return (text or "").strip()


def _detail(doc, label):
    """The value after a "Type:" / "Status(s):" label on a series' page."""
    for b in doc.select(".detail_info li b"):
        if b.text.rstrip(": ").lower().startswith(label) and b.parent is not None:
            value = b.parent.one("a")
            return value.text if value is not None else b.parent.own_text
    return ""


def series(url):
    url = _on_site(url)
    m = re.match(r"(https://[^/]+/manga/[^/?#]+)", url)
    if not m:
        raise ValueError(f"That isn't a series' link on {NAME}: {url}")
    url = m.group(1) + "/"  # (a chapter's link, /manga/<name>/c001/, is its series' plus the chapter)
    doc = parse(_get(url).text)
    title = doc.one("h1.title-top")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    img, meta = doc.one(".detail_info img"), doc.one("meta[property='og:image']")
    cover = picture(img, BASE) if img is not None else absolute(BASE, meta.get("content")) if meta is not None else ""
    about = doc.one("span#show") or doc.one("span#hide")  # (the whole summary, else the shortened one shown)
    chapters, seen = [], set()
    for row in doc.select("ul.chapter_list li"):
        link = row.one("a[href]")
        if link is None:
            continue
        href = absolute(BASE, link.get("href"))
        if href in seen:
            continue
        seen.add(href)
        m = re.search(r"/c(\d+(?:\.\d+)?)/?$", href)
        num = float(m.group(1)) if m else number(link.text)
        extra = " - ".join(s.text for s in row.select("span") if s.text and "time" not in s.classes and "new" not in s.classes)
        name = (f"Chapter {m.group(1).lstrip('0') or '0'}" if m else link.text) + (f" - {extra}" if extra else "")
        when = row.one("span.time")
        chapters.append({"url": href, "name": name, "number": num, "date": _date(when.text) if when is not None else ""})
    chapters.reverse()  # (newest first on the page)
    return {"url": url, "title": title.text, "cover": cover,
            "summary": about.own_text if about is not None else "",
            "kind": KINDS.get(_detail(doc, "type").strip().lower(), ""), "chapters": chapters}


def _picture(doc):
    img = doc.one("img#image")
    return picture(img, BASE) if img is not None else ""


def pages(chapter_url):
    url = _on_site(chapter_url)
    if not re.search(r"/manga/[^/]+/(?:v[^/]+/)?c[^/]+/?", url):
        raise ValueError(f"That isn't a chapter's link on {NAME}: {chapter_url}")
    doc = parse(_get(url).text)
    first = _picture(doc)
    if not first:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    # The reader shows one picture a page; the page list is the reader's own dropdown ("featured" is an ad page).
    links = []
    for option in doc.select(".page_select select option"):
        href = absolute(BASE, option.get("value"))
        if href and href not in links and "featured" not in href.rsplit("/", 1)[-1]:
            links.append(href)
    rest = links[1:] if links else []
    with ThreadPoolExecutor(PAGE_READERS) as pool:
        found = [first] + list(pool.map(lambda href: _picture(parse(_get(href).text)), rest))
    pictures = []
    for src in found:
        if src and src not in pictures:
            pictures.append(src)
    return pictures
