"""Hentai Webtoon: chapters from hentaiwebtoon.com, a small adult manhwa site with a layout of its own.

A Recap Studio source plugin from RecapStudio-Sources. The site has a dozen series, all on its /manga/ page; its
search only filters those cards in the browser, so search() does the same with the words typed. A series' chapters
are all on its page (/manga/<slug>/, newest first) and a chapter's pictures are plain <img>s on its page
(/manga/<slug>/chapter-<n>/), served by the site itself.
"""
RECAP_PLUGIN = 1
NAME = "Hentai Webtoon"
VERSION = "1.0.0"
SITES = ["hentaiwebtoon.com"]
LANGUAGE = "en"
NSFW = True
LISTS = [{"id": "all", "name": "All series"}, {"id": "manhwa", "name": "Manhwa"}]
HEADERS = {"Referer": "https://hentaiwebtoon.com/"}
DELAY = 0
TEST_QUERY = "circles"

import time
from urllib.parse import urlsplit

import requests

# ---- dom: a page's HTML as a tree, searched with CSS selectors (the standard library only: plugins have no other
# parser). parse(text) gives the document; node.select("css") its matches, node.one("css") the first, node.text the
# text inside, node.get("attr") an attribute. Selectors: tag, *, .class, #id, [attr], [attr=v], [attr^=v], [attr$=v],
# [attr*=v], [attr~=v], combined with descendant (space) and child (>) combinators, and groups (a, b).
import html as _html
import re as _re
from html.parser import HTMLParser as _HTMLParser
from urllib.parse import urljoin as _urljoin

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_HIDDEN = {"script", "style", "noscript", "template"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag, self.attrs, self.children, self.parent = tag, attrs or {}, [], parent

    def get(self, name, default=""):
        value = self.attrs.get(name)
        return value if value not in (None, "") else default

    @property
    def classes(self):
        return set(self.attrs.get("class", "").split())

    def elements(self):
        """Every element inside, in document order."""
        stack = [c for c in reversed(self.children) if isinstance(c, Node)]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(c for c in reversed(node.children) if isinstance(c, Node))

    @property
    def text(self):
        """The text inside, with runs of white space as one space (scripts and styles left out)."""
        out, stack = [], list(reversed(self.children))
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                out.append(item)
            elif item.tag not in _HIDDEN:
                if item.tag in ("br", "p", "div", "li"):
                    out.append(" ")
                stack.extend(reversed(item.children))
        return _re.sub(r"\s+", " ", "".join(out)).strip()

    @property
    def own_text(self):
        """Its own text, without that of the elements inside it (a heading without its badges)."""
        return _re.sub(r"\s+", " ", "".join(c for c in self.children if isinstance(c, str))).strip()

    def select(self, css):
        chains = _selector(css)
        return [n for n in self.elements() if any(_chain(n, chain, len(chain) - 1) for chain in chains)]

    def one(self, css):
        chains = _selector(css)
        return next((n for n in self.elements() if any(_chain(n, chain, len(chain) - 1) for chain in chains)), None)

    def __repr__(self):
        return f"<{self.tag} {self.attrs}>"


class _Builder(_HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.cur = Node("#document")

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: v or "" for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, {k: v or "" for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        node = self.cur
        while node is not self.root and node.tag != tag:
            node = node.parent
        if node is not self.root:  # (an end tag nothing opened is passed over)
            self.cur = node.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def parse(text):
    builder = _Builder()
    builder.feed(text or "")
    builder.close()
    return builder.root


_SIMPLE = _re.compile(r"""([a-zA-Z][\w-]*|\*)|\.([\w-]+)|\#([\w-]+)|\[\s*([\w:-]+)\s*(?:([~^$*]?=)\s*("[^"]*"|'[^']*'|[^\]\s]+)\s*)?\]""")
_cache = {}


def _compound(text):
    tests, pos = [], 0
    for m in _SIMPLE.finditer(text):
        if m.start() != pos:
            break
        pos = m.end()
        tag, cls, ident, attr, op, value = m.groups()
        if tag:
            tests.append(("tag", tag.lower()))
        elif cls:
            tests.append(("class", cls))
        elif ident:
            tests.append(("attr", "id", "=", ident))
        else:
            tests.append(("attr", attr.lower(), op, (value or "").strip("\"'")))
    if pos != len(text) or not tests:
        raise ValueError(f"A selector this parser can't read: {text!r}")
    return tests


def _selector(css):
    if css not in _cache:
        chains = []
        for group in css.split(","):
            tokens = _re.sub(r"\s*>\s*", " > ", group.strip()).split()
            chain, comb = [], " "
            for token in tokens:
                if token == ">":
                    comb = ">"
                    continue
                chain.append((comb, _compound(token)))
                comb = " "
            if chain:
                chains.append(chain)
        _cache[css] = chains
    return _cache[css]


def _matches(node, tests):
    if node.tag.startswith("#"):
        return False
    for test in tests:
        if test[0] == "tag":
            if test[1] != "*" and node.tag != test[1]:
                return False
        elif test[0] == "class":
            if test[1] not in node.classes:
                return False
        else:
            _, name, op, want = test
            if name not in node.attrs:
                return False
            have = node.attrs[name] or ""
            if op and not (op == "=" and have == want or op == "^=" and want and have.startswith(want)
                           or op == "$=" and want and have.endswith(want) or op == "*=" and want and want in have
                           or op == "~=" and want in have.split()):
                return False
    return True


def _chain(node, chain, i):
    comb, tests = chain[i]
    if not _matches(node, tests):
        return False
    if i == 0:
        return True
    if comb == ">":
        return node.parent is not None and _chain(node.parent, chain, i - 1)
    up = node.parent
    while up is not None:
        if _chain(up, chain, i - 1):
            return True
        up = up.parent
    return False


def absolute(base, url):
    """A link as a full one, with the spaces and line breaks some sites leave around it taken out."""
    url = _html.unescape((url or "").strip())
    return _urljoin(base, url) if url else ""


def picture(img, base=""):
    """An <img>'s picture: its lazy-loading attributes first (the src is often a placeholder), then srcset's first."""
    if img is None:
        return ""
    for name in ("data-src", "data-lazy-src", "lazy-src", "data-original", "data-cfsrc", "data-url", "src"):
        value = img.get(name).strip()
        if value and not value.startswith("data:"):
            return absolute(base, value)
    srcset = (img.get("data-srcset") or img.get("srcset")).strip()
    return absolute(base, srcset.split(",")[0].split()[0]) if srcset else ""


def number(name):
    """A chapter's number from its name ("Chapter 12.5 - The End" → 12.5), or None."""
    m = (_re.search(r"(?:chapter|chap|ch|episode|ep|#)\.?\s*(\d+(?:[.,]\d+)?)", name or "", _re.I)
         or _re.search(r"(\d+(?:\.\d+)?)", name or ""))
    return float(m.group(1).replace(",", ".")) if m else None
# ---- end of dom

BASE = "https://hentaiwebtoon.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
PATHS = {"all": "/manga/", "manhwa": "/manga-genre/manhwa/"}
KINDS = {"manga": "manga", "manhwa": "webtoon", "manhua": "webtoon", "webtoon": "webtoon"}
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})


def _get(url, **kw):
    for attempt in range(2):
        r = _session.get(url, timeout=45, **kw)
        if r.status_code == 429 and attempt == 0:
            wait = r.headers.get("Retry-After", "")
            time.sleep(min(30, int(wait) if wait.isdigit() else 10))
            continue
        if r.status_code in (403, 503) and ("cf_chl" in r.text or "cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        if r.status_code == 404:
            raise ValueError(f"{NAME} has no such page: {url}")
        r.raise_for_status()
        if "charset" not in r.headers.get("content-type", "").lower():
            r.encoding = "utf-8"  # (the pages say so in a <meta>, but the server doesn't)
        return r
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _cards(path):
    """A page's series cards: [{"url", "title", "cover", "terms"}] (terms: what the site's own search matches)."""
    items, seen = [], set()
    for card in parse(_get(BASE + path).text).select("article.webtoon-card"):
        link = card.one("h2 a") or card.one("a[href*='/manga/']")
        if link is None:
            continue
        url = absolute(BASE, link.get("href"))
        if url in seen:
            continue
        seen.add(url)
        title = link.text or link.get("aria-label").removeprefix("Open ")
        items.append({"url": url, "title": title, "cover": picture(card.one("img"), BASE), "kind": "webtoon",
                      "terms": " ".join((card.get("data-title-terms") + " " + title).lower().split())})
    return items


def search(query, page=1):
    words = query.lower().split()
    items = [item for item in _cards("/manga/") if all(word in item["terms"] for word in words)] if page == 1 else []
    return {"items": [{k: v for k, v in item.items() if k != "terms"} for item in items], "more": False}


def browse(list_id, page=1):
    if list_id not in PATHS:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    items = _cards(PATHS[list_id]) if page == 1 else []  # (one page: the site has no more)
    return {"items": [{k: v for k, v in item.items() if k != "terms"} for item in items], "more": False}


def _detail(doc, label):
    """A row of the series' details ("Type", "Status"...), or ""."""
    for dt in doc.select("dl dt"):
        if dt.text.strip().lower() == label.lower() and dt.parent is not None:
            dd = dt.parent.one("dd")
            return dd.text if dd is not None else ""
    return ""


def series(url):
    path = urlsplit(url.strip()).path
    if not path.startswith("/manga/") or len([p for p in path.split("/") if p]) != 2:
        raise ValueError(f"That isn't a series' link on {NAME}: {url}")
    url = BASE + path.rstrip("/") + "/"
    doc = parse(_get(url).text)
    title = doc.one("h1")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    cover = doc.one("meta[property='og:image']")
    summary = doc.one(".series-synopsis p")
    rows = []
    for li in doc.select("#chapter-rows li, ol.chapter-rows li"):
        link = li.one("a[href]")
        if link is None:
            continue
        name = link.text or f"Chapter {li.get('data-episode-number')}"
        sequence = li.get("data-sequence") or li.get("data-episode-number")
        try:
            order = float(sequence)
        except ValueError:
            order = number(name) or 0.0
        rows.append((order, {"url": absolute(BASE, link.get("href")), "name": name,
                             "number": number(li.get("data-episode-number")) or number(name), "date": ""}))
    rows.sort(key=lambda row: row[0])  # (newest first on the page; the site's own script sorts them too)
    chapters, seen = [], set()
    for _, chapter in rows:
        if chapter["url"] not in seen:
            seen.add(chapter["url"])
            chapters.append(chapter)
    return {"url": url, "title": title.text,
            "cover": absolute(BASE, cover.get("content")) if cover is not None else picture(doc.one("img.series-poster-image"), BASE),
            "summary": summary.text if summary is not None else "",
            "kind": KINDS.get(_detail(doc, "Type").lower(), ""), "chapters": chapters}


def pages(chapter_url):
    doc = parse(_get(chapter_url).text)
    found = []
    for img in doc.select(".scroll-pages img") or doc.select("[data-scroll-reader] img"):
        src = picture(img, chapter_url)
        if src and src not in found:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
