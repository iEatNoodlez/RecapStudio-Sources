"""Hachiraw: chapters from hachiraw.win (Japanese raws; it calls itself adult and has adult categories).

Search is the site's /search?query= page, which shows its first 20 matches only (no page of it goes further, so the
search offers no more); the lists are its front page of latest updates (/page/<n>/) and two of its genre categories;
a series' chapters are all on its page, newest first, without dates; a chapter's pictures are lazy-loaded <img>s
(data-src) on its page, on a third-party host. The site tells browsers to send no Referer, so none is set for its
pictures.
"""
import re
import time
from urllib.parse import urlencode

import requests

RECAP_PLUGIN = 1
NAME = "Hachiraw"
VERSION = "1.0.0"
SITES = ["hachiraw.win"]
LANGUAGE = "ja"
NSFW = True
LISTS = [{"id": "latest", "name": "Latest updates"}, {"id": "action", "name": "Action"},
         {"id": "fantasy", "name": "Fantasy"}]
HEADERS = {}
DELAY = 0
TEST_QUERY = "one"

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

BASE = "https://hachiraw.win"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
CATEGORIES = {"action": "6", "fantasy": "12"}
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})


def _get(url):
    for attempt in range(2):
        r = _session.get(url, timeout=45)
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
    items, seen = [], set()
    for card in doc.select("article.post"):
        link = card.one(".entry-title a") or card.one("a[href*='/manga-']")
        if link is None:
            continue
        url = absolute(BASE, link.get("href"))
        if url in seen:
            continue
        seen.add(url)
        img = card.one(".featured-thumb img") or card.one("img")
        items.append({"url": url, "title": link.text or (img.get("alt") if img is not None else ""),
                      "cover": picture(img, BASE), "kind": "manga"})
    return items


def search(query, page=1):
    if page > 1:  # (the site's search has no second page: every page parameter gives the same first 20)
        return {"items": [], "more": False}
    items = _cards(parse(_get(f"{BASE}/search?{urlencode({'query': query.strip()})}").text))
    return {"items": items, "more": False}


def browse(list_id, page=1):
    if list_id == "latest":
        folder = ""
    elif list_id in CATEGORIES:
        folder = f"/category/{CATEGORIES[list_id]}"
    else:
        raise ValueError(f"{NAME} has no list {list_id!r}")
    doc = parse(_get(f"{BASE}{folder}/" + (f"page/{page}/" if page > 1 else "")).text)
    items = _cards(doc)
    return {"items": items, "more": bool(items) and doc.one(".nav-links a.next") is not None}


def _series_url(url):
    """The series' link (/manga-<slug>/), from its own or one of its chapters' (/chapter/<series>/<chapter>/)."""
    url = url.strip()
    m = re.match(r"https?://[^/]+/(manga-[^/?#]+)", url)
    if m:
        return f"{BASE}/{m.group(1)}/"
    if "/chapter/" in url:  # (a chapter's page links to its series)
        for a in parse(_get(url).text).select("a[href*='/manga-']"):
            m = re.match(r"(?:https?://[^/]+)?/(manga-[^/?#]+)", a.get("href"))
            if m:
                return f"{BASE}/{m.group(1)}/"
    raise ValueError(f"That isn't a series' link on {NAME}: {url}")


def series(url):
    url = _series_url(url)
    doc = parse(_get(url).text)
    title = doc.one("h1.entry-title")
    if title is None:
        raise ValueError(f"That page isn't a series on {NAME}: {url}")
    content = doc.one(".entry-content")
    summary = []
    if content is not None:  # (the author and category lines, then the summary's paragraphs, then the chapter table)
        for p in content.select("p"):
            text = p.text
            if text and not text.lower().startswith(("author:", "category:", "categor")) and p.one("a[href*='/chapter/']") is None:
                summary.append(text)

    chapters, seen = [], set()
    for a in doc.select(".chaplist a[href*='/chapter/']"):
        href = absolute(BASE, a.get("href"))
        if href in seen:
            continue
        seen.add(href)
        name = a.text or "Chapter"
        chapters.append({"url": href, "name": name, "number": number(name), "date": ""})
    chapters.reverse()  # (newest first on the page)
    return {"url": url, "title": title.text,
            "cover": picture(content.one(".wp-block-image img, img") if content is not None else None, BASE),
            "summary": " ".join(summary), "kind": "manga", "chapters": chapters}


def pages(chapter_url):
    doc = parse(_get(chapter_url.strip()).text)
    content = doc.one(".entry-content") or doc
    found = []
    for img in content.select(".wp-block-image img, img.aligncenter"):
        src = picture(img, BASE)
        if src and "/themes/" not in src and src not in found:
            found.append(src)
    if not found:
        raise RuntimeError(f"{NAME} showed no pictures for this chapter")
    return found
