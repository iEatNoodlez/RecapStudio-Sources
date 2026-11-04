"""Rizz Fables: chapters from rizzfables.com, a site of its own that copies MangaThemesia's pages (not WordPress).

A Recap Studio source plugin from RecapStudio-Sources. Its search and lists are the site's own requests (JSON, every
result at once); a series' page and a chapter's pictures are read like MangaThemesia's (engines/themesia.py). The site
puts a code in front of every series' and chapter's link (/series/r2311170-<title>) and sends a wrong one to an ad:
the code is read from its home page, and read again when it changes.
"""
RECAP_PLUGIN = 1
NAME = "Rizz Fables"
VERSION = "1.0.0"
SITES = ["rizzfables.com"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}]
HEADERS = {"Referer": "https://rizzfables.com/"}
DELAY = 0
TEST_QUERY = "sect leader"

import json
import re
import time
from urllib.parse import urlparse

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

BASE = "https://rizzfables.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})
ORDER = {"popular": "popular", "latest": "update"}
KINDS = {"manhwa": "webtoon", "manhua": "webtoon", "webtoon": "webtoon", "manga": "manga", "comic": "comic"}
SKIP = re.compile(r"ramrvsrc\d+\.webp$|66c47bc2c15dd\.webp$")  # (the site's own banners between pictures)
_code = []


class _Moved(Exception):
    """The site sent a link to an ad: its links' code has changed."""


def _get(url, method="GET", **kw):
    asked, waited = url, False
    for hop in range(6):  # (its own redirects are followed here; one to another site is an ad, not followed)
        r = _session.request(method, url, timeout=45, allow_redirects=False, **kw)
        if r.status_code == 429 and not waited:
            waited = True
            time.sleep(min(30, int(r.headers.get("Retry-After") or 10)))
            continue
        if r.status_code == 429:
            raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")
        if r.status_code in (403, 503) and ("cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        if r.status_code in (301, 302, 303, 307, 308):
            url = absolute(url, r.headers.get("Location"))
            if urlparse(url).hostname not in ("rizzfables.com", "www.rizzfables.com"):
                raise _Moved(asked)
            continue
        if r.status_code == 404:
            raise ValueError(f"{NAME} has no such page: {url}")
        r.raise_for_status()
        return r
    raise RuntimeError(f"{NAME} keeps sending this page somewhere else: {asked}")


def _prefix(again=False):
    """The code in front of every link ("r2311170-"), from the links on the home page."""
    if again or not _code:
        m = re.search(r"/series/(r\d+-)", _get(BASE + "/").text)
        if not m:
            raise RuntimeError(f"{NAME} changed its pages: its series' links weren't found on its home page")
        _code[:] = [m.group(1)]
    return _code[0]


def _slug(title):
    """A series' link name from its title, as the site's own links have it ("Hunter's Journey!" → hunters-journey)."""
    return re.sub(r"[^a-z0-9]+", "-", re.sub(r"['’]", "", title.lower())).strip("-")


def _item(x):
    year = number(str(x.get("released") or ""))
    return {"url": f"{BASE}/series/{_prefix()}{_slug(x['title'])}", "title": x["title"],
            "cover": f"{BASE}/assets/images/{x['image_url']}" if x.get("image_url") else "",
            "summary": x.get("long_description") or "", "kind": KINDS.get(str(x.get("type") or "").lower(), ""),
            "year": int(year) if year else None}


def _post(path, data):
    r = _get(BASE + path, "POST", data=data, headers={"X-Requested-With": "XMLHttpRequest"})
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"{NAME} didn't answer as expected (its site may have changed)")


def search(query, page=1):
    if page > 1:
        return {"items": [], "more": False}  # (the site gives every result at once)
    found = _post("/Index/live_search", {"search_value": query})
    return {"items": [_item(x) for x in found or [] if x.get("title")], "more": False}


def browse(list_id, page=1):
    if page > 1:
        return {"items": [], "more": False}
    found = _post("/Index/filter_series", {"StatusValue": "all", "TypeValue": "all", "OrderValue": ORDER[list_id]})
    return {"items": [_item(x) for x in found or [] if x.get("title")], "more": False}


def _open(url):
    """A series' or chapter's page; a link with an old code is tried again with the current one."""
    try:
        return _get(url).text
    except _Moved:
        fixed = re.sub(r"/(series|chapter)/r\d+-", lambda m: f"/{m.group(1)}/{_prefix(again=True)}", url)
        try:
            return _get(fixed).text
        except _Moved:
            raise ValueError(f"{NAME} has no such page: {url}")


def _info(doc, label):
    for row in doc.select(".imptdt"):
        text = row.text  # ("Type manhua", "Released 2021")
        if text.lower().startswith(label.lower()):
            return text[len(label):].strip(" :")
    return ""


def series(url):
    text = _open(url)
    doc = parse(text)
    title = doc.one("h1.entry-title")
    # (the summary is in a script that writes it into the page)
    m = re.search(r'var description\s*=\s*("(?:[^"\\]|\\.)*")', text)
    try:
        summary = json.loads(m.group(1)) if m else ""
    except ValueError:
        summary = ""
    if not summary:
        box = doc.one(".entry-content[itemprop=description]")
        summary = box.text if box is not None else ""
    chapters = []
    for row in doc.select("#chapterlist li"):
        link = row.one("a[href]")
        if link is None or not link.get("href").startswith(("http", "/")):
            continue
        label = row.one(".chapternum")
        name = label.text if label is not None else link.text
        date = row.one(".chapterdate")
        num = number(name)
        if num is None and row.get("data-num"):
            num = number(row.get("data-num"))
        chapters.append({"url": absolute(BASE, link.get("href")), "name": name, "number": num,
                         "date": date.text if date is not None else ""})
    chapters.reverse()  # (newest first on the page)
    year = number(_info(doc, "Released"))
    return {"url": url, "title": title.text if title is not None else "Untitled",
            "cover": picture(doc.one(".thumb img"), BASE), "summary": summary,
            "kind": KINDS.get(_info(doc, "Type").lower(), ""), "year": int(year) if year else None,
            "chapters": chapters}


def pages(chapter_url):
    doc = parse(_open(chapter_url))
    found = []
    for img in doc.select("#readerarea img"):
        src = picture(img, chapter_url)
        if src and not SKIP.search(src) and src not in found:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
