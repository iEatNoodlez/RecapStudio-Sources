"""ZinManga: chapters from zinmanga.net, a site that looks like Madara but loads its chapters from its own comics API.

A Recap Studio source plugin from RecapStudio-Sources. Search and the lists are the site's pages (Madara's layout),
a series' chapters come from the request its page makes for them (api/comics/<slug>/chapters, 100 at a time) and a
chapter's pictures are in its page. Pictures need the site as Referer (HEADERS has it).
"""
RECAP_PLUGIN = 1
NAME = "ZinManga"
VERSION = "1.0.0"
SITES = ["zinmanga.net"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "popular", "name": "Popular"}, {"id": "latest", "name": "Latest"},
         {"id": "trending", "name": "Trending"}]
HEADERS = {"Referer": "https://www.zinmanga.net/"}
DELAY = 0
TEST_QUERY = "solo"

import re
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

BASE = "https://www.zinmanga.net"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})
ORDER = {"popular": "views", "latest": "latest", "trending": "trending"}
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


def _cards(doc, card_css):
    """Search and list results: [{"url", "title", "cover"}]."""
    out, seen = [], set()
    for card in doc.select(card_css):
        link = card.one(".post-title a, h3 a")
        url = absolute(BASE, link.get("href")) if link is not None else ""
        if not url or url in seen or "/manga/" not in url:
            continue
        seen.add(url)
        out.append({"url": url, "title": link.text, "cover": picture(card.one("img"), BASE)})
    return out


def _more(doc):
    return doc.one("a[aria-label=Next], a.nextpostslink, link[rel=next]") is not None


def search(query, page=1):
    folder = f"/page/{page}" if page > 1 else "/"
    doc = parse(_get(BASE + folder, params={"s": query, "post_type": "wp-manga"}).text)
    items = _cards(doc, ".c-tabs-item__content")
    return {"items": items, "more": bool(items) and _more(doc)}


def browse(list_id, page=1):
    folder = f"/page/{page}" if page > 1 else ""
    doc = parse(_get(f"{BASE}/manga{folder}", params={"orderby": ORDER[list_id]}).text)
    items = _cards(doc, ".page-content-listing .page-item-detail")
    return {"items": items, "more": bool(items) and _more(doc)}


def _summary(doc):
    """The story's summary: the "Main Plot" part of the site's description (the rest is the site's own blurb)."""
    box = doc.one(".description-summary .summary__content, .summary__content")
    if box is None:
        return ""
    after = False
    for child in box.children:
        if isinstance(child, str):
            continue
        if child.tag == "h3" and "plot" in child.text.lower():
            after = True
        elif after and child.tag == "p" and child.text:
            return child.text
    return box.text


def series(url):
    url = BASE + urlsplit(url.strip()).path  # (any of the site's hosts: the paths are the same)
    page = _get(url).text
    doc = parse(page)
    title = doc.one(".post-title h1, h1")
    genres = {a.get("href").rstrip("/").rsplit("/", 1)[-1] for a in doc.select(".genres-content a")}
    kind = "webtoon" if genres & WEBTOON else "manga" if "manga" in genres else ""
    year = doc.one("a[href*=manga-release]")

    # The chapters: the page asks for them after it loads, oldest first here, 100 at a time.
    m = re.search(r"getChaptersUrl\s*=\s*['\"]([^'\"]+)", page)
    m_slug = re.search(r"comicSlug\s*=\s*['\"]([^'\"]+)", page)
    slug = m_slug.group(1) if m_slug else url.rstrip("/").rsplit("/", 1)[-1]
    api = m.group(1) if m else f"{BASE}/api/comics/{slug}/chapters"
    chapters, at = [], 1
    while at <= 100:  # (10,000 chapters at most)
        r = _get(api, params={"page": at, "per_page": 100, "order": "asc"},
                 headers={"X-Requested-With": "XMLHttpRequest", "Referer": url})
        try:
            data = r.json().get("data") or {}
        except ValueError:
            raise RuntimeError(f"{NAME} didn't answer with its usual chapter list")
        for row in data.get("chapters") or []:
            if not row.get("chapter_slug"):
                continue
            name = (row.get("chapter_name") or "").strip()
            num = number(name)
            chapters.append({"url": f"{BASE}/manga/{slug}/{row['chapter_slug']}", "name": name,
                             "number": num if num is not None else row.get("chapter_num"),
                             "date": (row.get("updated_at") or "")[:10]})
        if not data.get("chapters") or int(data.get("current_page") or at) >= int(data.get("last_page") or at):
            break
        at += 1
    return {"url": url, "title": title.text if title is not None else "Untitled",
            "cover": picture(doc.one(".summary_image img"), BASE), "summary": _summary(doc), "kind": kind,
            "year": year.text if year is not None else None, "chapters": chapters}


def pages(chapter_url):
    doc = parse(_get(chapter_url).text)
    found = []
    for img in doc.select(".reading-content img, .page-break img"):
        src = picture(img, chapter_url)
        if src and src not in found:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
