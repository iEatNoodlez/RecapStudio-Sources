"""TCB Scans: chapters from tcbonepiecechapters.com (One Piece, Chainsaw Man, Jujutsu Kaisen and the group's other
Shonen Jump series).

A small site with no search of its own: /projects lists every series it works on, with its cover, and search here
filters that list by the words typed. A series' page lists its chapters (newest first), each chapter's page has all
of its pictures (img.fixed-ratio-content, on the group's own picture host). The site is slow at times and answers
502 now and then, so a request is tried twice. Links on the group's older domains (tcbscans.com, tcbscans.me) open
on the current one: the paths are the same.
"""
import re
import time
from urllib.parse import urlsplit

import requests

RECAP_PLUGIN = 1
NAME = "TCB Scans"
VERSION = "1.0.0"
SITES = ["tcbonepiecechapters.com", "tcbscans.com", "tcbscans.me", "onepiecechapters.com"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "projects", "name": "Projects"}]
HEADERS = {"Referer": "https://tcbonepiecechapters.com/"}
DELAY = 0
TEST_QUERY = "chainsaw"

# @include dom

BASE = "https://tcbonepiecechapters.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})


def _here(url):
    """A link on one of the group's domains, on BASE."""
    parts = urlsplit(url)
    return BASE + parts.path + (f"?{parts.query}" if parts.query else "")


def _get(url):
    for attempt in range(3):
        r = _session.get(url, timeout=60)
        if r.status_code == 429 and attempt == 0:
            time.sleep(min(30, int(r.headers.get("Retry-After") or 10)))
            continue
        if r.status_code in (403, 503) and ("cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        if r.status_code == 404:
            raise ValueError(f"{NAME} has no such page: {url}")
        if r.status_code >= 500 and attempt < 2:  # (the site answers 502 now and then)
            time.sleep(4)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _chapter_number(name, href):
    m = re.search(r"chapter[\s-]*(\d+(?:\.\d+)?)", name, re.I) or re.search(r"chapter-(\d+(?:\.\d+)?)", href, re.I)
    return float(m.group(1)) if m else None


_projects = None  # the series the group works on, read once a session


def _all_projects():
    global _projects
    if _projects is None:
        doc = parse(_get(f"{BASE}/projects").text)
        items, seen = [], set()
        for link in doc.select("a[href^='/mangas/']"):
            href = link.get("href")
            if not href or href in seen:
                continue
            title = link.text.strip()
            img = link.one("img")
            if img is not None and not title:
                title = (img.get("alt") or "").strip()
            card = items[-1] if items and items[-1]["url"] == absolute(BASE, href) else None
            if card is None:
                seen.add(href)
                items.append({"url": absolute(BASE, href), "title": title, "cover": picture(img, BASE) if img is not None else "",
                              "summary": "", "kind": "manga", "year": None})
            else:  # (the same series' second link: its cover came first, its title second)
                if not card["title"]:
                    card["title"] = title
                if not card["cover"] and img is not None:
                    card["cover"] = picture(img, BASE)
        # a series' cover link and its title link follow each other: join them
        joined, by_url = [], {}
        for it in items:
            if it["url"] in by_url:
                have = by_url[it["url"]]
                have["title"] = have["title"] or it["title"]
                have["cover"] = have["cover"] or it["cover"]
            else:
                by_url[it["url"]] = it
                joined.append(it)
        _projects = [it for it in joined if it["title"]]
    return _projects


def search(query, page=1):
    words = [w for w in query.lower().split() if w]
    items = [it for it in _all_projects() if all(w in it["title"].lower() for w in words)] if page == 1 else []
    return {"items": items, "more": False}


def browse(list_id, page=1):
    if list_id != "projects":
        raise ValueError(f"{NAME} has no list called {list_id}")
    return {"items": _all_projects() if page == 1 else [], "more": False}


def series(url):
    url = _here(url)
    doc = parse(_get(url).text)
    title = doc.one("h1")
    title = title.text.strip() if title is not None else ""
    cover = ""
    for img in doc.select("img"):
        if title and (img.get("alt") or "").strip() == title:
            cover = picture(img, BASE)
            break
    summary = doc.one("p.leading-6")
    chapters = []
    for link in doc.select("a[href^='/chapters/']"):
        href = link.get("href")
        name_node = link.one("div")
        name = (name_node.text if name_node is not None else link.text).strip()
        if not href or not name:
            continue
        chapters.append({"url": absolute(BASE, href), "name": name, "number": _chapter_number(name, href), "date": None})
    chapters.reverse()  # (newest first on the page)
    seen, kept = set(), []
    for c in chapters:
        if c["url"] not in seen:
            seen.add(c["url"])
            kept.append(c)
    return {"title": title, "cover": cover, "summary": summary.text.strip() if summary is not None else "",
            "kind": "manga", "chapters": kept}


def pages(chapter_url):
    doc = parse(_get(_here(chapter_url)).text)
    urls, seen = [], set()
    for img in doc.select("img.fixed-ratio-content"):
        src = picture(img, BASE)
        if src and src not in seen:
            seen.add(src)
            urls.append(src)
    if not urls:
        raise RuntimeError(f"{NAME} shows no pictures for this chapter: {chapter_url}")
    return urls
