"""Rawkuma: chapters from rawkuma.net (Japanese raws).

A WordPress site on its own theme (not MangaThemesia: /?s= is a 404). Search and the lists are the Library page's
own request, a POST to admin-ajax.php?action=advanced_search with a nonce the site hands out at
admin-ajax.php?action=get_nonce (24 a page, with the site's popular, updated and rating orders); a series' chapters
are all on its page, newest first; a chapter's pictures are plain <img>s on its page.
"""
import re
import time

import requests

RECAP_PLUGIN = 1
NAME = "Rawkuma"
VERSION = "1.0.0"
SITES = ["rawkuma.net", "rawkuma.com"]
LANGUAGE = "ja"
NSFW = False
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest updates"},
         {"id": "rating", "name": "Top rated"}]
HEADERS = {"Referer": "https://rawkuma.net/"}
DELAY = 0
TEST_QUERY = "one piece"

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

BASE = "https://rawkuma.net"
AJAX = BASE + "/wp-admin/admin-ajax.php"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
PER_PAGE = 24
ORDER = {"popular": "popular", "latest": "updated", "rating": "rating"}
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})
_nonce = None


def _request(method, url, **kw):
    for attempt in range(2):
        r = _session.request(method, url, timeout=45, **kw)
        if r.status_code == 429 and attempt == 0:
            time.sleep(min(30, int(r.headers.get("Retry-After") or 10)))
            continue
        if r.status_code in (403, 503) and ("cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        if r.status_code == 404:
            raise ValueError(f"{NAME} has no such page: {url}")
        return r
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _get(url):
    r = _request("GET", url)
    r.raise_for_status()
    return r


def _get_nonce():
    """The search nonce the site gives every visitor (its Library page asks for it the same way)."""
    doc = parse(_request("GET", f"{AJAX}?type=search_form&action=get_nonce",
                         headers={"HX-Request": "true", "Referer": BASE + "/library/"}).text)
    field = doc.one("input[name=search_nonce]")
    if field is None or not field.get("value"):
        raise RuntimeError(f"{NAME} didn't hand out its search nonce: its search may have changed")
    return field.get("value")


def _library(query, orderby, page):
    """The Library's results for a search term and order: {"items", "more"}."""
    global _nonce
    for attempt in range(2):
        if _nonce is None:
            _nonce = _get_nonce()
        data = {"nonce": _nonce, "query": query, "orderby": orderby, "order": "desc", "page": page,
                "genre": "[]", "genre_exclude": "[]", "author": "[]", "artist": "[]", "type": "[]", "status": "[]",
                "inclusion": "OR", "exclusion": "OR", "project": "0"}
        r = _request("POST", f"{AJAX}?action=advanced_search", data=data, headers={"Referer": BASE + "/library/"})
        if r.status_code == 403 and attempt == 0:  # (the nonce ran out: get a new one)
            _nonce = None
            continue
        r.raise_for_status()
        break
    items, seen = [], set()
    for heading in parse(r.text).select("a[href*='/manga/'] > h1, a[href*='/manga/'] > h2"):
        link = heading.parent
        url = absolute(BASE, link.get("href"))
        if not url or url in seen:
            continue
        seen.add(url)
        card = link
        while card.parent is not None and card.one("img") is None:
            card = card.parent
        cover = next((img for img in card.select("img") if "/themes/" not in img.get("src")), None)
        items.append({"url": url, "title": heading.text or (cover.get("alt") if cover is not None else ""),
                      "cover": picture(cover, BASE), "kind": "manga"})
    return {"items": items, "more": len(items) >= PER_PAGE}


def search(query, page=1):
    return _library(query.strip(), "popular", page)


def browse(list_id, page=1):
    if list_id not in ORDER:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    return _library("", ORDER[list_id], page)


def _series_url(url):
    """The series' link (/manga/<slug>/) from its own or one of its chapters' (/manga/<slug>/chapter-<n>.<id>/)."""
    m = re.match(r"https?://[^/]+/manga/([^/?#]+)", url.strip())
    if not m:
        raise ValueError(f"That isn't a series' link on {NAME}: {url}")
    return f"{BASE}/manga/{m.group(1)}/"


def series(url):
    url = _series_url(url)
    doc = parse(_get(url).text)
    title = doc.one("h1[itemprop=name]")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    summary = next((n.text for n in doc.select("[itemprop=description]") if n.text), "")
    cover = doc.one("[itemprop=image]")
    cover = (cover.get("content") or cover.get("src") or picture(cover.one("img"), BASE)) if cover is not None else ""
    if not cover:
        cover = picture(doc.one("img.wp-post-image"), BASE)

    rows = doc.select("#chapter-list a[href*='/chapter-']") or doc.select(f"a[href*='{url}chapter-']")
    chapters, seen = [], set()
    for row in rows:
        href = absolute(BASE, row.get("href"))
        if not href or href in seen:
            continue
        seen.add(href)
        label = row.one(".font-medium span, span")
        name = (label.text if label is not None and label.text else row.text) or "Chapter"
        when = row.one("time")
        chapters.append({"url": href, "name": name, "number": number(name),
                         "date": (when.get("datetime") or when.text)[:10] if when is not None else ""})
    chapters.reverse()  # (newest first on the page)
    return {"url": url, "title": title.text, "cover": absolute(BASE, cover), "summary": summary, "kind": "manga",
            "chapters": chapters}


def pages(chapter_url):
    doc = parse(_get(chapter_url.strip()).text)
    found = []
    imgs = doc.select("section[data-image-data] img")
    if not imgs:  # (the reader's section lost its mark: every picture that isn't the site's own)
        imgs = [img for img in (doc.one("main") or doc).select("img")
                if not any(s in img.get("src") for s in ("/wp-content/", "/themes/", "gravatar"))]
    for img in imgs:
        src = picture(img, BASE)
        if src and src not in found:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
