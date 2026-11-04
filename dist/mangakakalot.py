"""MangaKakalot: chapters from kakamanga.com, a site built on the Manganato platform (MangaNato, MangaNelo, MangaKakalot).

A Recap Studio source plugin from RecapStudio-Sources, made by its tools/build.py from engines/manganato.py: change that, not this file."""
RECAP_PLUGIN = 1
NAME = 'MangaKakalot'
VERSION = '1.0.0'
SITES = ['kakamanga.com', 'mangakakalot.gg']
LANGUAGE = 'en'
NSFW = False
HEADERS = {'Referer': 'https://www.kakamanga.com/'}
LISTS = [{'id': 'popular', 'name': 'Popular'}, {'id': 'latest', 'name': 'Latest'}, {'id': 'new', 'name': 'New'}, {'id': 'completed', 'name': 'Completed'}]
DELAY = 0
BASE = 'https://www.kakamanga.com'
OPTIONS = {}

# engine: Manganato
# version: 1.0.0
# title: the Manganato platform (MangaNato, MangaNelo, MangaKakalot)
# lists: [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"}, {"id": "new", "name": "New"}, {"id": "completed", "name": "Completed"}]
#
# Search is the site's quick search (its JSON answer: up to 20 series, no further pages; the full search page is
# behind Cloudflare's browser check). Lists are its manga-list pages. A series' chapters come from the request its
# page makes for them (api/manga/<slug>/chapters), a chapter's pictures from the list in its page's script (the
# picture server plus each picture's path). Pictures need the site as Referer (HEADERS has it).
# A link on one of the site's old domains ("also" in sites.json) is opened on BASE: the paths stay the same.
# OPTIONS (sites.json): none so far.
import html
import json
import re
import time
import unicodedata
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

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})
XHR = {"X-Requested-With": "XMLHttpRequest"}
LIST_PATH = {"popular": "hot-manga", "latest": "latest-manga", "new": "new-manga", "completed": "completed-manga"}
WEBTOON = {"manhwa", "manhua", "webtoons", "long-strip"}  # (genres that mean a vertical comic)


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
        return r
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _json(url, **kw):
    r = _get(url, **kw)
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"{NAME} didn't answer with its usual data ({url})")


def _here(url):
    """A link on one of the site's old domains, on its current one (the paths haven't changed)."""
    parts = urlsplit(url.strip())
    return BASE + parts.path + (f"?{parts.query}" if parts.query else "")


def _alias(query):
    """The search word as the site's own search box writes it: lower case, accents dropped, spaces and punctuation
    as "_"."""
    text = unicodedata.normalize("NFKD", query.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[\s!@%^*()+=<>?/,.:;'\"&#\[\]~$_-]+", "_", text).strip("_")


def search(query, page=1):
    word = _alias(query)
    if page > 1 or not word:  # (the quick search has one page)
        return {"items": [], "more": False}
    found = _json(f"{BASE}/home/search/json", params={"searchword": word}, headers=XHR)
    items = []
    for x in found if isinstance(found, list) else []:
        url = x.get("url") or (f"{BASE}/manga/{x['slug']}" if x.get("slug") else "")
        title = html.unescape(re.sub(r"<[^>]+>", "", x.get("name") or ""))
        if url and title:
            items.append({"url": _here(url), "title": title, "cover": x.get("thumb") or ""})
    return {"items": items, "more": False}


def browse(list_id, page=1):
    doc = parse(_get(f"{BASE}/manga-list/{LIST_PATH[list_id]}", params={"page": page}).text)
    items, seen = [], set()
    for card in doc.select(".list-comic-item-wrap"):
        link = card.one("h3 a") or card.one("a.list-story-item")
        url = absolute(BASE, link.get("href")) if link is not None else ""
        if not url or url in seen:
            continue
        seen.add(url)
        items.append({"url": url, "title": link.text or link.get("title"), "cover": picture(card.one("img"), BASE)})
    last = doc.one("a.page_last")
    m = re.search(r"page=(\d+)", last.get("href")) if last is not None else None
    return {"items": items, "more": bool(items) and m is not None and page < int(m.group(1))}


def series(url):
    url = _here(url)
    doc = parse(_get(url).text)
    title = doc.one("ul.manga-info-text h1") or doc.one("h1")
    genres = {a.get("href").rstrip("/").rsplit("/", 1)[-1] for a in doc.select("li.genres a")}
    kind = "webtoon" if genres & WEBTOON else "manga" if "manga" in genres else ""
    box = doc.one("#contentBox")
    summary = (box.own_text or box.text) if box is not None else ""

    # The chapters: the page fills its list from the site's chapter request after it loads.
    holder = doc.one("#chapter-list-container")
    slug = (holder.get("data-comic-slug") if holder is not None else "") or urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    api = (holder.get("data-api-url") if holder is not None else "") or f"{BASE}/api/manga/__SLUG__/chapters"
    link = (holder.get("data-chapter-url-template") if holder is not None else "") or f"{BASE}/manga/__MANGA__/__CHAPTER__"
    found = _json(api.replace("__SLUG__", slug), params={"limit": -1}, headers={**XHR, "Referer": url})
    if not found.get("success", True):
        raise RuntimeError(f"{NAME} didn't give this series' chapters")
    chapters = []
    for row in reversed((found.get("data") or {}).get("chapters") or []):  # (newest first)
        if not row.get("chapter_slug"):
            continue
        name = (row.get("chapter_name") or "").strip()
        num = number(name)
        chapters.append({"url": link.replace("__MANGA__", slug).replace("__CHAPTER__", row["chapter_slug"]),
                         "name": name, "number": num if num is not None else row.get("chapter_num"),
                         "date": (row.get("updated_at") or "")[:10]})
    return {"url": url, "title": title.text if title is not None else "Untitled",
            "cover": picture(doc.one(".manga-info-pic img"), BASE), "summary": summary, "kind": kind,
            "chapters": chapters}


def _script_list(text, name):
    """A list the page's script sets (var cdns = [...]), or []."""
    m = re.search(rf"var\s+{name}\s*=\s*(\[.*?\])\s*;", text, re.S)
    try:
        return json.loads(m.group(1)) if m else []
    except ValueError:
        return []


def pages(chapter_url):
    url = _here(chapter_url)
    text = _get(url).text
    servers, files = _script_list(text, "cdns"), _script_list(text, "chapterImages")
    if servers and files:
        server = servers[0].strip().rstrip("/")
        found = [f"{server}/{f.strip().lstrip('/')}" for f in files if f.strip()]
    else:  # (an older page: the pictures are in it)
        found = [picture(img, url) for img in parse(text).select(".container-chapter-reader img")]
    found = list(dict.fromkeys(f for f in found if f))
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
