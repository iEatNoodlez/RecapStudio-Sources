"""MangaDex: English chapters from mangadex.org, through its official public API (api.mangadex.org, no account).

Search, the Popular (most followed) and Latest (newest English chapters) lists, a series' English chapters and a
chapter's pictures, at full quality, from the server MangaDex picks for it (/at-home/server: often a MangaDex@Home
volunteer's). A chapter number translated by several groups is listed once: the version by the group that did most
of the series (named after the chapter's title), else the next one's. Chapters that only link to a publisher's own
site, or have no pages, are left out. Series rated "pornographic" are left out too (CONTENT_RATINGS below).

MangaDex's rules (https://api.mangadex.org/docs/2-limitations/): a User-Agent of the program's own, not a browser's
(this one, sent with its pictures and covers too: HEADERS); about 5 requests a second (this plugin asks at most 4);
40 a minute for /at-home/server.
"""
import html
import re
import time
from collections import Counter

import requests

RECAP_PLUGIN = 1
NAME = "MangaDex"
VERSION = "1.0.0"
SITES = ["mangadex.org"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}]
HEADERS = {"User-Agent": "RecapStudio-MangaDex/1.0.0"}  # (MangaDex asks for the program's own name)
DELAY = 0
TEST_QUERY = "berserk"

API = "https://api.mangadex.org"
SITE = "https://mangadex.org"
CONTENT_RATINGS = ["safe", "suggestive", "erotica"]  # (add "pornographic" to have those too)
GAP = 0.25             # seconds between API requests: at most 4 a second
AT_HOME_GAP = 1.6      # seconds between /at-home/server requests: under its 40 a minute
PER_PAGE = 30          # series a page in search and Popular
CHAPTERS_PER_PAGE = 100  # chapters looked at a page in Latest
MOST = 10000           # MangaDex answers no further into a list than this (offset + limit)
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_last = {"api": 0.0, "at-home": 0.0}
_session = requests.Session()
_session.headers.update({**HEADERS, "Accept": "application/json"})


def _wait(kind, gap):
    wait = _last[kind] + gap - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last[kind] = time.monotonic()


def _api(path, params=(), at_home=False):
    """One API request, paced, waiting once when MangaDex says to slow down."""
    for attempt in range(2):
        _wait("api", GAP)
        if at_home:
            _wait("at-home", AT_HOME_GAP)
        try:
            r = _session.get(API + path, params=list(params), timeout=30)
        except requests.RequestException as e:
            raise RuntimeError(f"{NAME} didn't answer: {e}")
        if r.status_code == 429 and attempt == 0:
            until, after = r.headers.get("X-RateLimit-Retry-After", ""), r.headers.get("Retry-After", "")
            wait = float(until) - time.time() if until.isdigit() else float(after) if after.isdigit() else 10
            time.sleep(min(60, max(1, wait)))  # (asking again sooner only makes it block for longer)
            continue
        if r.status_code == 429:
            raise RuntimeError(f"{NAME} says it's getting too many requests from here: try again in a minute")
        if r.status_code == 403 and "json" not in r.headers.get("Content-Type", ""):
            raise RuntimeError(f"{NAME} is refusing this computer for now (too many requests earlier, or a VPN or "
                               "proxy it doesn't allow): try again later")
        try:
            found = r.json()
        except ValueError:
            r.raise_for_status()
            raise RuntimeError(f"{NAME} gave an answer that isn't JSON")
        if r.status_code == 404:
            raise ValueError(f"{NAME} has no such series or chapter")
        if found.get("result") == "error" or r.status_code >= 400:
            error = (found.get("errors") or [{}])[0]
            raise RuntimeError(f"{NAME} answered: {error.get('detail') or error.get('title') or r.status_code}")
        return found
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _ratings():
    return [("contentRating[]", r) for r in CONTENT_RATINGS]


