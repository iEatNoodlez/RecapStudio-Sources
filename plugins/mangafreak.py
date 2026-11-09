"""MangaFreak: chapters from mangafreak.me (ww3.mangafreak.me and its other ww* mirrors).

Search is /Find/<words>?page=…; the lists are its ranking (/Genre/All/<page>, 15 a page) and Latest Releases
(/Latest_Releases/<page>, 30 a page); a series' whole chapter list is on its page (/Manga/<Name>, first chapter
first); and a chapter's pictures are all on its reader page (/Read1_<Name>_<n>), from images.mangafreak.me, which
wants the site as Referer.
"""
import re
import time
from urllib.parse import quote, urlparse

import requests

RECAP_PLUGIN = 1
NAME = "MangaFreak"
VERSION = "1.0.0"
SITES = ["mangafreak.me"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest releases"}]
HEADERS = {"Referer": "https://ww3.mangafreak.me/"}
DELAY = 0
TEST_QUERY = "one piece"

# @include dom

BASE = "https://ww3.mangafreak.me"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
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
    """A link on any of the site's mirrors (ww1, ww3...) as one on BASE."""
    parts = urlparse(url.strip())
    return BASE + (parts.path or "/")


def _kind(text):
    """From the site's type line: "Manga (Read Right to Left)" / "Type: Right(→) to Left(←)" is manga, and its
    "Manhwa (Read Left to Right)" / "Type: Left(←) to Right(→)" a webtoon."""
    text = (text or "").lower()
    if "manhwa" in text or "webtoon" in text or "manhua" in text:
        return "webtoon"
    right, left = text.find("right"), text.find("left")
    if right >= 0 and left >= 0:
        return "manga" if right < left else "webtoon"
    return "manga" if "manga" in text else ""


def _more(doc):
    return doc.one(".pagination a.next_p") is not None


def search(query, page=1):
    words = quote(query.strip().lower(), safe="")  # (what the site's own search box does: trimmed, lower case)
    doc = parse(_get(f"{BASE}/Find/{words}?page={page}").text)
    items, seen = [], set()
    for card in doc.select(".manga_search_item"):
        link = card.one("h3 a[href^='/Manga/']") or card.one("a[href^='/Manga/']")
        if link is None:
            continue
        url = absolute(BASE, link.get("href"))
        if url in seen:
            continue
        seen.add(url)
        kind = card.one("strong")
        items.append({"url": url, "title": link.text, "cover": picture(card.one("img"), BASE),
                      "kind": _kind(kind.text if kind is not None else "")})
    return {"items": items, "more": bool(items) and _more(doc)}


def browse(list_id, page=1):
    if list_id == "popular":
        doc = parse(_get(f"{BASE}/Genre/All/{page}").text)
        cards, title_css = doc.select(".ranking_item"), "h3"
    elif list_id == "latest":
        doc = parse(_get(f"{BASE}/Latest_Releases/{page}").text)
        cards, title_css = doc.select(".latest_releases_item"), "strong"
    else:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    items, seen = [], set()
    for card in cards:
        link = card.one("a[href^='/Manga/']")
        if link is None:
            continue
        url = absolute(BASE, link.get("href"))
        if url in seen:
            continue
        seen.add(url)
        heading = link.one(title_css) or card.one(title_css)
        lines = " ".join(d.text for d in card.select("div"))
        items.append({"url": url, "title": heading.text if heading is not None else link.text,
                      "cover": picture(card.one("img"), BASE), "kind": _kind(lines)})
    return {"items": items, "more": bool(items) and _more(doc)}


def series(url):
    url = _on_site(url)
    if "/Read" in url:  # (a chapter's link: its page's heading links to the series)
        link = parse(_get(url).text).one("h1 a[href^='/Manga/'], a[href^='/Manga/']")
        if link is None:
            raise ValueError(f"That isn't a series' link on {NAME}: {url}")
        url = absolute(BASE, link.get("href"))
    m = re.match(r"(https://[^/]+/Manga/[^/?#]+)", url)
    if not m:
        raise ValueError(f"That isn't a series' link on {NAME}: {url}")
    url = m.group(1)
    doc = parse(_get(url).text)
    title = doc.one(".manga_series_data h1") or doc.one("h1")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    lines = [d.text for d in doc.select(".manga_series_data div")]
    year = next((l.split(":", 1)[1].strip() for l in lines if l.lower().startswith("year published")), "")
    kind = _kind(next((l for l in lines if l.lower().startswith("type")), ""))
    if not kind:
        kind = _kind(" ".join(a.text for a in doc.select(".series_sub_genre_list a")))
    about = doc.one(".manga_series_description p")
    chapters, seen = [], set()
    for row in doc.select(".manga_series_list tr"):
        link = row.one("a.chapter-link") or row.one("a[href^='/Read']")
        if link is None:
            continue
        href = absolute(BASE, link.get("href"))
        if href in seen:
            continue
        seen.add(href)
        cells = row.select("td")
        when = cells[1].text if len(cells) > 1 else ""
        name = link.text or "Chapter"
        num = number(name)
        if num is None:  # (the link's own number: /Read1_<Name>_<n>)
            m = re.search(r"_(\d+(?:\.\d+)?)[a-z]?$", href)
            num = float(m.group(1)) if m else None
        chapters.append({"url": href, "name": name, "number": num,
                         "date": when.replace("/", "-") if re.fullmatch(r"\d{4}/\d{2}/\d{2}", when) else when})
    if len(chapters) > 1 and chapters[0]["number"] is not None and chapters[-1]["number"] is not None \
            and chapters[0]["number"] > chapters[-1]["number"]:
        chapters.reverse()
    return {"url": url, "title": title.text, "cover": picture(doc.one(".manga_series_image img"), BASE),
            "summary": about.text if about is not None else "", "kind": kind,
            "year": int(year) if year.isdigit() else None, "chapters": chapters}


def pages(chapter_url):
    url = _on_site(chapter_url)
    if "/Read" not in url:
        raise ValueError(f"That isn't a chapter's link on {NAME}: {chapter_url}")
    doc = parse(_get(url).text)
    found = []
    for img in doc.select(".mySlides img, img#gohere"):
        src = picture(img, BASE)
        if src and src not in found and "/share/" not in src:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
