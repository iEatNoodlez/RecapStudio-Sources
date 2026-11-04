# engine: MangaThemesia
# version: 1.0.0
# title: MangaThemesia (Themesia's MangaReader, a WordPress theme many comic sites are built on)
# lists: [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}]
#
# The site's own search, its Popular and Latest lists, a series' chapters (all of them on its page) and a chapter's
# pictures (the theme's reader script, ts_reader.run({...}), lists them; the page's <img>s when it doesn't).
# OPTIONS (sites.json): "path", the folder its series are in ("manga" unless the site renamed it, like "series");
# "kind", what kind of comic it has when its pages don't say; "cookies", ones it needs.
import base64
import json
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
ORDER = {"popular": "popular", "latest": "update"}
KINDS = {"manhwa": "webtoon", "manhua": "webtoon", "webtoon": "webtoon", "manga": "manga", "comic": "comic"}


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


def _kind(text):
    """"Manhwa", "type Manhua", "MANGA"... as the app's kind ("webtoon", "manga", "comic"), else the site's option."""
    for word in re.findall(r"[a-z]+", (text or "").lower()):
        if word in KINDS:
            return KINDS[word]
    return OPTIONS.get("kind", "")


def _cards(doc):
    """Search and list results: [{"url", "title", "cover", "kind"}]."""
    out, seen = [], set()
    for card in doc.select(".listupd .bsx, .bs .bsx"):
        link = card.one("a[href]")
        url = absolute(BASE, link.get("href")) if link else ""
        if not url or url in seen or f"/{PATH}/" not in url:
            continue
        seen.add(url)
        title = card.one(".tt")
        badge = card.one(".type, .colored")
        out.append({"url": url, "title": link.get("title") or (title.text if title is not None else ""),
                    "cover": picture(card.one("img"), BASE),
                    "kind": _kind(" ".join(badge.classes) + " " + badge.text) if badge is not None else _kind("")})
    return out


def _more(doc):
    return doc.one("a.next.page-numbers, .hpage a.r, a.nextpostslink, link[rel=next]") is not None


def search(query, page=1):
    folder = f"/page/{page}" if page > 1 else ""
    doc = parse(_get(f"{BASE}{folder}/?s={quote_plus(query)}").text)
    items = _cards(doc)
    return {"items": items, "more": bool(items) and _more(doc)}


def browse(list_id, page=1):
    doc = parse(_get(f"{BASE}/{PATH}/?order={ORDER[list_id]}" + (f"&page={page}" if page > 1 else "")).text)
    items = _cards(doc)
    return {"items": items, "more": bool(items) and _more(doc)}


def _info(doc, label):
    """A value from the series' details box ("Type", "Released"...), in either of the theme's two layouts."""
    for row in doc.select(".imptdt, .fmed, .infotable tr"):
        text = row.text  # ("Type Manhwa", "Released 2021")
        if text.lower().startswith(label.lower()):
            return text[len(label):].strip(" :")
    return ""


def _chapters(doc):
    out = []
    for row in doc.select("#chapterlist li"):
        link = row.one("a[href]")
        href = link.get("href") if link is not None else ""
        if not href or href.startswith(("#", "javascript")):
            continue  # (a locked chapter, bought with the site's coins, has no link)
        label = row.one(".chapternum")
        name = label.text if label is not None else link.text
        date = row.one(".chapterdate")
        num = number(name)
        if num is None and row.get("data-num"):
            num = number(row.get("data-num"))
        out.append({"url": absolute(BASE, href), "name": name, "number": num,
                    "date": date.text if date is not None else ""})
    return list(reversed(out))  # (newest first on the page)


def _summary(node):
    if node is None:
        return ""
    first = node.one("p") or node
    kids = first.children
    # (some sites start it with a line for search engines, "Read manga <title> / <other titles>", then a <br>)
    if kids and isinstance(kids[0], str) and kids[0].strip().startswith("Read "):
        cut = next((i for i, k in enumerate(kids) if getattr(k, "tag", "") == "br"), None)
        if cut is not None:
            del kids[:cut + 1]
    return node.text


def series(url):
    doc = parse(_get(url).text)
    title = doc.one("h1.entry-title, .seriestuhead h1, h1")
    year = number(_info(doc, "Released"))
    return {"url": url, "title": title.text if title is not None else "Untitled",
            "cover": picture(doc.one(".thumb img, .seriestucontl img, .wp-post-image"), BASE),
            "summary": _summary(doc.one(".entry-content[itemprop=description], .entry-content")),
            "kind": _kind(_info(doc, "Type")), "year": int(year) if year else None, "chapters": _chapters(doc)}


def _reader(text):
    """The pictures the theme's reader script lists: ts_reader.run({..., "sources": [{"images": [...]}]})."""
    scripts = [text]
    # (some sites ship their scripts base64-encoded, as data: links)
    for data in re.findall(r"data:text/javascript;base64,([A-Za-z0-9+/=]+)", text):
        try:
            scripts.append(base64.b64decode(data).decode("utf-8", "replace"))
        except ValueError:
            pass
    for script in scripts:
        at = script.find("ts_reader.run(")
        if at < 0:
            continue
        try:
            params, _ = json.JSONDecoder().raw_decode(script, at + len("ts_reader.run("))
        except ValueError:
            continue
        for source in params.get("sources") or []:
            if source.get("images"):
                return [absolute(BASE, x) for x in source["images"] if x]
    return []


def pages(chapter_url):
    text = _get(chapter_url).text
    found = []
    for src in _reader(text) or [picture(img, chapter_url) for img in
                                 parse(text).select("#readerarea img, #readarea img, .reader-area img")]:
        if src and src not in found:
            found.append(src)
    if not found:
        if re.search(r"lockedChapterModal|chapter is locked|unlock this chapter", text, re.I):
            raise RuntimeError(f"{NAME} keeps this chapter locked (it's bought with the site's coins)")
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
