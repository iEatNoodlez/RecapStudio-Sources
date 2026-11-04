# engine: Madara
# version: 1.0.0
# title: Madara (a WordPress theme many comic sites are built on)
# lists: [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}]
#
# The site's own search, its Popular and Latest lists, a series' chapters (on its page, or from the theme's chapter
# list request when the page loads them later) and a chapter's pictures (?style=list: all of them on one page).
# OPTIONS (sites.json): "path", the folder its series are in ("manga" unless the site renamed it); "kind", what
# kind of comic it has ("webtoon" for manhwa sites); "cookies", ones it needs (an adult site's "show adult" one).
import re
import time
from urllib.parse import quote_plus

import requests

# @include dom

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})
_session.cookies.update(OPTIONS.get("cookies", {}))
PATH = OPTIONS.get("path", "manga")
ORDER = {"popular": "views", "latest": "latest"}


def _get(url, method="GET", **kw):
    for attempt in range(2):
        r = _session.request(method, url, timeout=45, **kw)
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


def _cards(doc):
    """Search and list results: [{"url", "title", "cover"}]."""
    out, seen = [], set()
    for card in doc.select(".c-tabs-item__content, .page-item-detail, .manga-item, .page-listing-item .item-thumb"):
        link = card.one(".post-title a, .item-summary a, h3 a, h4 a") or card.one("a[href]")
        url = absolute(BASE, link.get("href")) if link else ""
        if not url or url in seen or f"/{PATH}/" not in url:
            continue
        seen.add(url)
        out.append({"url": url, "title": link.text or link.get("title"), "cover": picture(card.one("img"), BASE),
                    "kind": OPTIONS.get("kind", "")})
    return out


def _more(doc):
    return doc.one("a.nextpostslink, .nav-previous a, a.next.page-numbers, a.next, link[rel=next]") is not None


def search(query, page=1):
    folder = f"/page/{page}" if page > 1 else ""
    doc = parse(_get(f"{BASE}{folder}/?s={quote_plus(query)}&post_type=wp-manga").text)
    items = _cards(doc)
    return {"items": items, "more": bool(items) and _more(doc)}


def browse(list_id, page=1):
    folder = f"/page/{page}" if page > 1 else ""
    doc = parse(_get(f"{BASE}/{PATH}{folder}/?m_orderby={ORDER[list_id]}").text)
    items = _cards(doc)
    return {"items": items, "more": bool(items) and _more(doc)}


def _chapters(doc, url):
    rows = doc.select("li.wp-manga-chapter")
    if not rows:  # (loaded after the page: the theme's own request for them)
        ajax = {"X-Requested-With": "XMLHttpRequest", "Referer": url}
        try:
            rows = parse(_get(url.rstrip("/") + "/ajax/chapters/", "POST", headers=ajax).text).select("li.wp-manga-chapter")
        except (ValueError, requests.HTTPError):
            rows = []
        holder = doc.one("#manga-chapters-holder, [data-id]")
        if not rows and holder is not None and holder.get("data-id"):
            rows = parse(_get(f"{BASE}/wp-admin/admin-ajax.php", "POST", headers=ajax,
                              data={"action": "manga_get_chapters", "manga": holder.get("data-id")}).text
                         ).select("li.wp-manga-chapter")
    out = []
    for row in rows:
        link = row.one("a[href]")
        if link is None:
            continue
        name = link.own_text or link.text
        date = row.one(".chapter-release-date i, .chapter-release-date, .post-on")
        out.append({"url": absolute(BASE, link.get("href")), "name": name, "number": number(name),
                    "date": date.text if date is not None else ""})
    return list(reversed(out))  # (newest first on the page)


def series(url):
    doc = parse(_get(url).text)
    title = doc.one(".post-title h1, .post-title h3, #manga-title h1, h1")
    summary = doc.one(".description-summary .summary__content, .summary__content, .manga-excerpt, .manga-summary")
    return {"url": url, "title": (title.own_text or title.text) if title is not None else "Untitled",
            "cover": picture(doc.one(".summary_image img, .tab-summary img"), BASE),
            "summary": summary.text if summary is not None else "", "kind": OPTIONS.get("kind", ""),
            "chapters": _chapters(doc, url)}


def pages(chapter_url):
    url = chapter_url + ("&" if "?" in chapter_url else "?") + "style=list"
    doc = parse(_get(url).text)
    found = []
    for img in doc.select(".reading-content img, .page-break img, .read-container img"):
        src = picture(img, chapter_url)
        if src and src not in found:
            found.append(src)
    if not found:
        if doc.one("#chapter-protector-data, [id*=chapter-protector], .chapter-warning"):
            raise RuntimeError(f"{NAME} hides this chapter's pictures behind its own lock, which this plugin can't open")
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
