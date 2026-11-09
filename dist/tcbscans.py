"""TCB Scans: chapters from tcbonepiecechapters.com (One Piece, Chainsaw Man, Jujutsu Kaisen and the group's other
Shonen Jump series).

A small site with no search of its own: /projects lists every series it works on, with its cover, and search here
filters that list by the words typed. A series' page lists its chapters (newest first), each chapter's page has all
of its pictures (img.fixed-ratio-content, on the group's own picture host). The site is slow at times and answers
502 now and then, so a request is tried twice. Links on the group's older domains (tcbscans.com, tcbscans.me) open
on the current one: the paths are the same.
"""
import re
import time
from urllib.parse import urlsplit

import requests

RECAP_PLUGIN = 1
NAME = "TCB Scans"
VERSION = "1.0.0"
SITES = ["tcbonepiecechapters.com", "tcbscans.com", "tcbscans.me", "onepiecechapters.com"]
LANGUAGE = "en"
NSFW = False
LISTS = [{"id": "projects", "name": "Projects"}]
HEADERS = {"Referer": "https://tcbonepiecechapters.com/"}
DELAY = 0
TEST_QUERY = "chainsaw"

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

BASE = "https://tcbonepiecechapters.com"
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/130.0.0.0 Safari/537.36")
_session = requests.Session()
_session.headers.update({"User-Agent": USER_AGENT, "Referer": BASE + "/"})


def _here(url):
    """A link on one of the group's domains, on BASE."""
    parts = urlsplit(url)
    return BASE + parts.path + (f"?{parts.query}" if parts.query else "")


def _get(url):
    for attempt in range(3):
        r = _session.get(url, timeout=60)
        if r.status_code == 429 and attempt == 0:
            time.sleep(min(30, int(r.headers.get("Retry-After") or 10)))
            continue
        if r.status_code in (403, 503) and ("cf-chl" in r.text or "Just a moment" in r.text):
            raise RuntimeError(f"{NAME} is behind Cloudflare's browser check right now, which a plain program can't pass")
        if r.status_code == 404:
            raise ValueError(f"{NAME} has no such page: {url}")
        if r.status_code >= 500 and attempt < 2:  # (the site answers 502 now and then)
            time.sleep(4)
            continue
        r.raise_for_status()
        return r
    raise RuntimeError(f"{NAME} is busy (too many requests): try again in a minute")


def _chapter_number(name, href):
    m = re.search(r"chapter[\s-]*(\d+(?:\.\d+)?)", name, re.I) or re.search(r"chapter-(\d+(?:\.\d+)?)", href, re.I)
    return float(m.group(1)) if m else None


_projects = None  # the series the group works on, read once a session


def _all_projects():
    global _projects
    if _projects is None:
        doc = parse(_get(f"{BASE}/projects").text)
        items, seen = [], set()
        for link in doc.select("a[href^='/mangas/']"):
            href = link.get("href")
            if not href or href in seen:
                continue
            title = link.text.strip()
            img = link.one("img")
            if img is not None and not title:
                title = (img.get("alt") or "").strip()
            card = items[-1] if items and items[-1]["url"] == absolute(BASE, href) else None
            if card is None:
                seen.add(href)
                items.append({"url": absolute(BASE, href), "title": title, "cover": picture(img, BASE) if img is not None else "",
                              "summary": "", "kind": "manga", "year": None})
            else:  # (the same series' second link: its cover came first, its title second)
                if not card["title"]:
                    card["title"] = title
                if not card["cover"] and img is not None:
                    card["cover"] = picture(img, BASE)
        # a series' cover link and its title link follow each other: join them
        joined, by_url = [], {}
        for it in items:
            if it["url"] in by_url:
                have = by_url[it["url"]]
                have["title"] = have["title"] or it["title"]
                have["cover"] = have["cover"] or it["cover"]
            else:
                by_url[it["url"]] = it
                joined.append(it)
        _projects = [it for it in joined if it["title"]]
    return _projects


def search(query, page=1):
    words = [w for w in query.lower().split() if w]
    items = [it for it in _all_projects() if all(w in it["title"].lower() for w in words)] if page == 1 else []
    return {"items": items, "more": False}


def browse(list_id, page=1):
    if list_id != "projects":
        raise ValueError(f"{NAME} has no list called {list_id}")
    return {"items": _all_projects() if page == 1 else [], "more": False}


def series(url):
    url = _here(url)
    doc = parse(_get(url).text)
    title = doc.one("h1")
    title = title.text.strip() if title is not None else ""
    cover = ""
    for img in doc.select("img"):
        if title and (img.get("alt") or "").strip() == title:
            cover = picture(img, BASE)
            break
    summary = doc.one("p.leading-6")
    chapters = []
    for link in doc.select("a[href^='/chapters/']"):
        href = link.get("href")
        name_node = link.one("div")
        name = (name_node.text if name_node is not None else link.text).strip()
        if not href or not name:
            continue
        chapters.append({"url": absolute(BASE, href), "name": name, "number": _chapter_number(name, href), "date": None})
    chapters.reverse()  # (newest first on the page)
    seen, kept = set(), []
    for c in chapters:
        if c["url"] not in seen:
            seen.add(c["url"])
            kept.append(c)
    return {"title": title, "cover": cover, "summary": summary.text.strip() if summary is not None else "",
            "kind": "manga", "chapters": kept}


def pages(chapter_url):
    doc = parse(_get(_here(chapter_url)).text)
    urls, seen = [], set()
    for img in doc.select("img.fixed-ratio-content"):
        src = picture(img, BASE)
        if src and src not in seen:
            seen.add(src)
            urls.append(src)
    if not urls:
        raise RuntimeError(f"{NAME} shows no pictures for this chapter: {chapter_url}")
    return urls
