# engine: VTheme
# version: 1.0.0
# title: VTheme (V DEV's comic site system, with a JSON API at api.<site>/api)
# lists: [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}]
#
# Search, the Popular and Latest lists, a series' details and its chapters come from the site's API (/query, /post,
# /chapters); a chapter's pictures from its page (the reader's pictures are in the page's HTML).
# Chapters the site still sells for its coins are left out until they're free (they then show up as new chapters).
# Novels (text, no pictures) are left out of search and lists.
# OPTIONS (sites.json): "api", the API's address when it isn't api.<site>/api.
import time
from urllib.parse import unquote, urlparse

import requests

# @include dom

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
API = (OPTIONS.get("api") or BASE.replace("://www.", "://").replace("://", "://api.", 1) + "/api").rstrip("/")
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})
ORDER = {"popular": "totalViews", "latest": "lastChapterAddedAt"}
KINDS = {"MANHWA": "webtoon", "MANHUA": "webtoon", "MANGA": "manga", "COMIC": "comic"}
PER_PAGE = 24


def _get(url, **params):
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
        return r
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _api(path, **params):
    """The API's answer (JSON) at API/<path>."""
    r = _get(f"{API}/{path}", **params)
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"{NAME}'s API gave something other than its usual answer ({r.url}): "
                           "the site may have changed or be down") from None


def _plain(text):
    """A description as plain text (they're HTML)."""
    return parse(text).text if text and "<" in text else (text or "").strip()


def _novel(post):
    return post.get("isNovel") or str(post.get("seriesType") or "").upper() == "NOVEL"


def _slugs(url):
    """The series' (and chapter's) slugs in a link: .../series/<series>[/<chapter>]."""
    parts = [unquote(p) for p in urlparse(url).path.split("/") if p]
    if "series" in parts:
        parts = parts[parts.index("series") + 1:]
    if not parts:
        raise ValueError(f"{NAME}'s series links look like {BASE}/series/<name>, and {url} isn't one")
    return parts


def _list(page, query="", order="totalViews"):
    """One page of the site's series (/query), as search and list results."""
    params = {"page": page, "perPage": PER_PAGE, "orderBy": order, "orderDirection": "desc"}
    if query:
        params["searchTerm"] = query
    data = _api("query", **params)
    items = [{"url": f"{BASE}/series/{p['slug']}", "title": (p.get("postTitle") or p["slug"]).strip(),
              "cover": p.get("featuredImage") or "", "kind": KINDS.get(str(p.get("seriesType") or "").upper(), "")}
             for p in data.get("posts") or [] if p.get("slug") and not _novel(p)]
    return {"items": items, "more": page * PER_PAGE < (data.get("totalCount") or 0)}


def search(query, page=1):
    return _list(page, query=query)


def browse(list_id, page=1):
    return _list(page, order=ORDER[list_id])


def series(url):
    slug = _slugs(url)[0]
    try:
        post = _api("post", postSlug=slug).get("post") or {}
    except ValueError:
        raise ValueError(f"{NAME} has no series at {url}") from None
    if not post.get("id"):
        raise ValueError(f"{NAME} has no series at {url}")
    if _novel(post):
        raise RuntimeError(f"{NAME}: {post.get('postTitle') or slug} is a novel (text, no pictures), which can't be recapped")
    chapters, skip, take = [], 0, 500
    while True:
        data = _api("chapters", postId=post["id"], take=take, skip=skip)
        got = (data.get("post") or {}).get("chapters") or []
        for c in got:
            if not c.get("slug") or c.get("isAccessible") is False or (c.get("isLocked") and not c.get("chapterPurchased")):
                continue  # (still sold for the site's coins)
            n = c.get("number")
            title = (c.get("title") or "").strip()
            name = (f"Chapter {n:g}" if isinstance(n, (int, float)) else c["slug"].replace("-", " ").title())
            folder = (c.get("mangaPost") or {}).get("slug") or post.get("slug") or slug
            chapters.append({"url": f"{BASE}/series/{folder}/{c['slug']}", "name": f"{name} - {title}" if title else name,
                             "number": float(n) if isinstance(n, (int, float)) else number(c["slug"]),
                             "date": (c.get("createdAt") or "")[:10]})
        skip += take
        if len(got) < take or skip >= (data.get("totalChapterCount") or 0):
            break
    return {"url": f"{BASE}/series/{post.get('slug') or slug}", "title": (post.get("postTitle") or slug).strip(),
            "cover": post.get("featuredImage") or "", "summary": _plain(post.get("postContent")),
            "kind": KINDS.get(str(post.get("seriesType") or "").upper(), ""),
            "chapters": list(reversed(chapters))}  # (newest first in the API)


def pages(chapter_url):
    doc = parse(_get(chapter_url).text)
    imgs = doc.select("img[data-reader-page-image]")
    if imgs:  # (in reading order by their index)
        imgs.sort(key=lambda img: int(img.get("data-reader-index", "0")) if img.get("data-reader-index", "0").isdigit() else 0)
    else:  # (older versions of the theme)
        imgs = [img for img in doc.select("section[itemprop=articleBody] figure img, .image-container img[data-image-index]")
                if not img.get("itemprop")]
    found = []
    for img in imgs:
        src = picture(img, chapter_url)
        if src and src not in found:
            found.append(src)
    if not found:
        if "Cost to Unlock" in doc.text or doc.one("a[href*='/auth/signin']") is not None and "unlock" in doc.text.lower():
            raise RuntimeError(f"{NAME} still sells this chapter for its coins: it can be got here once the site makes it free")
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