def _clean(text, most):
    """A description (Markdown) as plain text, shortened at a sentence's end."""
    text = html.unescape(text or "").replace("\r", "")
    text = re.split(r"\n\s*(?:-{3,}|_{3,}|\*{3,})\s*(?:\n|$)", text)[0]  # (what follows a rule: links, notes)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [words](link)
    text = re.sub(r"(\*\*|__|~~)(.+?)\1", r"\2", text, flags=re.S)
    text = re.sub(r"(?<![\w*])[*_](\S(?:.*?\S)?)[*_](?![\w*])", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > most:
        cut = text[:most]
        end = max(cut.rfind(". "), cut.rfind(".\n"))
        text = cut[:end + 1] if end > most // 3 else cut.rsplit(" ", 1)[0] + "…"
    return text


def _title(a):
    """The English title when it has one (MangaDex's own is often in rōmaji), else its own."""
    titles = a.get("title") or {}
    english = titles.get("en") or next((t["en"] for t in a.get("altTitles") or [] if t.get("en")), "")
    return (english or next(iter(titles.values()), "") or "Untitled").strip()


def _kind(a):
    """Long strips, Korean and Chinese series are webtoons; Japanese ones manga; the rest comics."""
    tags = {(t.get("attributes") or {}).get("name", {}).get("en") for t in a.get("tags") or []}
    language = a.get("originalLanguage") or ""
    if "Long Strip" in tags or language in ("ko", "zh", "zh-hk"):
        return "webtoon"
    return "manga" if language == "ja" else "comic"


def _cover(m, size):
    art = next((r for r in m.get("relationships", []) if r["type"] == "cover_art" and r.get("attributes")), None)
    return f"https://uploads.mangadex.org/covers/{m['id']}/{art['attributes']['fileName']}.{size}.jpg" if art else None


def _card(m, most=300):
    a = m["attributes"]
    return {"url": f"{SITE}/title/{m['id']}", "title": _title(a), "cover": _cover(m, 256),
            "summary": _clean((a.get("description") or {}).get("en"), most), "kind": _kind(a), "year": a.get("year")}


def _series_page(params, page):
    offset = (page - 1) * PER_PAGE
    if offset + PER_PAGE > MOST:
        return {"items": [], "more": False}
    found = _api("/manga", [*params, ("limit", PER_PAGE), ("offset", offset), ("includes[]", "cover_art"),
                            ("availableTranslatedLanguage[]", "en"), *_ratings()])
    return {"items": [_card(m) for m in found["data"]], "more": _more(offset + PER_PAGE, PER_PAGE, found)}


def _more(next_offset, limit, found):
    """Whether there's another page MangaDex will give."""
    return next_offset < found.get("total", 0) and next_offset + limit <= MOST


def search(query, page=1):
    """Series with English chapters whose title matches, the closest first."""
    return _series_page([("title", query.strip()), ("order[relevance]", "desc")], page)


def _latest(page):
    """Series with the newest English chapters, newest first."""
    offset = (page - 1) * CHAPTERS_PER_PAGE
    if offset + CHAPTERS_PER_PAGE > MOST:
        return {"items": [], "more": False}
    found = _api("/chapter", [("translatedLanguage[]", "en"), ("order[readableAt]", "desc"),
                              ("limit", CHAPTERS_PER_PAGE), ("offset", offset), ("includeExternalUrl", 0),
                              ("includeEmptyPages", 0), ("includeFuturePublishAt", 0), *_ratings()])
    ids = list(dict.fromkeys(r["id"] for c in found["data"] for r in c["relationships"] if r["type"] == "manga"))
    more = _more(offset + CHAPTERS_PER_PAGE, CHAPTERS_PER_PAGE, found)
    if not ids:
        return {"items": [], "more": more}
    series = _api("/manga", [*[("ids[]", i) for i in ids], ("limit", len(ids)), ("includes[]", "cover_art"),
                             *_ratings()])["data"]
    order = {i: n for n, i in enumerate(ids)}
    return {"items": [_card(m) for m in sorted(series, key=lambda m: order.get(m["id"], len(ids)))], "more": more}


def browse(list_id, page=1):
    if list_id == "popular":
        return _series_page([("order[followedCount]", "desc"), ("hasAvailableChapters", "true")], page)
    if list_id == "latest":
        return _latest(page)
    raise ValueError(f"{NAME} has no list {list_id}")


def _manga_id(url):
    """A series' id from its link (mangadex.org/title/<id>/...), or from a chapter's link (its series')."""
    found = UUID.search(url or "")
    if not found:
        raise ValueError(f"That isn't a {NAME} series link (one like {SITE}/title/<id>/<name>)")
    if "/chapter/" in url:
        chapter = _api(f"/chapter/{found.group().lower()}")["data"]
        return next(r["id"] for r in chapter["relationships"] if r["type"] == "manga")
    return found.group().lower()


def _group(c):
    return " & ".join(r["attributes"]["name"] for r in c["relationships"]
                      if r["type"] == "scanlation_group" and r.get("attributes"))


def _number(chapter):
    m = re.match(r"\d+(?:\.\d+)?", chapter or "")
    return float(m.group()) if m else None


def _chapters(mid):
    """A series' English chapters that can be read on MangaDex, one per chapter number, first chapter first."""
    rows, offset = [], 0
    while offset + 500 <= MOST:
        found = _api(f"/manga/{mid}/feed", [("translatedLanguage[]", "en"), ("limit", 500), ("offset", offset),
                                            ("includes[]", "scanlation_group"), ("order[volume]", "asc"),
                                            ("order[chapter]", "asc"), ("includeExternalUrl", 0),
                                            ("includeEmptyPages", 0), ("includeFuturePublishAt", 0), *_ratings()])
        rows += found["data"]
        offset += 500
        if offset >= found.get("total", 0):
            break
    rows = [c for c in rows if c["attributes"].get("pages") and not c["attributes"].get("externalUrl")
            and not c["attributes"].get("isUnavailable")]

    def key(c):  # (one entry per chapter number, "12" and "12.0" alike; ones without a number by their title)
        a = c["attributes"]
        chapter = (a.get("chapter") or "").strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", chapter):
            return float(chapter)
        return chapter.lower() or "~" + (a.get("title") or "").strip().lower()

    # The group that translated the most chapter numbers comes first, then the next...; within one group, its
    # newest upload of a chapter (a fixed one).
    done = Counter(g for g, _ in {(_group(c), key(c)) for c in rows})
    best = {}
    for c in rows:
        rank = (done[_group(c)], _group(c), c["attributes"].get("publishAt") or "")
        if key(c) not in best or rank > best[key(c)][0]:
            best[key(c)] = (rank, c)
    out = []
    for _, c in best.values():
        a, group = c["attributes"], _group(c)
        chapter, title = (a.get("chapter") or "").strip(), (a.get("title") or "").strip()
        name = f"Chapter {chapter}" + (f" - {title}" if title else "") if chapter else title or "Oneshot"
        if group and _number(chapter) is not None:  # (the app places a chapter by the first number in its name)
            name += f" [{group}]"
        out.append({"url": f"{SITE}/chapter/{c['id']}", "name": name, "number": _number(chapter),
                    "date": (a.get("readableAt") or a.get("publishAt") or "")[:10]})
    # Numbered ones in order, then any without a number (extras, a oneshot) by date.
    return sorted(out, key=lambda x: (x["number"] is None, x["number"] or 0, x["date"]))


def series(url):
    mid = _manga_id(url)
    m = _api(f"/manga/{mid}", [("includes[]", "cover_art")])["data"]
    a = m["attributes"]
    if a.get("contentRating") not in CONTENT_RATINGS:
        raise RuntimeError(f"{NAME} rates “{_title(a)}” {a.get('contentRating')}, which this plugin leaves out")
    chapters = _chapters(mid)
    if not chapters:
        raise RuntimeError(f"{NAME} has no English chapters of “{_title(a)}” to read there (they may have been taken "
                           "down, or only link to the publisher's own site)")
    return {**_card(m, 1500), "cover": _cover(m, 512), "chapters": chapters}


def pages(chapter_url):
    """A chapter's pictures, at full quality, from the server MangaDex gives for it (good for about 15 minutes)."""
    found = UUID.search(chapter_url or "")
    if not found:
        raise ValueError(f"That isn't a {NAME} chapter link ({SITE}/chapter/<id>)")
    server = _api(f"/at-home/server/{found.group().lower()}", at_home=True)
    files = (server.get("chapter") or {}).get("data") or []
    if not files:
        raise RuntimeError(f"{NAME} has no pages for this chapter (it may only link to the publisher's own site)")
    return [f"{server['baseUrl']}/data/{server['chapter']['hash']}/{f}" for f in files]
