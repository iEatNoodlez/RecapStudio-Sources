"""LHTranslation: chapters from lhtranslation.net, a site built on Madara (a WordPress theme many comic sites are built on).

A Recap Studio source plugin from RecapStudio-Sources, made by its tools/build.py from engines/madara.py: change that, not this file."""
RECAP_PLUGIN = 1
NAME = 'LHTranslation'
VERSION = '1.0.0'
SITES = ['lhtranslation.net']
LANGUAGE = 'en'
NSFW = False
HEADERS = {'Referer': 'https://lhtranslation.net/'}
LISTS = [{'id': 'popular', 'name': 'Popular'}, {'id': 'latest', 'name': 'Latest'}]
DELAY = 0
BASE = 'https://lhtranslation.net'
OPTIONS = {}

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
